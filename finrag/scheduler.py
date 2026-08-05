"""
finrag.scheduler — unattended scheduling via APScheduler.

Keeps it simple and reason-able rather than per-source crontabs:

  * FAST cycle  : ingest + resolve + score + report on the frequent cadence.
    During market hours you want exchange/news quickly. Default every 15 min.
  * SLOW cycle  : same full chain but intended for low-frequency regulator
    feeds; in practice the FAST cycle already polls everything, so SLOW is
    mainly a safety re-run + off-hours heartbeat. Default hourly.

Because every stage is idempotent, overlapping or extra runs are harmless
(dedup + already-reported guards). A single BlockingScheduler runs in the
foreground; on cloud, run this under systemd / a process manager, or invoke
run_once from an external cron and skip the scheduler entirely.

Cost note (local vs cloud): self-hosting a 24/7 box that holds a 7B judge
costs money. The batch design means you can instead run run_once from cron a
few times a day on a tiny box (or your laptop), calling a free-tier API judge,
and pay nothing. Prefer that unless you need minute-level latency.
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler

from finrag.orchestrate import run_once

log = logging.getLogger("finrag.scheduler")


def _fast_cycle():
    s = run_once()
    log.info("fast cycle done in %.1fs errors=%d", s.seconds, len(s.errors))


def _slow_cycle():
    s = run_once()
    log.info("slow cycle done in %.1fs errors=%d", s.seconds, len(s.errors))


def build_scheduler(fast_minutes: int = 15, slow_minutes: int = 60
                    ) -> BlockingScheduler:
    sched = BlockingScheduler(timezone="Asia/Kolkata")
    # coalesce: if the app was asleep, run once not N times; max_instances=1:
    # never overlap a cycle with itself.
    sched.add_job(_fast_cycle, "interval", minutes=fast_minutes,
                  id="fast", coalesce=True, max_instances=1)
    sched.add_job(_slow_cycle, "interval", minutes=slow_minutes,
                  id="slow", coalesce=True, max_instances=1)
    return sched


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sched = build_scheduler()
    log.info("finrag scheduler starting (Ctrl-C to stop)")
    # run one cycle immediately so you're not waiting for the first interval
    _fast_cycle()
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("scheduler stopped")


if __name__ == "__main__":
    main()
