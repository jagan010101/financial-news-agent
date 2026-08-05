"""
finrag.score.sentiment — FinBERT directional-sentiment wrapper.

Lazy-loads the configured model (default: ProsusAI/finbert) on the first call;
the pipeline is LRU-cached so every subsequent call is essentially free.

If the model can't be loaded (offline, weights not cached) or sentiment is
disabled in config, sentiment() returns ('neutral', 0.0) — it never raises
into the scoring path.  That's the contract: callers must never guard against
exceptions from this module.
"""
from __future__ import annotations

from functools import lru_cache

from finrag.config import settings


@lru_cache(maxsize=1)
def _pipeline():
    from transformers import pipeline as hf_pipeline
    return hf_pipeline(
        "text-classification",
        model=settings.sentiment_model,
        top_k=None,      # return scores for all classes, not just the top one
        truncation=True,
    )


def sentiment(text: str) -> tuple[str | None, float | None]:
    """Return (label, confidence) where label in {'positive','negative','neutral'}.

    Input is truncated to 2 048 chars before tokenisation (the pipeline also
    applies model-max-length truncation, so this is just an OOM guard).
    Returns (None, None) when sentiment_enabled=False or on any exception so
    callers can store a true NULL rather than confusing a failure with a
    legitimately neutral article.
    """
    if not settings.sentiment_enabled:
        return (None, None)
    try:
        pipe = _pipeline()
        results = pipe(text[:2048])
        # top_k=None with a string input → flat list of {'label':…,'score':…}
        # Guard against batch-mode nesting just in case.
        if results and isinstance(results[0], list):
            results = results[0]
        best = max(results, key=lambda r: r["score"])
        return (best["label"].lower(), round(float(best["score"]), 4))
    except Exception:
        return (None, None)
