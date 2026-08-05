"""
Run ingestion from the command line.

    python -m scripts.ingest               # all default feeds
    python -m scripts.ingest --source RBI  # only feeds for source (eg. RBI)
    python -m scripts.ingest --list        # show configured feeds

Test one adapter at a time before wiring the scheduler.
"""
from __future__ import annotations

import argparse

from finrag.ingest.http import HttpClient
from finrag.ingest.pipeline import run_source
from finrag.ingest.rss import DEFAULT_FEEDS, RssAdapter


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", help="only run feeds for this source name")
    ap.add_argument("--list", action="store_true", help="list feeds and exit")
    args = ap.parse_args()

    feeds = DEFAULT_FEEDS
    if args.source:
        feeds = [f for f in feeds if f.source_name == args.source.upper()]
    if args.list:
        for f in feeds:
            print(f"{f.source_name:16} {f.url}")
        return

    total_new = total_fetched = 0
    for spec in feeds:
        summary = run_source(RssAdapter(spec))
        flag = "ERR" if summary["error"] else "ok "
        print(f"[{flag}] {summary['source']:16} fetched={summary['fetched']:3} "
              f"new={summary['new']:3} {summary['error'] or ''}")
        total_fetched += summary["fetched"]
        total_new += summary["new"]
    print(f"\nTOTAL fetched={total_fetched} new={total_new}")


if __name__ == "__main__":
    main()
