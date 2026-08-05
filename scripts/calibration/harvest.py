"""
One-time calibration harvest: score a large batch without touching defaults.

    python -m scripts.calibration.harvest              # 80 articles, 2 s delay
    python -m scripts.calibration.harvest --limit 40   # smaller batch
    python -m scripts.calibration.harvest --delay 15   # fully-safe Groq pass

What it does
  1. Reports the current backlog (status='resolved' articles + holding links).
  2. Calls score.pipeline.run() with explicit limit and call_delay — does NOT
     touch score_per_cycle or judge_call_delay in config.py.
  3. Prints a post-run breakdown by validation_status and finbert_label, plus
     the scored_at range so you can confirm timestamps were written.

Groq free-tier rate limits (llama-3.3-70b-versatile)
  30 RPM  →  minimum 2 s gap (hard floor from requests-per-minute)
  6 000 TPM  →  at ~1 300 tokens/call that is ≈ 4.6 calls/min → 13 s safe gap
  1 000 RPD  →  80 calls = 8 % of the daily budget; fine for one harvest

The default 2 s delay here relies on GroqJudge's built-in 429 / retry-after
back-off (tenacity, up to 8 attempts) rather than pre-emptive sleeping.  Each
TPM-rate-limited 429 adds the retry-after seconds automatically.  If your
account is heavily loaded or you want zero 429s, pass --delay 15.

If Groq's *daily* quota runs out mid-harvest, the FallbackJudge advances to
Google AI Studio (if GOOGLE_API_KEY is set) or Ollama (if running locally).
"""
from __future__ import annotations

import argparse
import datetime as dt

from sqlalchemy import text

from finrag.store.db import SessionLocal

_DEFAULT_LIMIT = 80
_DEFAULT_DELAY = 2.0   # seconds — see rate-limit note above


# ── helpers ───────────────────────────────────────────────────────────────────

def _backlog(session) -> tuple[int, int]:
    row = session.execute(text("""
        SELECT
            COUNT(DISTINCT a.id)  AS articles,
            COUNT(ah.article_id)  AS links
        FROM articles a
        LEFT JOIN article_holdings ah ON ah.article_id = a.id
        WHERE a.status = 'resolved'
    """)).one()
    return int(row.articles), int(row.links)


def _post_run_breakdown(session, since: dt.datetime, total_scores: int) -> None:
    def pct(n: int) -> str:
        return f"{100 * n / total_scores:5.1f} %" if total_scores else "    — "

    # scored_at sanity check
    ts = session.execute(text("""
        SELECT MIN(scored_at) AS earliest, MAX(scored_at) AS latest,
               COUNT(*) FILTER (WHERE scored_at IS NULL) AS null_count
        FROM scores WHERE scored_at >= :since
    """), {"since": since}).one()

    print(f"\n  {'─' * 54}")
    if ts.null_count and ts.null_count > 0:
        print(f"  WARNING: {ts.null_count} new rows have scored_at = NULL")
    else:
        fmt = "%Y-%m-%d %H:%M:%S UTC"
        earliest = ts.earliest.strftime(fmt) if ts.earliest else "n/a"
        latest   = ts.latest.strftime(fmt)   if ts.latest   else "n/a"
        print(f"  scored_at  {earliest}  →  {latest}")

    # validation_status
    print(f"\n  VALIDATION STATUS  (of {total_scores} new score rows)")
    for r in session.execute(text("""
        SELECT validation_status, COUNT(*) AS cnt
        FROM scores WHERE scored_at >= :since
        GROUP BY validation_status ORDER BY cnt DESC
    """), {"since": since}).all():
        label = r.validation_status or "(null)"
        print(f"    {label:<14}  {int(r.cnt):>4,}  {pct(int(r.cnt))}")

    # finbert_label
    print(f"\n  FINBERT LABEL")
    for r in session.execute(text("""
        SELECT COALESCE(finbert_label, '(null)') AS label, COUNT(*) AS cnt
        FROM scores WHERE scored_at >= :since
        GROUP BY finbert_label ORDER BY cnt DESC
    """), {"since": since}).all():
        print(f"    {r.label:<14}  {int(r.cnt):>4,}  {pct(int(r.cnt))}")

    # composite distribution (quick sanity)
    dist = session.execute(text("""
        SELECT
            MIN(composite)  AS lo,
            MAX(composite)  AS hi,
            AVG(composite)  AS avg,
            COUNT(*) FILTER (WHERE composite > 5.0) AS above_threshold
        FROM scores WHERE scored_at >= :since
    """), {"since": since}).one()
    if dist.lo is not None:
        print(f"\n  COMPOSITE  "
              f"min={float(dist.lo):.1f}  "
              f"max={float(dist.hi):.1f}  "
              f"avg={float(dist.avg):.1f}  "
              f"above_threshold={int(dist.above_threshold or 0)}")

    print()


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Score a calibration batch without changing steady-state defaults.")
    ap.add_argument("--limit", type=int, default=_DEFAULT_LIMIT,
                    help=f"max articles to score in this pass (default {_DEFAULT_LIMIT})")
    ap.add_argument("--delay", type=float, default=_DEFAULT_DELAY,
                    help=f"seconds between judge calls (default {_DEFAULT_DELAY}; "
                         "use 15 for zero-429 Groq pass)")
    args = ap.parse_args()

    # ── 1. backlog report ─────────────────────────────────────────────────────
    with SessionLocal() as session:
        art_cnt, link_cnt = _backlog(session)

    print(f"\n  Backlog  {art_cnt:,} resolved articles  "
          f"({link_cnt:,} article-holding links, "
          f"ceiling on score rows this pass)")

    effective = min(args.limit, art_cnt)
    if effective == 0:
        print("  Nothing to score — run `python -m scripts.ingest` first.")
        return

    est_secs = effective * (args.delay + 2)   # +2 s rough inference overhead
    print(f"  Scoring up to {effective} articles  "
          f"({args.delay} s inter-call delay, "
          f"~{est_secs / 60:.0f} min estimated)")
    print(f"  Config defaults untouched: "
          f"score_per_cycle={__import__('finrag.config', fromlist=['settings']).settings.score_per_cycle}  "
          f"judge_call_delay={__import__('finrag.config', fromlist=['settings']).settings.judge_call_delay}")

    # ── 2. harvest ────────────────────────────────────────────────────────────
    from finrag.score.pipeline import run

    start = dt.datetime.now(tz=dt.timezone.utc)
    print(f"\n  Started {start.strftime('%H:%M:%S')} UTC\n")

    summary = run(limit=args.limit, call_delay=args.delay)

    elapsed = (dt.datetime.now(tz=dt.timezone.utc) - start).total_seconds()
    m, s = divmod(int(elapsed), 60)
    print(f"\n  Done in {m}m {s}s — "
          f"articles={summary['articles']}  "
          f"scores={summary['scores']}  "
          f"above_threshold={summary['above_threshold']}  "
          f"errors={summary['errors']}")

    # ── 3. post-run report ────────────────────────────────────────────────────
    if summary["scores"] > 0:
        with SessionLocal() as session:
            _post_run_breakdown(session, start, summary["scores"])
    else:
        print("  No scores written (all resolved articles may lack holding links).")


if __name__ == "__main__":
    main()
