"""Worker entrypoint.

Cadence is deliberately unhurried. Nothing here needs to be minute-fresh, and
a source that blocks you is far more expensive than data that is two hours old
and honestly labelled as such.
"""

from __future__ import annotations

import logging
import os

from apscheduler.schedulers.blocking import BlockingScheduler

from .loop import MonitoringLoop

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
log = logging.getLogger("pokearb.worker")


def main() -> None:
    loop = MonitoringLoop(handlers={})  # handlers wired per enabled source
    scheduler = BlockingScheduler(timezone="UTC")

    scheduler.add_job(loop.run_cycle, "interval", hours=2, id="monitor",
                      max_instances=1, coalesce=True)
    scheduler.add_job(loop.run_cycle, "cron", hour=5, minute=0, id="daily",
                      max_instances=1, coalesce=True)

    log.info("worker started; no source handlers are wired until sources pass review")
    scheduler.start()


if __name__ == "__main__":
    main()
