"""
finrag.calibration.validate_report — read-only query helpers for validation health.

All functions are pure read: they open a session, run SELECT, return Python
structures, close.  No writes, no side-effects.  No new dependencies —
everything uses the existing SQLAlchemy engine.

Typical call from the script:

    from finrag.calibration.validate_report import fetch_validation_summary
    summary = fetch_validation_summary(days=7)
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import text

from finrag.store.db import SessionLocal


@dataclass
class FlaggedRow:
    fetched_at: str          # "YYYY-MM-DD HH:MM" — display-ready
    title: str
    holding: str
    composite: float
    event_type: str | None
    reasons: list[str]
    finbert_label: str | None


def fetch_validation_summary(
    *,
    days: int = 7,
    session=None,
) -> dict:
    """Query the scores table and return a summary dict.

    Keys:
        window_days     int
        total_scored    int  — rows with a non-NULL validation_status in window
        status_counts   dict[str, int]   — {"passed": N, "flagged": N, ...}
        reason_counts   dict[str, int]   — per flag-reason name, sorted desc
        recent_flagged  list[FlaggedRow] — up to 20 most recent 'flagged' rows

    The window is applied on articles.fetched_at (set at ingestion time).
    Rows whose fetched_at IS NULL are excluded from window-filtered queries.
    Pass a large value (e.g. days=3650) to see everything.
    """
    own = session is None
    session = session or SessionLocal()
    window = f"{days} days"

    try:
        # ── 1. status breakdown ──────────────────────────────────────────────
        status_rows = session.execute(text("""
            SELECT sc.validation_status, COUNT(*) AS cnt
            FROM scores sc
            JOIN articles a ON a.id = sc.article_id
            WHERE sc.validation_status IS NOT NULL
              AND a.fetched_at > NOW() - CAST(:window AS INTERVAL)
            GROUP BY sc.validation_status
            ORDER BY cnt DESC
        """), {"window": window}).all()

        status_counts: dict[str, int] = {r.validation_status: int(r.cnt)
                                          for r in status_rows}
        total_scored = sum(status_counts.values())

        # ── 2. flag-reason frequency (unnest array → one row per reason) ────
        reason_rows = session.execute(text("""
            SELECT reason, COUNT(*) AS cnt
            FROM scores sc
            JOIN articles a ON a.id = sc.article_id,
                 UNNEST(sc.flag_reasons) AS reason
            WHERE sc.flag_reasons IS NOT NULL
              AND array_length(sc.flag_reasons, 1) > 0
              AND a.fetched_at > NOW() - CAST(:window AS INTERVAL)
            GROUP BY reason
            ORDER BY cnt DESC
        """), {"window": window}).all()

        reason_counts: dict[str, int] = {r.reason: int(r.cnt) for r in reason_rows}

        # ── 3. 20 most recent 'flagged' rows ─────────────────────────────────
        flagged_rows = session.execute(text("""
            SELECT
                a.fetched_at,
                a.title,
                COALESCE(h.nse_symbol, h.common_name) AS holding,
                sc.composite,
                sc.event_type,
                sc.flag_reasons,
                sc.finbert_label
            FROM scores sc
            JOIN articles a  ON a.id  = sc.article_id
            JOIN holdings h  ON h.id  = sc.holding_id
            WHERE sc.validation_status = 'flagged'
              AND a.fetched_at > NOW() - CAST(:window AS INTERVAL)
            ORDER BY a.fetched_at DESC
            LIMIT 20
        """), {"window": window}).all()

        recent_flagged: list[FlaggedRow] = []
        for r in flagged_rows:
            ts = r.fetched_at
            ts_str = (ts.strftime("%Y-%m-%d %H:%M") if isinstance(ts, dt.datetime)
                      else str(ts or "")[:16])
            reasons = list(r.flag_reasons or [])
            recent_flagged.append(FlaggedRow(
                fetched_at=ts_str,
                title=(r.title or "")[:60],
                holding=r.holding or "",
                composite=float(r.composite),
                event_type=r.event_type,
                reasons=reasons,
                finbert_label=r.finbert_label,
            ))

    finally:
        if own:
            session.close()

    return {
        "window_days":    days,
        "total_scored":   total_scored,
        "status_counts":  status_counts,
        "reason_counts":  reason_counts,
        "recent_flagged": recent_flagged,
    }
