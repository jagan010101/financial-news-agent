"""
finrag.ingest.pipeline — central ingestion: run an adapter, persist new items,
log the cycle. Adapters stay thin; all DB/dedup/observability lives here.

Dedup: INSERT ... ON CONFLICT (content_hash) DO NOTHING. The DB unique
constraint is the single source of truth, so the same story arriving from two
feeds (or re-arriving next cycle) is silently skipped. We count how many were
actually new via RETURNING.

Every cycle writes an ingest_log row (fetched, new_items, error) for
observability — you can see at a glance which sources are healthy.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert

from finrag.ingest.base import RawItem
from finrag.store.db import Article, SessionLocal, Source


def _source_id_map(session) -> dict[str, int]:
    return {name: sid for sid, name in session.execute(
        text("select id, name from sources")).all()}


def persist_items(session, items: list[RawItem], source_ids: dict[str, int]) -> int:
    """Insert items, skipping content_hash duplicates. Returns count of NEW rows."""
    if not items:
        return 0
    rows = []
    seen_hashes = set()
    for it in items:
        h = it.hash()
        if h in seen_hashes:          # dedup within this batch too
            continue
        seen_hashes.add(h)
        sid = source_ids.get(it.source_name)
        if sid is None:
            continue                  # unknown source -> skip (mis-config guard)
        rows.append(dict(
            source_id=sid, url=it.url, external_id=it.external_id,
            title=it.title, body=it.body, published_at=it.published_at,
            fetched_at=dt.datetime.now(dt.timezone.utc),
            content_hash=h, raw=it.raw, status="ingested",
        ))
    if not rows:
        return 0
    stmt = insert(Article).values(rows).on_conflict_do_nothing(
        index_elements=[Article.content_hash]).returning(Article.id)
    new_ids = session.execute(stmt).scalars().all()
    return len(new_ids)


def run_source(adapter, *, session=None) -> dict:
    """
    Execute one adapter end-to-end: fetch -> persist -> log.
    Returns a summary dict. Safe to call repeatedly (idempotent via dedup).
    """
    own = session is None
    session = session or SessionLocal()
    summary = {"source": adapter.name, "fetched": 0, "new": 0, "error": None}
    log_id = None
    try:
        source_ids = _source_id_map(session)
        sid = source_ids.get(adapter.name)
        # open ingest_log row
        log_id = session.execute(text(
            "insert into ingest_log (source_id, started_at) "
            "values (:sid, now()) returning id"), {"sid": sid}).scalar()
        session.commit()

        items = list(adapter.fetch())
        summary["fetched"] = len(items)
        summary["new"] = persist_items(session, items, source_ids)
        session.execute(text(
            "update ingest_log set finished_at=now(), fetched=:f, new_items=:n "
            "where id=:id"),
            {"f": summary["fetched"], "n": summary["new"], "id": log_id})
        session.commit()
    except Exception as e:               # noqa: BLE001 — record and surface
        session.rollback()
        summary["error"] = f"{type(e).__name__}: {e}"
        if log_id is not None:
            session.execute(text(
                "update ingest_log set finished_at=now(), error=:err where id=:id"),
                {"err": summary["error"], "id": log_id})
            session.commit()
    finally:
        if own:
            session.close()
    return summary
