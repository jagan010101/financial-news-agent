"""
finrag.config — central, environment-driven configuration.

Same code runs locally and on cloud; you change only the .env file.
Secrets (DB password, SMTP/API creds) NEVER live in code.

Model stack:
  embeddings : Qwen3-Embedding-0.6B  (tops MTEB multilingual, Apache-2.0, CPU-ok)
  reranker   : Qwen3-Reranker-0.6B   (cross-encoder; the biggest free precision lever)

  Two LLM tiers, deliberately NOT auto-fallback into each other:
    normal  (free, local) : Ollama — every routine score + interactive chat turn.
    premium (Groq/Google) : ONLY for report-writing summaries and dispute
                            resolution (a normal-tier score the deterministic
                            validator flagged/rejected). The premium verdict
                            is final and gets logged to
                            logs/dispute_verdicts.csv (see score/dispute_log.py).
  See score/judge.py (get_judge / get_premium_judge) and chat/llm.py
  (get_chat_llm / get_premium_chat).
"""
from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- storage ---
    database_url: str = Field(
        default="postgresql+psycopg://finrag:finrag@localhost:5432/finrag",
        description="SQLAlchemy URL. Swap host for cloud managed Postgres.",
    )

    # --- embeddings (local, free) ---
    embed_model: str = "Qwen/Qwen3-Embedding-0.6B"   # frontier open retriever, CPU-friendly
    embed_dim: int = 1024                             # Qwen3-0.6B native dim; MUST match vector(N)
    embed_fallback: str = "BAAI/bge-m3"               # conservative fallback (also 1024-dim)

    # --- reranker (local, free) — retrieve-then-rerank is the key RAG quality lever ---
    rerank_model: str = "Qwen/Qwen3-Reranker-0.6B"    # cross-encoder; fallback BAAI/bge-reranker-v2-m3
    rerank_candidates: int = 50                       # bi-encoder pulls this many...
    rerank_top_k: int = 5                             # ...cross-encoder keeps this many

    # --- LLM tiers ---
    llm_provider: str = "ollama"                      # normal-tier label for model_tag()
    # normal tier: Ollama, local, free — routine scoring + chat (get_judge/get_chat_llm)
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b-instruct"
    # premium tier: Groq -> Google — report-writing summaries + dispute
    # resolution ONLY (get_premium_judge/get_premium_chat). Never used silently.
    groq_api_key: str | None = None
    groq_model: str = "llama-3.3-70b-versatile"
    google_api_key: str | None = None
    google_model: str = "gemini-2.0-flash"

    llm_temperature: float = 0.0                      # deterministic scoring
    llm_max_tokens: int = 1024

    # --- sentiment (FinBERT directional signal, optional coherence check) ---
    sentiment_enabled: bool = True
    sentiment_model: str = "ProsusAI/finbert"

    # --- scoring ---
    score_threshold: float = 5.0                      # email if composite > this
    immediate_threshold: float = 8.0                  # individual alert vs batched digest
    prompt_version: str = "v1"                        # bump when the rubric changes
    score_per_cycle: int = 20                         # max articles scored per run (backlog guard)
    judge_call_delay: float = 8.0                     # seconds between judge calls (Groq 12k TPM)

    # --- context retrieval (grounds the judge with prior coverage) ---
    # Selected independently per scope (company, sector): articles clearing the
    # similarity floor, UNIONED with articles inside the recency window, capped
    # at context_max_k each. If that union is thinner than context_min_k, it's
    # backfilled by nearest similarity so the judge always gets a baseline.
    context_min_k: int = 3                             # floor per scope (company, sector)
    context_max_k: int = 6                             # cap per scope, per selection method
    context_similarity_min: float = 0.55               # cosine similarity floor to count as "similar"
    context_recency_hours: int = 72                    # window to count as "recent"

    # --- ingestion ---
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
    request_timeout: int = 15
    nse_min_interval: float = 3.0                     # seconds between NSE hits (anti-ban)

    # --- delivery ---
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None                  # Gmail app password, via .env
    email_to: str | None = None

    # --- dedup ---
    minhash_perms: int = 128
    near_dup_jaccard: float = 0.80
    semantic_dup_cosine: float = 0.92
    dedup_window_hours: int = 24

    # --- chatbot (conversational RAG over the article corpus) ---
    chat_temperature: float = 0.3                 # more natural than the deterministic judge
    chat_max_tokens: int = 900
    chat_history_turns: int = 6                   # prior user/assistant turns kept in the prompt
    chat_candidates: int = 40                     # ANN candidates before rerank
    chat_top_k: int = 6                           # final articles put in the context block
    chat_recency_days: int = 180                  # default lookback for retrieval; 0 = no limit


settings = Settings()


# ---------------------------------------------------------------------------
# Source catalogue — seeded into the `sources` table.
# authority_rank: lower = more authoritative (primary filings beat aggregators).
# Feeds the credibility floor and dedup's "keep the best source" rule.
# ---------------------------------------------------------------------------
SOURCE_CATALOG = [
    # id, name,           kind,        authority_rank, poll_seconds
    (1, "NSE_ANN",        "exchange",  1,  600),
    (2, "BSE_ANN",        "exchange",  1,  600),   # disabled: API requires browser session
    (3, "SEBI",           "regulator", 1,  3600),
    (4, "RBI",            "regulator", 1,  3600),
    (5, "MONEYCONTROL",   "aggregator",4,  1800),
    (6, "ECONOMIC_TIMES", "wire",      3,  1800),
    (7, "LIVEMINT",          "wire",      3,  1800),
    (8, "BUSINESS_STANDARD", "wire",      3,  1800),
    (9, "BUSINESSLINE",      "wire",      3,  1800),
]
