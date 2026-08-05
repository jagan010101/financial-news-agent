"""
finrag.orchestrate — sequence the full pipeline.

The four stages are independent and idempotent, so orchestration is just
ordered sequencing with per-stage error isolation: a failure in one stage is
logged and the run continues where it sensibly can (e.g. a flaky feed must not
block scoring of already-ingested items).

run_once() executes one full cycle and returns a structured summary. It is the
single entrypoint the scheduler calls and the one command you use to drive the
whole chain by hand.

Stages:
  1. ingest   — pull configured feeds (optionally a subset of sources)
  2. resolve  — link ingested articles to holdings (pre-filter)
  3. score    — judge resolved articles (needs LLM backend)
  4. report   — email the digest of above-threshold, unreported items

Flags let you skip stages (e.g. --no-score when no LLM key yet, or
--dry-run-report to avoid sending) so the chain is usable incrementally.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger("finrag.orchestrate")


@dataclass
class CycleSummary:
    ingest: dict | None = None
    resolve: dict | None = None
    score: dict | None = None
    report: dict | None = None
    errors: list[str] = field(default_factory=list)
    seconds: float = 0.0


def _stage(name: str, fn, summary: CycleSummary):
    """Run a stage, capturing its result or recording its error."""
    try:
        t0 = time.monotonic()
        result = fn()
        log.info("stage %s ok in %.1fs: %s", name, time.monotonic() - t0, result)
        return result
    except Exception as e:  # noqa: BLE001 — isolate stage failures
        msg = f"{name}: {type(e).__name__}: {e}"
        log.exception("stage %s FAILED", name)
        summary.errors.append(msg)
        return None


def run_once(*, sources: list[str] | None = None, do_score: bool = True,
             do_report: bool = True, dry_run_report: bool = False) -> CycleSummary:
    """Execute one full pipeline cycle. Returns a CycleSummary."""
    s = CycleSummary()
    t0 = time.monotonic()

    # 1. ingest -------------------------------------------------------------
    def _ingest():
        from finrag.ingest.pipeline import run_source
        from finrag.ingest.nse import NseAdapter
        from finrag.ingest.rbi import RbiAdapter
        from finrag.ingest.rss import DEFAULT_FEEDS, RssAdapter
        rss_feeds = DEFAULT_FEEDS
        if sources:
            wanted = {x.upper() for x in sources}
            rss_feeds = [f for f in rss_feeds if f.source_name in wanted]
        adapters = [RssAdapter(spec) for spec in rss_feeds]
        if not sources or "RBI" in {x.upper() for x in sources}:
            adapters.append(RbiAdapter())
        if not sources or "NSE_ANN" in {x.upper() for x in sources}:
            adapters.append(NseAdapter())
        agg = {"fetched": 0, "new": 0, "sources": 0, "errors": 0}
        for adapter in adapters:
            r = run_source(adapter)
            agg["sources"] += 1
            agg["fetched"] += r["fetched"]
            agg["new"] += r["new"]
            if r["error"]:
                agg["errors"] += 1
        return agg
    s.ingest = _stage("ingest", _ingest, s)

    # 2. resolve ------------------------------------------------------------
    def _resolve():
        from finrag.process.pipeline import run as resolve_run
        return resolve_run()
    s.resolve = _stage("resolve", _resolve, s)

    # 3. score --------------------------------------------------------------
    if do_score:
        def _score():
            from finrag.score.pipeline import run as score_run
            return score_run()
        s.score = _stage("score", _score, s)

    # 4. report -------------------------------------------------------------
    if do_report:
        def _report():
            from finrag.report.pipeline import run as report_run
            return report_run(dry_run=dry_run_report)
        s.report = _stage("report", _report, s)

    s.seconds = round(time.monotonic() - t0, 1)
    return s
