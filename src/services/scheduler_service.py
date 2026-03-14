"""Scheduled ingestion service using APScheduler."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent.parent / "data"
CONFIG_FILE = DATA_DIR / "scheduler_config.json"

DEFAULT_INTERVAL_HOURS = 6
MIN_INTERVAL_HOURS = 1
MAX_INTERVAL_HOURS = 24


class SchedulerService:
    """Wraps APScheduler for periodic data ingestion."""

    JOB_ID = "fda_watch_ingest"

    def __init__(self, ingest_callback=None, config_file: Path | None = None):
        self._scheduler = AsyncIOScheduler()
        self._ingest_callback = ingest_callback
        self._config_file = config_file or CONFIG_FILE
        self._config = self._load_config()

    def _load_config(self) -> dict:
        if self._config_file.exists():
            try:
                with open(self._config_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        return {"enabled": True, "interval_hours": DEFAULT_INTERVAL_HOURS}

    def _save_config(self) -> None:
        self._config_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._config_file, "w", encoding="utf-8") as f:
            json.dump(self._config, f, indent=2)

    async def _run_ingest(self) -> None:
        """Job callback — runs the ingestion pipeline."""
        logger.info("Scheduled ingestion starting")
        if self._ingest_callback:
            try:
                await self._ingest_callback()
            except Exception as e:
                logger.error("Scheduled ingestion failed: %s", e)

    def start(self) -> None:
        """Start the scheduler if enabled."""
        if not self._config.get("enabled", True):
            logger.info("Scheduler disabled, not starting")
            return

        hours = self._config.get("interval_hours", DEFAULT_INTERVAL_HOURS)
        self._scheduler.add_job(
            self._run_ingest,
            "interval",
            hours=hours,
            id=self.JOB_ID,
            replace_existing=True,
        )
        self._scheduler.start()
        logger.info("Scheduler started with %dh interval", hours)

    def stop(self) -> None:
        """Stop the scheduler."""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            self._scheduler = AsyncIOScheduler()
            logger.info("Scheduler stopped")

    def set_interval(self, hours: int) -> None:
        """Update the ingestion interval."""
        hours = max(MIN_INTERVAL_HOURS, min(MAX_INTERVAL_HOURS, hours))
        self._config["interval_hours"] = hours
        self._save_config()

        if self._scheduler.running:
            self._scheduler.reschedule_job(
                self.JOB_ID, trigger="interval", hours=hours
            )
            logger.info("Scheduler interval updated to %dh", hours)

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable the scheduler."""
        self._config["enabled"] = enabled
        self._save_config()

        if enabled and not self._scheduler.running:
            self.start()
        elif not enabled and self._scheduler.running:
            self.stop()

    def get_status(self) -> dict:
        """Return scheduler status info."""
        status = {
            "enabled": self._config.get("enabled", True),
            "interval_hours": self._config.get("interval_hours", DEFAULT_INTERVAL_HOURS),
            "running": self._scheduler.running,
            "next_run": None,
        }
        if self._scheduler.running:
            job = self._scheduler.get_job(self.JOB_ID)
            if job and job.next_run_time:
                status["next_run"] = job.next_run_time.isoformat()
        return status
