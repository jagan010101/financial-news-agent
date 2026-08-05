"""
Build and send the portfolio news digest.

    python -m scripts.report             # send via SMTP (needs SMTP_* in .env)
    python -m scripts.report --dry-run   # render + record, no actual send

Selects above-threshold scores not yet reported, batches into one digest
(immediate tier for composite >= IMMEDIATE_THRESHOLD), sends, and records
article_ids so nothing is emailed twice.
"""
from __future__ import annotations

import argparse

from finrag.report.pipeline import run


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="render and record but do not send")
    args = ap.parse_args()
    s = run(dry_run=args.dry_run)
    if s.get("skipped_empty"):
        print("nothing above threshold to report")
    elif s.get("error"):
        print(f"error: {s['error']} (report_id={s.get('report_id')})")
    else:
        print(f"items={s['items']} sent={s['sent']} report_id={s['report_id']}")


if __name__ == "__main__":
    main()
