"""
finrag.report.pipeline — assemble + send the digest, idempotently.

Selects above-threshold scores whose article has NOT already been reported,
builds a ReportItem per (article, holding) keeping the highest score per
article, renders the digest, sends it, and records the article_ids in `reports`
so they are never emailed again.

Idempotency is enforced two ways:
  * we exclude article_ids already present in any sent `reports` row;
  * the send + record happen in one transaction (record only on success).

run() returns a summary. dry_run lets you exercise the whole path without SMTP
AND without touching the `reports` table — it returns before any INSERT/UPDATE,
so a dry run never marks articles as reported (they stay eligible for a real
send later). Only a real (non-dry-run) send affects idempotency.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import text

from finrag.config import settings
from finrag.deliver.email import send_email
from finrag.report.render import ReportItem, render_html, render_text
from finrag.report.summarize import write_summary
from finrag.store.db import SessionLocal


def _already_reported_ids(session) -> set[int]:
    rows = session.execute(text(
        "select unnest(article_ids) from reports where delivery_status='sent'")).all()
    return {r[0] for r in rows}


def _select_items(session) -> list[ReportItem]:
    reported = _already_reported_ids(session)
    rows = session.execute(text("""
        select distinct on (sc.article_id)
            sc.article_id, a.title, a.url, a.published_at,
            coalesce(h.nse_symbol, h.common_name) holding, h.sector,
            sc.composite, sc.event_type, sc.rationale, sc.rule_floor,
            coalesce(s.name, '') source
        from scores sc
        join articles a on a.id = sc.article_id
        join holdings h on h.id = sc.holding_id
        left join sources s on s.id = a.source_id
        where sc.composite > :thr
        order by sc.article_id, sc.composite desc
    """), {"thr": settings.score_threshold}).all()
    items = []
    for r in rows:
        if r.article_id in reported:
            continue
        items.append(ReportItem(
            article_id=r.article_id, title=r.title, url=r.url, holding=r.holding,
            sector=r.sector, composite=float(r.composite), event_type=r.event_type,
            rationale=r.rationale, rule_floor=r.rule_floor,
            published_at=r.published_at, source=r.source))
    return items


def run(*, session=None, dry_run: bool = False, send_fn=send_email) -> dict:
    own = session is None
    session = session or SessionLocal()
    summary = {"items": 0, "sent": False, "report_id": None, "skipped_empty": False}
    try:
        items = _select_items(session)
        summary["items"] = len(items)
        if not items:
            summary["skipped_empty"] = True
            return summary

        n_imm = sum(1 for i in items if i.composite >= settings.immediate_threshold)
        subject = (f"[Portfolio] {len(items)} news item(s)"
                   + (f" — {n_imm} urgent" if n_imm else ""))
        llm_summary = write_summary(items)  # premium tier; None if unconfigured/failed
        html_body = render_html(items, llm_summary)
        text_body = render_text(items, llm_summary)

        if dry_run:
            # Preview only — deliberately returns before any `reports` write, so
            # article_ids never get marked reported and stay eligible for a real send.
            summary["sent"] = send_fn(subject, html_body, text_body, dry_run=True)
            return summary

        # record FIRST as pending, then send, then mark sent — so a crash mid-send
        # leaves an auditable 'pending' row rather than silently losing the record.
        article_ids = [i.article_id for i in items]
        report_id = session.execute(text("""
            insert into reports (kind, payload, article_ids, delivery_status)
            values ('digest', :payload, :ids, 'pending') returning id
        """), {"payload": '{"items": %d}' % len(items),
               "ids": article_ids}).scalar()
        session.commit()
        summary["report_id"] = report_id

        ok = send_fn(subject, html_body, text_body, dry_run=False)

        session.execute(text(
            "update reports set delivery_status=:st, sent_at=now() where id=:id"),
            {"st": "sent" if ok else "failed", "id": report_id})
        session.commit()
        summary["sent"] = bool(ok)
    except Exception as e:
        session.rollback()
        if summary.get("report_id"):
            session.execute(text(
                "update reports set delivery_status='failed' where id=:id"),
                {"id": summary["report_id"]})
            session.commit()
        summary["error"] = f"{type(e).__name__}: {e}"
    finally:
        if own:
            session.close()
    return summary
