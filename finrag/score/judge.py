"""
finrag.score.judge — two-tier LLM judge: normal (Ollama) + premium (Groq/Google).

get_judge()          — normal tier, every article×holding pair. Ollama only,
                        no cloud fallback: local, free, no daily quota to
                        babysit. Raises if Ollama isn't reachable.
get_premium_judge()  — premium tier, DISPUTE RESOLUTION ONLY. Groq → Google,
                        never Ollama. Called by score/pipeline.py exclusively
                        when the deterministic validator flags/rejects a
                        normal-tier score; its verdict overwrites the row and
                        is logged to CSV via score/dispute_log.py.

When a premium provider's *daily* quota is fully exhausted it raises
DailyLimitError, which bypasses tenacity retries and tells FallbackJudge to
advance to the next provider. Temporary per-minute 429s are still retried via
tenacity as before.

Every backend enforces STRUCTURED OUTPUT (JSON schema / json mode).
No SDK is hard-required: Groq and Google are plain HTTPS, Ollama is local HTTP.
"""
from __future__ import annotations

import json
import logging
import time as _time
from typing import Protocol

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

from finrag.config import settings

log = logging.getLogger(__name__)


class DailyLimitError(RuntimeError):
    """Provider's daily quota is fully exhausted — skip to next provider."""


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, DailyLimitError):
        return False  # don't retry; FallbackJudge will advance to next provider
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return isinstance(exc, (httpx.ReadError, httpx.ConnectError, httpx.TimeoutException))


SCORE_SCHEMA = {
    "type": "object",
    "properties": {
        "direct_relevance": {"type": "integer", "minimum": 0, "maximum": 10},
        "materiality":      {"type": "integer", "minimum": 0, "maximum": 10},
        "urgency":          {"type": "integer", "minimum": 0, "maximum": 10},
        "credibility":      {"type": "integer", "minimum": 0, "maximum": 10},
        "event_type":       {"type": "string"},
        "rationale":        {"type": "string"},
    },
    "required": ["direct_relevance", "materiality", "urgency", "credibility",
                 "event_type", "rationale"],
    "additionalProperties": False,
}


class JudgeError(RuntimeError):
    pass


class LLMJudge(Protocol):
    name: str
    def score(self, system: str, user: str) -> dict: ...


# --------------------------------------------------------------------------- #
# Groq — OpenAI-compatible endpoint, free tier, frontier 70B, sub-second.
# --------------------------------------------------------------------------- #
class GroqJudge:
    def __init__(self) -> None:
        if not settings.groq_api_key:
            raise JudgeError("GROQ_API_KEY not set")
        self.model = settings.groq_model
        self.name = f"groq/{self.model}"
        self.url = "https://api.groq.com/openai/v1/chat/completions"
        self.headers = {"Authorization": f"Bearer {settings.groq_api_key}"}

    @retry(retry=retry_if_exception(_is_retryable),
           wait=wait_exponential(multiplier=2, min=10, max=120),
           stop=stop_after_attempt(8),
           before_sleep=lambda rs: log.warning("Groq request failed, retrying in %ss…",
                                               round(rs.next_action.sleep)))
    def score(self, system: str, user: str) -> dict:
        body = {
            "model": self.model,
            "temperature": settings.llm_temperature,
            "max_tokens": settings.llm_max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
        }
        r = httpx.post(self.url, headers=self.headers, json=body,
                       timeout=settings.request_timeout * 4)
        if r.status_code == 429:
            body_text = r.text
            if "per day" in body_text or "TPD" in body_text:
                raise DailyLimitError(f"Groq daily token limit exhausted")
            wait = int(r.headers.get("retry-after", 10))
            log.warning("Groq 429 (RPM) — waiting %ss", wait)
            _time.sleep(wait)
            r.raise_for_status()
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        return _parse(content)


# --------------------------------------------------------------------------- #
# Google AI Studio — free tier, Gemini Flash, REST with responseSchema.
# --------------------------------------------------------------------------- #
class GoogleJudge:
    def __init__(self) -> None:
        if not settings.google_api_key:
            raise JudgeError("GOOGLE_API_KEY not set")
        self.model = settings.google_model
        self.name = f"google/{self.model}"
        self._base_url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent"
        )
        self._key = settings.google_api_key

    @retry(retry=retry_if_exception(_is_retryable),
           wait=wait_exponential(multiplier=2, min=10, max=120),
           stop=stop_after_attempt(8),
           before_sleep=lambda rs: log.warning("Google request failed, retrying in %ss…",
                                               round(rs.next_action.sleep)))
    def score(self, system: str, user: str) -> dict:
        schema = {
            "type": "object",
            "properties": {
                k: ({"type": "integer"} if v.get("type") == "integer" else {"type": "string"})
                for k, v in SCORE_SCHEMA["properties"].items()
            },
            "required": SCORE_SCHEMA["required"],
        }
        body = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "temperature": settings.llm_temperature,
                "responseMimeType": "application/json",
                "responseSchema": schema,
            },
        }
        r = httpx.post(self._base_url, params={"key": self._key},
                       json=body, timeout=settings.request_timeout * 4)
        if r.status_code == 429:
            body_text = r.text
            if "RESOURCE_EXHAUSTED" in body_text or (
                "quota" in body_text.lower() and "day" in body_text.lower()
            ):
                raise DailyLimitError("Google daily quota exhausted")
            wait = int(r.headers.get("retry-after", 15))
            log.warning("Google 429 (RPM) — waiting %ss", wait)
            _time.sleep(wait)
            r.raise_for_status()
        r.raise_for_status()
        content = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        return _parse(content)


# --------------------------------------------------------------------------- #
# Ollama — local, offline, private. No rate limits.
# --------------------------------------------------------------------------- #
class OllamaJudge:
    def __init__(self) -> None:
        self.model = settings.ollama_model
        self.name = f"ollama/{self.model}"
        self.url = f"{settings.ollama_host}/api/chat"

    def score(self, system: str, user: str) -> dict:
        body = {
            "model": self.model,
            "stream": False,
            "format": SCORE_SCHEMA,
            "options": {"temperature": settings.llm_temperature},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        r = httpx.post(self.url, json=body, timeout=settings.request_timeout * 8)
        r.raise_for_status()
        return _parse(r.json()["message"]["content"])


# --------------------------------------------------------------------------- #
# FallbackJudge — tries providers in order, advances on daily limit exhaustion.
# State (_idx) persists across all articles in one pipeline cycle so we don't
# re-hit an exhausted provider repeatedly within the same run.
# --------------------------------------------------------------------------- #
class FallbackJudge:
    def __init__(self, judges: list) -> None:
        self._judges = judges
        self._idx = 0

    @property
    def name(self) -> str:
        if self._idx < len(self._judges):
            return self._judges[self._idx].name
        return "fallback/exhausted"

    def score(self, system: str, user: str) -> dict:
        while self._idx < len(self._judges):
            judge = self._judges[self._idx]
            try:
                return judge.score(system, user)
            except DailyLimitError:
                log.warning("provider %s daily limit exhausted — switching to next", judge.name)
                self._idx += 1
        raise JudgeError("all LLM providers exhausted their daily limits")


def _ollama_reachable() -> bool:
    try:
        httpx.get(f"{settings.ollama_host}/api/tags", timeout=2.0)
        return True
    except Exception:
        return False


def _parse(content: str) -> dict:
    txt = content.strip()
    if txt.startswith("```"):
        txt = txt.split("```", 2)[1].removeprefix("json").strip()
    try:
        obj = json.loads(txt)
    except json.JSONDecodeError as e:
        raise JudgeError(f"judge returned non-JSON: {content[:200]}") from e
    for k in ("direct_relevance", "materiality", "urgency", "credibility"):
        v = obj.get(k)
        if not isinstance(v, int) or not 0 <= v <= 10:
            raise JudgeError(f"dimension {k!r} invalid: {v!r}")
    obj.setdefault("event_type", "unknown")
    obj.setdefault("rationale", "")
    return obj


def get_judge() -> LLMJudge:
    """The everyday scoring judge: Ollama only, no cloud fallback.

    Groq/Google are the PREMIUM tier — reserved for report writing and dispute
    resolution (see get_premium_judge()), never used silently here. If Ollama
    is unreachable this raises; the score stage fails loudly rather than
    quietly spending premium-tier calls on routine scoring."""
    if not _ollama_reachable():
        raise JudgeError("Ollama not reachable — no fallback configured (premium "
                          "tier is reserved for report writing / dispute resolution)")
    log.info("LLM provider (normal): ollama/%s", settings.ollama_model)
    return OllamaJudge()


def get_premium_judge() -> LLMJudge | None:
    """Premium tier: Groq → Google (skip unconfigured), never Ollama.

    Used only to resolve a DISPUTE — a score the deterministic validator
    flagged or rejected. Its verdict is final: pipeline.py overwrites the
    normal judge's dims/composite with this one's and logs the swap.
    Returns None (not raising) when neither provider is configured, so callers
    can leave the disputed row as-is rather than crashing the batch.
    """
    chain: list[LLMJudge] = []

    if settings.groq_api_key:
        try:
            chain.append(GroqJudge())
        except JudgeError as e:
            log.warning("Groq (premium) unavailable: %s", e)

    if settings.google_api_key:
        try:
            chain.append(GoogleJudge())
        except JudgeError as e:
            log.warning("Google (premium) unavailable: %s", e)

    if not chain:
        log.warning("no premium provider configured — disputed rows will stay flagged")
        return None

    if len(chain) == 1:
        log.info("premium provider: %s", chain[0].name)
        return chain[0]

    names = " → ".join(j.name for j in chain)
    log.info("premium fallback chain: %s", names)
    return FallbackJudge(chain)


def model_tag(judge: LLMJudge | None = None) -> str:
    """Stable identifier logged with every score row."""
    if judge is not None:
        return judge.name
    return f"ollama/{settings.ollama_model}"
