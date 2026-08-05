"""
Export a calibration / labelling dataset from scored rows.

    python -m scripts.calibration.export_label_dataset

Writes three CSV files to labelling/ (created if absent, overwritten if present):

  label_sheet.csv     — one row per (article_id, holding_id) pair.
                        gold_relevant / gold_material / notes are blank —
                        a human fills them.

  pipeline_output.csv — same pair_id key; all pipeline scores, validation
                        state, and FinBERT output.  flag_reasons are
                        pipe-joined so the cell is a plain string.

  flag_verdicts.csv   — one row per individual flag on pairs whose
                        validation_status is 'flagged' or 'rejected';
                        verdict blank for human fill.

Selection filter
  Rows where the validation step ran (validation_status IS NOT NULL).
  Rows with event_type = 'judge_error' are excluded unless they ended up
  'flagged' (unlikely in practice but kept for completeness).

No new dependencies — stdlib csv only.
"""
from __future__ import annotations

import csv
import pathlib
import sys

from sqlalchemy import text

from finrag.store.db import SessionLocal

OUT_DIR = pathlib.Path(__file__).parent.parent.parent / "labelling"

_LABEL_COLS = [
    "pair_id", "article_id", "holding_id", "holding_name",
    "title", "body",
    "gold_relevant", "gold_material", "notes",
]

_PIPELINE_COLS = [
    "pair_id",
    "direct_relevance", "materiality", "urgency", "credibility",
    "composite", "rule_floor", "event_type",
    "match_method", "match_score", "authority_rank",
    "validation_status", "flag_reasons",
    "finbert_label", "finbert_score",
]

_VERDICT_COLS = ["pair_id", "flag_reason", "verdict"]

# SQL — one pass that fetches everything needed for all three files.
# LEFT JOINs on article_holdings and sources because source_id can be NULL
# and (rarely) an article may lack a holding link at export time.
_QUERY = text("""
    SELECT
        sc.article_id,
        sc.holding_id,
        COALESCE(h.common_name, h.legal_name)    AS holding_name,
        a.title,
        a.body,
        sc.direct_relevance,
        sc.materiality,
        sc.urgency,
        sc.credibility,
        sc.composite::float                       AS composite,
        sc.rule_floor,
        sc.event_type,
        ah.match_method,
        ah.match_score::float                     AS match_score,
        src.authority_rank,
        sc.validation_status,
        sc.flag_reasons,
        sc.finbert_label,
        sc.finbert_score::float                   AS finbert_score
    FROM scores sc
    JOIN     articles         a   ON a.id  = sc.article_id
    JOIN     holdings         h   ON h.id  = sc.holding_id
    LEFT JOIN article_holdings ah ON ah.article_id = sc.article_id
                                 AND ah.holding_id  = sc.holding_id
    LEFT JOIN sources         src ON src.id = a.source_id
    WHERE sc.validation_status IS NOT NULL
      AND (sc.event_type <> 'judge_error' OR sc.validation_status = 'flagged')
    ORDER BY sc.article_id, sc.holding_id
""")


def _f(v) -> str:
    """None → ''; everything else → str (floats already cast in SQL)."""
    return "" if v is None else str(v)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with SessionLocal() as session:
        rows = session.execute(_QUERY).all()

    if not rows:
        print("No eligible scored rows found. "
              "Run `python -m scripts.calibration.harvest` first.")
        sys.exit(0)

    label_path    = OUT_DIR / "label_sheet.csv"
    pipeline_path = OUT_DIR / "pipeline_output.csv"
    verdict_path  = OUT_DIR / "flag_verdicts.csv"

    existing = [p.name for p in (label_path, pipeline_path, verdict_path) if p.exists()]
    if existing:
        print(f"  Overwriting: {', '.join(existing)}")

    verdict_count      = 0
    blank_gold_count   = 0
    prefilled_gold     = 0  # sanity: should always stay 0

    with (
        open(label_path,    "w", newline="", encoding="utf-8") as lf,
        open(pipeline_path, "w", newline="", encoding="utf-8") as pf,
        open(verdict_path,  "w", newline="", encoding="utf-8") as vf,
    ):
        lw = csv.writer(lf)
        pw = csv.writer(pf)
        vw = csv.writer(vf)

        lw.writerow(_LABEL_COLS)
        pw.writerow(_PIPELINE_COLS)
        vw.writerow(_VERDICT_COLS)

        for r in rows:
            pid = f"{r.article_id}_{r.holding_id}"

            # ── label_sheet ── no score / flag / sentiment columns ────────────
            gold_relevant = ""   # blank — human fills
            gold_material = ""   # blank — human fills
            notes         = ""   # blank — human fills

            lw.writerow([
                pid,
                r.article_id,
                r.holding_id,
                r.holding_name or "",
                r.title or "",
                r.body or "",
                gold_relevant,
                gold_material,
                notes,
            ])

            if gold_material == "":
                blank_gold_count += 1
            else:
                prefilled_gold += 1   # would indicate a bug

            # ── pipeline_output ───────────────────────────────────────────────
            flag_str = ("|".join(r.flag_reasons)
                        if r.flag_reasons else "")

            pw.writerow([
                pid,
                _f(r.direct_relevance),
                _f(r.materiality),
                _f(r.urgency),
                _f(r.credibility),
                f"{r.composite:.2f}"   if r.composite   is not None else "",
                _f(r.rule_floor),
                r.event_type or "",
                r.match_method or "",
                f"{r.match_score:.4f}" if r.match_score  is not None else "",
                _f(r.authority_rank),
                r.validation_status or "",
                flag_str,
                r.finbert_label or "",
                f"{r.finbert_score:.4f}" if r.finbert_score is not None else "",
            ])

            # ── flag_verdicts ── one row per flag, flagged/rejected pairs only ─
            if r.validation_status in ("flagged", "rejected") and r.flag_reasons:
                for flag in r.flag_reasons:
                    vw.writerow([pid, flag, ""])   # verdict blank — human fills
                    verdict_count += 1

    pair_count = len(rows)

    print(f"\n  Pairs written         : {pair_count:,}")
    print(f"  Flag-verdict rows     : {verdict_count:,}")

    # sanity check — gold_material must be blank for every row we just wrote
    sanity = "✓" if prefilled_gold == 0 else f"✗  {prefilled_gold} UNEXPECTED pre-fills"
    print(f"  gold_material blank   : {blank_gold_count:,} / {pair_count:,}  {sanity}")

    print(f"\n  Output directory  : {OUT_DIR}")
    for p in (label_path, pipeline_path, verdict_path):
        size_kb = p.stat().st_size / 1024
        print(f"    {p.name:<26}  {size_kb:6.1f} KB")
    print()


if __name__ == "__main__":
    main()
