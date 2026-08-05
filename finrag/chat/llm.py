"""
finrag.chat.llm — freeform chat completion, mirroring score.judge's two tiers.

get_chat_llm()     — normal tier for interactive Q&A: Ollama only.
get_premium_chat() — premium tier, report-writing only: Groq -> Google, same
                     free-tier daily-limit fallback as score.judge.

Returns conversational text instead of a fixed JSON schema — the judge module
scores, this one talks.
"""
from __future__ import annotations

import logging
import time as _time
from typing import Protocol

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

from finrag.config import settings
from finrag.score.judge import DailyLimitError, JudgeError, _is_retryable

log = logging.getLogger(__name__)


class ChatLLM(Protocol):
    name: str
    def complete(self, messages: list[dict]) -> str: ...


class GroqChat:
    def __init__(self) -> None:
        if not settings.groq_api_key:
            raise JudgeError("GROQ_API_KEY not set")
        self.model = settings.groq_model
        self.name = f"groq/{self.model}"
        self.url = "https://api.groq.com/openai/v1/chat/completions"
        self.headers = {"Authorization": f"Bearer {settings.groq_api_key}"}

    @retry(retry=retry_if_exception(_is_retryable),
           wait=wait_exponential(multiplier=2, min=10, max=120),
           stop=stop_after_attempt(5),
           before_sleep=lambda rs: log.warning("Groq chat request failed, retrying in %ss…",
                                               round(rs.next_action.sleep)))
    def complete(self, messages: list[dict]) -> str:
        body = {
            "model": self.model,
            "temperature": settings.chat_temperature,
            "max_tokens": settings.chat_max_tokens,
            "messages": messages,
        }
        r = httpx.post(self.url, headers=self.headers, json=body,
                       timeout=settings.request_timeout * 4)
        if r.status_code == 429:
            body_text = r.text
            if "per day" in body_text or "TPD" in body_text:
                raise DailyLimitError("Groq daily token limit exhausted")
            wait = int(r.headers.get("retry-after", 10))
            log.warning("Groq 429 (RPM) — waiting %ss", wait)
            _time.sleep(wait)
            r.raise_for_status()
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()


class GoogleChat:
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
           stop=stop_after_attempt(5),
           before_sleep=lambda rs: log.warning("Google chat request failed, retrying in %ss…",
                                               round(rs.next_action.sleep)))
    def complete(self, messages: list[dict]) -> str:
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        contents = [
            {"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
            for m in messages if m["role"] != "system"
        ]
        body = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": contents,
            "generationConfig": {
                "temperature": settings.chat_temperature,
                "maxOutputTokens": settings.chat_max_tokens,
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
        return r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()


class OllamaChat:
    def __init__(self) -> None:
        self.model = settings.ollama_model
        self.name = f"ollama/{self.model}"
        self.url = f"{settings.ollama_host}/api/chat"

    def complete(self, messages: list[dict]) -> str:
        body = {
            "model": self.model,
            "stream": False,
            "options": {"temperature": settings.chat_temperature},
            "messages": messages,
        }
        r = httpx.post(self.url, json=body, timeout=settings.request_timeout * 8)
        r.raise_for_status()
        return r.json()["message"]["content"].strip()


class FallbackChat:
    """Same idea as score.judge.FallbackJudge: advance to the next provider on
    daily-quota exhaustion, remember the position for the rest of the process."""
    def __init__(self, backends: list) -> None:
        self._backends = backends
        self._idx = 0

    @property
    def name(self) -> str:
        if self._idx < len(self._backends):
            return self._backends[self._idx].name
        return "fallback/exhausted"

    def complete(self, messages: list[dict]) -> str:
        while self._idx < len(self._backends):
            backend = self._backends[self._idx]
            try:
                return backend.complete(messages)
            except DailyLimitError:
                log.warning("chat provider %s daily limit exhausted — switching", backend.name)
                self._idx += 1
        raise JudgeError("all chat LLM providers exhausted their daily limits")


def _ollama_reachable() -> bool:
    try:
        httpx.get(f"{settings.ollama_host}/api/tags", timeout=2.0)
        return True
    except Exception:
        return False


def get_chat_llm() -> ChatLLM:
    """Normal tier for interactive chat: Ollama only, no cloud fallback.
    Groq/Google are premium — reserved for report writing (get_premium_chat)."""
    if not _ollama_reachable():
        raise JudgeError("Ollama not reachable — no fallback configured (premium "
                          "tier is reserved for report writing)")
    return OllamaChat()


def get_premium_chat() -> ChatLLM | None:
    """Premium tier: Groq → Google (skip unconfigured), never Ollama.
    Used only by report/summarize.py to write the digest's executive summary.
    Returns None when neither provider is configured, so the report can skip
    the summary rather than fail."""
    chain: list[ChatLLM] = []

    if settings.groq_api_key:
        try:
            chain.append(GroqChat())
        except JudgeError as e:
            log.warning("Groq (premium) chat unavailable: %s", e)

    if settings.google_api_key:
        try:
            chain.append(GoogleChat())
        except JudgeError as e:
            log.warning("Google (premium) chat unavailable: %s", e)

    if not chain:
        log.warning("no premium chat provider configured — report will skip the LLM summary")
        return None

    if len(chain) == 1:
        return chain[0]
    return FallbackChat(chain)
