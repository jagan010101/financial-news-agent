"""
finrag.chat.retrieve — semantic search over the article corpus + portfolio context.

Retrieval is corpus-wide by default (any company, any industry): the wire and
aggregator sources ingest broad market news independent of PORTFOLIO, so most
of the article table is NOT holding-scoped. `holding_id` narrows the search
only when the caller has already matched the question to a portfolio holding.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz
from sqlalchemy import text

from finrag.config import settings


@dataclass(frozen=True)
class ArticleHit:
    id: int
    title: str
    url: str | None
    source: str | None
    published_at: dt.datetime | None
    event_type: str | None
    snippet: str
    score: float


@dataclass(frozen=True)
class HoldingRef:
    id: int
    common_name: str
    nse_symbol: str | None
    sector: str | None
    industry: str | None
    weight: float | None
    aliases: list[str] = field(default_factory=list)


def _vec_literal(embedding) -> str:
    """Format an embedding as a pgvector literal (see score/pipeline.py for why
    plain str() on numpy arrays is unsafe here)."""
    vals = embedding.tolist() if hasattr(embedding, "tolist") else list(embedding)
    return "[" + ",".join(repr(float(x)) for x in vals) + "]"


def match_holding_mention(question: str, holdings: list[HoldingRef]) -> HoldingRef | None:
    """Fuzzy-match a portfolio holding mentioned in the question. Conservative:
    an exact ticker/alias/name hit wins immediately; otherwise the best fuzzy
    candidate is accepted only above a high threshold, else None (no guess)."""
    toks = set(re.findall(r"[A-Za-z0-9]+", question.upper()))
    q_cf = question.casefold()
    best: tuple[float, HoldingRef] | None = None

    for h in holdings:
        candidates = [h.common_name, *h.aliases]
        if h.nse_symbol:
            candidates.append(h.nse_symbol)
        for c in candidates:
            cf = c.casefold().strip()
            if not cf:
                continue
            if c.isupper() or len(cf) <= 4:
                if c.upper() in toks:
                    return h
                continue
            if re.search(rf"\b{re.escape(cf)}\b", q_cf):
                return h
            score = fuzz.token_set_ratio(cf, q_cf)
            if score >= 90 and (best is None or score > best[0]):
                best = (score, h)
    return best[1] if best else None


def load_holdings(session) -> list[HoldingRef]:
    rows = session.execute(text("""
        SELECT id, common_name, nse_symbol, sector, industry, weight, aliases
        FROM holdings
        WHERE is_active = true
    """)).all()
    return [
        HoldingRef(id=r.id, common_name=r.common_name, nse_symbol=r.nse_symbol,
                   sector=r.sector, industry=r.industry,
                   weight=float(r.weight) if r.weight is not None else None,
                   aliases=list(r.aliases or []))
        for r in rows
    ]


def semantic_search(
    query: str,
    *,
    k: int | None = None,
    days: int | None = None,
    holding_id: int | None = None,
    session=None,
) -> list[ArticleHit]:
    """Embed `query`, pull ANN candidates from Postgres/pgvector, rerank with
    the cross-encoder, return the top-k as ArticleHit. `days=0` disables the
    recency filter (search the whole corpus)."""
    from finrag.process.embed import embed_one, rerank
    from finrag.store.db import SessionLocal

    k = k or settings.chat_top_k
    days = settings.chat_recency_days if days is None else days

    own_session = session is None
    session = session or SessionLocal()
    try:
        qvec = _vec_literal(embed_one(query))
        clauses = ["a.embedding IS NOT NULL"]
        params: dict = {"emb": qvec, "limit": settings.chat_candidates}
        if days:
            clauses.append("a.fetched_at > now() - make_interval(days => :days)")
            params["days"] = days
        join = ""
        if holding_id is not None:
            join = "JOIN article_holdings ah ON ah.article_id = a.id AND ah.holding_id = :holding_id"
            params["holding_id"] = holding_id
        where = " AND ".join(clauses)
        rows = session.execute(text(f"""
            SELECT a.id, a.title, a.body, a.url, a.published_at, a.event_type,
                   s.name AS source_name
            FROM articles a
            {join}
            LEFT JOIN sources s ON s.id = a.source_id
            WHERE {where}
            ORDER BY a.embedding <=> CAST(:emb AS vector)
            LIMIT :limit
        """), params).all()
    finally:
        if own_session:
            session.close()

    if not rows:
        return []

    docs = [f"{r.title}\n{(r.body or '')[:800]}" for r in rows]
    ranked = rerank(query, docs, top_k=k)
    hits = []
    for idx, score in ranked:
        r = rows[idx]
        hits.append(ArticleHit(
            id=r.id, title=r.title, url=r.url, source=r.source_name,
            published_at=r.published_at, event_type=r.event_type,
            snippet=(r.body or "").strip()[:400], score=score,
        ))
    return hits


def holding_recent_activity(holding_id: int, *, days: int = 30, limit: int = 5, session=None) -> list[dict]:
    """Most material scored articles for one holding in the last `days`, for
    portfolio-aware answers ("what's moved this stock recently")."""
    from finrag.store.db import SessionLocal

    own_session = session is None
    session = session or SessionLocal()
    try:
        rows = session.execute(text("""
            SELECT a.title, a.published_at, sc.composite, sc.event_type
            FROM scores sc
            JOIN articles a ON a.id = sc.article_id
            WHERE sc.holding_id = :hid
              AND sc.scored_at > now() - make_interval(days => :days)
            ORDER BY sc.composite DESC
            LIMIT :limit
        """), {"hid": holding_id, "days": days, "limit": limit}).all()
        return [dict(title=r.title, published_at=r.published_at,
                     composite=float(r.composite), event_type=r.event_type)
                for r in rows]
    finally:
        if own_session:
            session.close()
