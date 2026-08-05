"""
finrag.process.pipeline — apply the resolver to ingested articles.

Pulls articles with status='ingested', resolves each against the gazetteer,
writes article_holdings links, and advances status:
  * 'resolved'   -> at least one holding matched (goes on to scoring)
  * 'irrelevant' -> no holding matched (the pre-filter drop; never scored)

This is THE pre-filter that protects the rate-limited scorer. Idempotent:
re-running only touches still-'ingested' rows. Links are upserted so re-runs
don't duplicate.
"""
from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from finrag.process.gazetteer import Gazetteer
from finrag.process.resolve import resolve
from finrag.store.db import (
    Article, ArticleHolding, Holding, SessionLocal, Subsidiary,
)


def run(*, session=None, limit: int | None = None) -> dict:
    own = session is None
    session = session or SessionLocal()
    summary = {"processed": 0, "resolved": 0, "irrelevant": 0, "links": 0}
    try:
        holdings = list(session.scalars(select(Holding).where(Holding.is_active)))
        subsidiaries = list(session.scalars(select(Subsidiary)))
        gaz = Gazetteer.from_holdings(holdings, subsidiaries)

        q = select(Article).where(Article.status == "ingested").order_by(Article.id)
        if limit:
            q = q.limit(limit)
        articles = list(session.scalars(q))

        for art in articles:
            summary["processed"] += 1
            matches = resolve(art.title, art.body, gaz)
            if matches:
                rows = [dict(article_id=art.id, holding_id=m.holding_id,
                             match_method=m.method, match_score=m.score)
                        for m in matches]
                stmt = insert(ArticleHolding).values(rows).on_conflict_do_nothing(
                    index_elements=[ArticleHolding.article_id,
                                    ArticleHolding.holding_id])
                session.execute(stmt)
                art.status = "resolved"
                summary["resolved"] += 1
                summary["links"] += len(rows)
            else:
                art.status = "irrelevant"
                summary["irrelevant"] += 1
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        if own:
            session.close()
    return summary
