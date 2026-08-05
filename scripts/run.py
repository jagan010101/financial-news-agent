"""
Drive the finrag pipeline.

    python -m scripts.run                 # one full cycle (ingest->resolve->score->report)
    python -m scripts.run --dry-run       # full cycle but don't actually send email
    python -m scripts.run --no-score      # ingest+resolve only (e.g. before LLM key set)
    python -m scripts.run --sources RBI MONEYCONTROL   # restrict ingest to some sources
    python -m scripts.run --schedule      # run forever on the interval scheduler

One full cycle is the unit of work; the scheduler just calls it on a timer.
Every stage is idempotent, so re-running is always safe.
"""
from __future__ import annotations

import argparse
import logging


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="don't send email")
    ap.add_argument("--no-score", action="store_true", help="skip scoring stage")
    ap.add_argument("--no-report", action="store_true", help="skip report stage")
    ap.add_argument("--sources", nargs="*", help="restrict ingest to these source names")
    ap.add_argument("--schedule", action="store_true", help="run forever on a timer")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # suppress httpx request logs to avoid leaking API keys in URLs
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if args.schedule:
        from finrag.scheduler import main as sched_main
        sched_main()
        return

    from finrag.orchestrate import run_once
    s = run_once(
        sources=args.sources,
        do_score=not args.no_score,
        do_report=not args.no_report,
        dry_run_report=args.dry_run,
    )
    print(f"\ncycle done in {s.seconds}s")
    for stage in ("ingest", "resolve", "score", "report"):
        val = getattr(s, stage)
        print(f"  {stage:8}: {val}")
    if s.errors:
        print("  errors  :")
        for e in s.errors:
            print(f"    - {e}")


if __name__ == "__main__":
    main()
