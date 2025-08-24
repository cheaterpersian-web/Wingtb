from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
import logging


logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(self) -> None:
        self.scheduler = AsyncIOScheduler()

    def start(self) -> None:
        if not self.scheduler.running:
            self.scheduler.start()
            logger.info("Scheduler started")

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("Scheduler stopped")

    def add_interval_job(self, func, seconds: int, *, id: str | None = None) -> None:
        self.scheduler.add_job(func, trigger=IntervalTrigger(seconds=seconds), id=id, replace_existing=True)

