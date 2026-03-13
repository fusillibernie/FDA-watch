"""Orchestrates data fetching, classification, and alerting."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from src.models.enforcement import RegulatoryAction, WarningLetterMeta
from src.services.openfda_client import fetch_enforcement
from src.services.warning_letter_client import fetch_warning_letters
from src.services.classifier import ViolationClassifier
from src.services.alert_service import AlertService
from src.services.search_service import SearchService
from src.integrations.ftc_client import fetch_ftc_cases
from src.integrations.classaction_client import fetch_classaction_lawsuits

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent.parent / "data"
SYNC_STATE_FILE = DATA_DIR / "sync_state.json"
LETTERS_FILE = DATA_DIR / "warning_letters" / "letters.json"


class IngestionService:
    """Orchestrates the full ingest pipeline: fetch → classify → alert."""

    def __init__(
        self,
        search_service: SearchService | None = None,
        alert_service: AlertService | None = None,
        classifier: ViolationClassifier | None = None,
        api_key: str | None = None,
    ):
        self.search = search_service or SearchService()
        self.alerts = alert_service or AlertService()
        self.classifier = classifier or ViolationClassifier()
        self.api_key = api_key
        self._sync_state = self._load_sync_state()

    def _load_sync_state(self) -> dict:
        if SYNC_STATE_FILE.exists():
            try:
                with open(SYNC_STATE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        return {}

    def _save_sync_state(self) -> None:
        SYNC_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(SYNC_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(self._sync_state, f, indent=2)

    def get_status(self) -> dict:
        """Return sync status info."""
        self.search._ensure_loaded()
        return {
            "sync_state": self._sync_state,
            "total_actions": len(self.search._actions),
        }

    async def ingest_all(self, source: str | None = None) -> dict:
        """Run full ingestion pipeline.

        Args:
            source: Optional specific source to ingest
                    ("openfda", "warning_letters", "ftc", "classaction").
                    If None, ingests from all sources.

        Returns:
            Summary dict with counts of new records and matches.
        """
        all_new_actions: list[RegulatoryAction] = []
        summary: dict = {"sources": {}}

        if source is None or source == "openfda":
            actions = await self._ingest_openfda()
            all_new_actions.extend(actions)
            summary["sources"]["openfda"] = len(actions)

        if source is None or source == "warning_letters":
            actions = await self._ingest_warning_letters()
            all_new_actions.extend(actions)
            summary["sources"]["warning_letters"] = len(actions)

        if source is None or source == "ftc":
            actions = await self._ingest_ftc()
            all_new_actions.extend(actions)
            summary["sources"]["ftc"] = len(actions)

        if source is None or source == "classaction":
            actions = await self._ingest_classaction()
            all_new_actions.extend(actions)
            summary["sources"]["classaction"] = len(actions)

        # Classify all new actions
        self.classifier.classify_batch(all_new_actions)

        # Add to search index (deduplicates)
        new_count = self.search.add_actions(all_new_actions)
        summary["new_actions"] = new_count

        # Check alerts
        if all_new_actions:
            matches = self.alerts.check_actions(all_new_actions)
            summary["new_alert_matches"] = len(matches)
        else:
            summary["new_alert_matches"] = 0

        self._save_sync_state()
        logger.info("Ingestion complete: %s", summary)
        return summary

    async def _ingest_openfda(self) -> list[RegulatoryAction]:
        """Fetch from both food and drug enforcement endpoints."""
        date_from = self._sync_state.get("openfda_last_fetch")
        # Convert ISO date to YYYYMMDD for openFDA
        openfda_date = None
        if date_from:
            try:
                openfda_date = date_from.replace("-", "")
            except (ValueError, AttributeError):
                pass

        all_actions: list[RegulatoryAction] = []
        for endpoint in ("food", "drug"):
            try:
                actions = await fetch_enforcement(
                    endpoint=endpoint,
                    date_from=openfda_date,
                    api_key=self.api_key,
                )
                all_actions.extend(actions)
            except Exception as e:
                logger.error("Failed to fetch openFDA/%s: %s", endpoint, e)

        self._sync_state["openfda_last_fetch"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return all_actions

    async def _ingest_warning_letters(self) -> list[RegulatoryAction]:
        """Fetch and parse FDA warning letters XML."""
        date_from = self._sync_state.get("warning_letters_last_fetch")

        try:
            letters, actions = await fetch_warning_letters(date_from=date_from)
        except Exception as e:
            logger.error("Failed to fetch warning letters: %s", e)
            return []

        # Save letter metadata
        if letters:
            self._save_letters(letters)

        self._sync_state["warning_letters_last_fetch"] = (
            datetime.now(timezone.utc).strftime("%Y-%m-%d")
        )
        return actions

    async def _ingest_ftc(self) -> list[RegulatoryAction]:
        """Fetch FTC enforcement cases from ftc.gov."""
        date_from = self._sync_state.get("ftc_last_fetch")
        try:
            actions = await fetch_ftc_cases(date_from=date_from)
        except Exception as e:
            logger.error("Failed to fetch FTC cases: %s", e)
            return []

        self._sync_state["ftc_last_fetch"] = (
            datetime.now(timezone.utc).strftime("%Y-%m-%d")
        )
        return actions

    async def _ingest_classaction(self) -> list[RegulatoryAction]:
        """Fetch class action lawsuits."""
        date_from = self._sync_state.get("classaction_last_fetch")
        try:
            actions = await fetch_classaction_lawsuits(date_from=date_from)
        except Exception as e:
            logger.error("Failed to fetch class actions: %s", e)
            return []

        self._sync_state["classaction_last_fetch"] = (
            datetime.now(timezone.utc).strftime("%Y-%m-%d")
        )
        return actions

    def _save_letters(self, new_letters: list[WarningLetterMeta]) -> None:
        """Merge and save warning letter metadata."""
        LETTERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        existing: list[dict] = []
        if LETTERS_FILE.exists():
            try:
                with open(LETTERS_FILE, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            except (json.JSONDecodeError, IOError):
                pass

        existing_ids = {e.get("letter_id") for e in existing}
        for letter in new_letters:
            if letter.letter_id not in existing_ids:
                existing.append(letter.model_dump())

        with open(LETTERS_FILE, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2)
