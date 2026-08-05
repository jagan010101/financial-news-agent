"""
Print a plain-text validation health report.

    python -m scripts.calibration.validation_report           # last 7 days (default)
    python -m scripts.calibration.validation_report --days 30 # last 30 days
    python -m scripts.calibration.validation_report --days 0  # all time

Use this to eyeball what the coherence checks are catching before trusting them.
"""
from __future__ import annotations

import argparse


def _pct(n: int, total: int) -> str:
    return f"{100 * n / total:5.1f} %" if total else "    — "


def _bar(n: int, mx: int, width: int = 20) -> str:
    filled = round(width * n / mx) if mx else 0
    return "█" * filled + "░" * (width - filled)


def _trunc(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def main() -> None:
    ap = argparse.ArgumentParser(description="Validation health report")
    ap.add_argument("--days", type=int, default=7,
                    help="look-back window in days (0 = all time, default 7)")
    args = ap.parse_args()

    days = args.days if args.days > 0 else 36500  # 0 → ~100 years ≈ all time

    from finrag.calibration.validate_report import fetch_validation_summary
    s = fetch_validation_summary(days=days)

    window_label = f"last {args.days} days" if args.days > 0 else "all time"
    total = s["total_scored"]
    statuses = s["status_counts"]
    reasons = s["reason_counts"]
    flagged_rows = s["recent_flagged"]

    # ── header ───────────────────────────────────────────────────────────────
    print()
    print(f"  Validation Report ({window_label})")
    print(f"  {'─' * 54}")
    print(f"  Total scored (with status): {total:,}")
    print()

    # ── status breakdown ──────────────────────────────────────────────────────
    print("  STATUS BREAKDOWN")
    print(f"  {'─' * 54}")
    status_order = ["passed", "flagged", "rejected"]
    # show any unexpected statuses too
    all_statuses = status_order + [k for k in statuses if k not in status_order]
    mx_status = max(statuses.values(), default=1)
    for st in all_statuses:
        cnt = statuses.get(st, 0)
        if cnt == 0 and st not in statuses:
            continue
        bar = _bar(cnt, mx_status)
        print(f"  {st:<12}  {cnt:>6,}  {_pct(cnt, total)}  {bar}")
    print()

    # ── reason frequency ──────────────────────────────────────────────────────
    flagged_total = statuses.get("flagged", 0)
    if reasons:
        print("  FLAG REASON FREQUENCY")
        print(f"  {'─' * 54}")
        mx_reason = max(reasons.values(), default=1)
        for reason, cnt in reasons.items():
            bar = _bar(cnt, mx_reason)
            # show as % of flagged rows (a flagged row can have multiple reasons,
            # so this can exceed 100% in aggregate — that's expected and useful)
            pct = _pct(cnt, flagged_total)
            print(f"  {reason:<38}  {cnt:>4,}  {pct}  {bar}")
        print()
    else:
        print("  FLAG REASON FREQUENCY — no flagged rows in window")
        print()

    # ── recent flagged ────────────────────────────────────────────────────────
    if flagged_rows:
        print(f"  RECENT FLAGGED (showing {len(flagged_rows)} of {flagged_total:,})")
        print(f"  {'─' * 54}")
        # column widths
        W_DATE, W_HOLD, W_COMP, W_EVENT, W_TITLE = 16, 12, 5, 22, 36
        hdr = (f"  {'FETCHED':<{W_DATE}}  {'HOLDING':<{W_HOLD}}  "
               f"{'COMP':>{W_COMP}}  {'EVENT':<{W_EVENT}}  "
               f"{'TITLE':<{W_TITLE}}  REASONS")
        print(hdr)
        print(f"  {'─' * (len(hdr) - 2)}")
        for row in flagged_rows:
            reasons_str = ", ".join(row.reasons) if row.reasons else "—"
            fb = f" [{row.finbert_label}]" if row.finbert_label else ""
            print(
                f"  {row.fetched_at:<{W_DATE}}  "
                f"{_trunc(row.holding, W_HOLD):<{W_HOLD}}  "
                f"{row.composite:>{W_COMP}.1f}  "
                f"{_trunc(row.event_type or '—', W_EVENT):<{W_EVENT}}  "
                f"{_trunc(row.title, W_TITLE):<{W_TITLE}}  "
                f"{reasons_str}{fb}"
            )
        print()
    else:
        print("  RECENT FLAGGED — none in window")
        print()


if __name__ == "__main__":
    main()
