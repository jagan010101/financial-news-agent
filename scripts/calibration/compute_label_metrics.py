"""
Compute calibration metrics from the three labelling CSVs.

    python -m scripts.calibration.compute_label_metrics

Reads (all read-only, no DB):
  labelling/label_sheet.csv
  labelling/pipeline_output.csv
  labelling/flag_verdicts.csv

Expected gold values (case-insensitive):
  gold_relevant  : 'yes' | 'no'
  gold_material  : 'material' | 'not_material' | 'borderline'
  verdict        : 'tp' | 'fp'  (also accepts true_positive / false_positive)

Guard: rows with blank gold_relevant OR gold_material are counted and excluded
from every section.

Section 1 — Resolver precision by match_method (uses gold_relevant).
Section 2 — Threshold P/R/F1 at 4.0, 5.0, 6.0 (gold_relevant='yes',
            gold_material in {material, not_material}).
Section 3 — Per-flag precision (flag_verdicts.csv, restricted to labelled pairs).
"""
from __future__ import annotations

import csv
import pathlib
import sys
from collections import defaultdict

LABEL_DIR  = pathlib.Path(__file__).parent.parent.parent / "labelling"
THRESHOLDS = [4.0, 5.0, 6.0]

# Canonical match_method display order (strongest → weakest expected precision)
_METHOD_ORDER = ["exact_id", "alias_exact", "alias_fuzzy", "sector"]

# Accepted verdict tokens (lower-cased)
_TP_TOKENS = {"tp", "true_positive", "true positive", "correct", "yes"}
_FP_TOKENS = {"fp", "false_positive", "false positive", "incorrect", "no", "wrong"}


# ── formatting helpers ─────────────────────────────────────────────────────────

def _n(s: str) -> str:
    """Strip + lower-case a CSV cell value."""
    return (s or "").strip().lower()


def _pct(num: int, den: int, width: int = 5) -> str:
    if den == 0:
        return " " * (width - 3) + "n/a"
    return f"{100 * num / den:{width}.1f}%"


def _rate(num: int, den: int) -> str:
    """'0.750 (3/4)' — always shows denominator."""
    if den == 0:
        return "n/a          "
    return f"{num / den:.3f} ({num}/{den})"


def _f1(prec: float | None, rec: float | None) -> str:
    if prec is None or rec is None or (prec + rec) == 0:
        return "  n/a"
    return f"{2 * prec * rec / (prec + rec):.3f}"


def _div(s: str = "", w: int = 62) -> None:
    print(f"  {s + '─' * (w - len(s))}")


# ── CSV loading ────────────────────────────────────────────────────────────────

def _load(name: str) -> list[dict]:
    path = LABEL_DIR / name
    if not path.exists():
        print(f"\n  ERROR: {path} not found — "
              f"run `python -m scripts.calibration.export_label_dataset` first.")
        sys.exit(1)
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    label_rows    = _load("label_sheet.csv")
    pipeline_rows = _load("pipeline_output.csv")
    verdict_rows  = _load("flag_verdicts.csv")

    # ── join label_sheet + pipeline_output on pair_id ─────────────────────────
    pipeline_by_id = {r["pair_id"]: r for r in pipeline_rows}

    joined: list[dict] = []
    for lr in label_rows:
        pid = lr["pair_id"]
        pr  = pipeline_by_id.get(pid, {})
        joined.append({**pr, **lr})   # label fields win on key collision

    total_pairs = len(joined)

    # ── guard: incomplete rows ─────────────────────────────────────────────────
    labelled: list[dict] = []
    n_blank_rel = n_blank_mat = 0
    for row in joined:
        gr = _n(row.get("gold_relevant", ""))
        gm = _n(row.get("gold_material", ""))
        if not gr:
            n_blank_rel += 1
        if not gm:
            n_blank_mat += 1
        if gr and gm:
            labelled.append(row)

    n_incomplete = total_pairs - len(labelled)

    print()
    print(f"  Label dataset: {total_pairs} total pairs")
    print(f"    blank gold_relevant : {n_blank_rel}")
    print(f"    blank gold_material : {n_blank_mat}")
    print(f"    incomplete (either) : {n_incomplete}  — excluded from all metrics")
    print(f"    Effective labelled N: {len(labelled)}")

    if not labelled:
        print("\n  Nothing labelled yet — fill the gold columns and re-run.")
        return

    labelled_ids = {r["pair_id"] for r in labelled}

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 1 — RESOLVER PRECISION BY match_method
    # ══════════════════════════════════════════════════════════════════════════
    print()
    print("  SECTION 1 — RESOLVER PRECISION (gold_relevant)")
    _div()

    method_total: dict[str, int] = defaultdict(int)
    method_yes:   dict[str, int] = defaultdict(int)

    for row in labelled:
        method = _n(row.get("match_method", "")) or "(unknown)"
        method_total[method] += 1
        if _n(row["gold_relevant"]) == "yes":
            method_yes[method] += 1

    seen = set(method_total)
    ordered = [m for m in _METHOD_ORDER if m in seen]
    ordered += sorted(m for m in seen if m not in _METHOD_ORDER)

    COL = 16
    print(f"  {'match_method':<{COL}}  {'n':>5}  {'relevant':>8}  "
          f"{'prec':>5}  {'precision (n/d)'}  note")
    _div()
    for m in ordered:
        tot  = method_total[m]
        yes  = method_yes[m]
        note = "⚠ n<5, do not conclude" if tot < 5 else ""
        print(f"  {m:<{COL}}  {tot:>5,}  {yes:>8,}  "
              f"{_pct(yes, tot):>5}  {_rate(yes, tot):<16}  {note}")

    _div()
    g_yes = sum(method_yes.values())
    g_tot = sum(method_total.values())
    print(f"  {'ALL':<{COL}}  {g_tot:>5,}  {g_yes:>8,}  "
          f"{_pct(g_yes, g_tot):>5}  {_rate(g_yes, g_tot)}")

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 2 — THRESHOLD PRECISION / RECALL / F1
    # ══════════════════════════════════════════════════════════════════════════
    print()
    print("  SECTION 2 — THRESHOLD PRECISION / RECALL")
    print("  Restricted to gold_relevant='yes', gold_material∈{material, not_material}")
    _div()

    # restrict to gold_relevant='yes'
    relevant = [r for r in labelled if _n(r["gold_relevant"]) == "yes"]
    n_borderline = sum(1 for r in relevant if _n(r.get("gold_material", "")) == "borderline")

    # strict subset: non-empty, non-borderline gold_material
    strict = [r for r in relevant
              if _n(r.get("gold_material", "")) not in ("", "borderline")]

    # collect any unexpected gold_material values so the user knows
    known_mat = {"material", "not_material"}
    unexpected = {_n(r.get("gold_material", "")) for r in strict} - known_mat
    if unexpected:
        print(f"  Note: unexpected gold_material values seen: "
              f"{', '.join(sorted(unexpected))} — treated as not_material")

    n_pos = sum(1 for r in strict if _n(r.get("gold_material", "")) == "material")
    n_neg = len(strict) - n_pos

    print(f"  gold_relevant='yes' pairs : {len(relevant)}")
    print(f"  borderline (excluded)     : {n_borderline}")
    print(f"  strict subset             : {len(strict)}"
          f"  ({n_pos} material, {n_neg} not_material, "
          f"base rate {_pct(n_pos, len(strict)).strip()})")
    print()

    if not strict:
        print("  Cannot compute threshold metrics — strict subset is empty.")
    else:
        # column header
        H1 = f"  {'thr':>5}  {'precision':>16}  {'recall':>14}  {'F1':>7}"
        H2 = f"{'TP':>5}{'FP':>5}{'FN':>5}{'TN':>5}"
        print(H1 + "  " + H2)
        _div()

        for thr in THRESHOLDS:
            tp = fp = fn = tn = 0
            for row in strict:
                gold = _n(row.get("gold_material", "")) == "material"
                try:
                    pred = float(row.get("composite") or 0) > thr
                except ValueError:
                    pred = False

                if     pred and     gold: tp += 1
                elif   pred and not gold: fp += 1
                elif not pred and   gold: fn += 1
                else:                     tn += 1

            prec = tp / (tp + fp) if (tp + fp) > 0 else None
            rec  = tp / (tp + fn) if (tp + fn) > 0 else None

            print(f"  {thr:>5.1f}  {_rate(tp, tp+fp):>16}  "
                  f"{_rate(tp, tp+fn):>14}  {_f1(prec, rec):>7}"
                  f"  {tp:>4}{fp:>5}{fn:>5}{tn:>5}")

        _div()
        print("  Threshold 5.0 is the current pipeline default.")
        print("  TP/FP/FN/TN counts are absolute — do not read rates alone.")

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 3 — PER-FLAG PRECISION (flag_verdicts.csv)
    # ══════════════════════════════════════════════════════════════════════════
    print()
    print("  SECTION 3 — PER-FLAG PRECISION")
    print("  Sorted worst-precision first (flags to tighten or disable)")
    print("  Restricted to labelled pairs; verdict='tp'|'fp'")
    _div()

    flag_tp:  dict[str, int] = defaultdict(int)
    flag_fp:  dict[str, int] = defaultdict(int)
    flag_unl: dict[str, int] = defaultdict(int)   # verdict blank / unrecognised
    n_skipped = 0   # verdict rows for non-labelled pairs

    for vrow in verdict_rows:
        pid    = vrow.get("pair_id", "").strip()
        flag   = vrow.get("flag_reason", "").strip()
        verdict = _n(vrow.get("verdict", ""))

        if not flag:
            continue
        if pid not in labelled_ids:
            n_skipped += 1
            continue

        if verdict in _TP_TOKENS:
            flag_tp[flag] += 1
        elif verdict in _FP_TOKENS:
            flag_fp[flag] += 1
        else:
            flag_unl[flag] += 1   # blank or unrecognised — not counted in precision

    if n_skipped:
        print(f"  ({n_skipped} flag-verdict rows skipped — pair not in labelled set)")
        print()

    all_flags = sorted(
        set(flag_tp) | set(flag_fp) | set(flag_unl)
    )

    if not all_flags:
        print("  No flag_verdicts rows — fill the verdict column and re-run.")
    else:
        def _sort_key(f: str) -> tuple:
            tp = flag_tp[f]
            fp = flag_fp[f]
            n  = tp + fp
            if n == 0:
                return (2, 0.0)               # no labelled verdicts → sort last
            return (1, tp / n)                # ascending precision (worst first)

        sorted_flags = sorted(all_flags, key=_sort_key)

        W = 40
        print(f"  {'flag_reason':<{W}}  {'n':>4}  {'TP':>4}  {'FP':>4}  "
              f"{'precision (tp/n)':>18}  {'unlab':>5}  note")
        _div()

        all_tp_sum = all_fp_sum = 0
        for flag in sorted_flags:
            tp  = flag_tp[flag]
            fp  = flag_fp[flag]
            unl = flag_unl[flag]
            n   = tp + fp
            all_tp_sum += tp
            all_fp_sum += fp

            note = "⚠ n<5, do not conclude" if n < 5 else ""
            prec_s = _rate(tp, n) if n > 0 else "n/a           "

            print(f"  {flag:<{W}}  {n:>4}  {tp:>4}  {fp:>4}  "
                  f"{prec_s:>18}  {unl:>5}  {note}")

        _div()
        all_n = all_tp_sum + all_fp_sum
        print(f"  {'ALL FLAGS':<{W}}  {all_n:>4}  {all_tp_sum:>4}  {all_fp_sum:>4}  "
              f"{_rate(all_tp_sum, all_n):>18}")

    print()


if __name__ == "__main__":
    main()
