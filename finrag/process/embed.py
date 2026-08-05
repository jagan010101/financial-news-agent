"""
finrag.process.embed — embeddings + reranking.

Retrieve-then-rerank is the single biggest free quality lever in modern RAG:
a cheap bi-encoder (embedding) pulls ~50 candidates, then a cross-encoder
reranker reorders to the top ~5 the judge actually reads. The reranker sees
the (query, doc) pair jointly, so it catches relevance the embedding misses.

Models are lazy-loaded (heavy import) and cached, so importing this module is
cheap and unit tests that don't embed stay fast. Defaults are Qwen3 (frontier,
free); fallbacks are BGE if Qwen weights are unavailable offline.
"""
from __future__ import annotations

from functools import lru_cache

from finrag.config import settings


@lru_cache(maxsize=1)
def _embedder():
    from sentence_transformers import SentenceTransformer
    try:
        return SentenceTransformer(settings.embed_model)
    except Exception:
        return SentenceTransformer(settings.embed_fallback)


@lru_cache(maxsize=1)
def _reranker():
    from sentence_transformers import CrossEncoder
    try:
        return CrossEncoder(settings.rerank_model)
    except Exception:
        return CrossEncoder("BAAI/bge-reranker-v2-m3")


def embed(texts: list[str]) -> list[list[float]]:
    """Embed a batch. Normalized vectors -> cosine == dot product."""
    model = _embedder()
    vecs = model.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
    return [v.tolist() for v in vecs]


def embed_one(text: str) -> list[float]:
    return embed([text])[0]


def rerank(query: str, docs: list[str], top_k: int | None = None) -> list[tuple[int, float]]:
    """
    Cross-encode (query, doc) pairs. Returns [(original_index, score), ...]
    sorted best-first, truncated to top_k. Indices refer to `docs`.
    """
    if not docs:
        return []
    top_k = top_k or settings.rerank_top_k
    model = _reranker()
    scores = model.predict([(query, d) for d in docs])
    ranked = sorted(enumerate(scores), key=lambda t: t[1], reverse=True)
    return [(i, float(s)) for i, s in ranked[:top_k]]
