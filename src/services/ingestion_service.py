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
from src.integrations.cpsc_client import fetch_cpsc_recalls
from src.integrations.prop65_client import fetch_prop65_notices
from src.integrations.nad_client import fetch_nad_decisions
from src.integrations.state_ag_client import fetch_state_ag_actions
from src.integrations.rapex_client import fetch_rapex_alerts
from src.integrations.rasff_client import fetch_rasff_notifications
from src.integrations.sccs_client import fetch_sccs_opinions
from src.integrations.echa_client import fetch_echa_substances
from src.models.enums import SourceType
from src.services.source_preferences import SourcePreferencesService

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
        preferences: SourcePreferencesService | None = None,
    ):
        self.search = search_service or SearchService()
        self.alerts = alert_service or AlertService()
        self.classifier = classifier or ViolationClassifier()
        self.api_key = api_key
        self.preferences = preferences or SourcePreferencesService()
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

        if (source is None or source == "openfda") and self.preferences.is_enabled(SourceType.OPENFDA_ENFORCEMENT):
            actions = await self._ingest_openfda()
            all_new_actions.extend(actions)
            summary["sources"]["openfda"] = len(actions)

        if (source is None or source == "warning_letters") and self.preferences.is_enabled(SourceType.FDA_WARNING_LETTER):
            actions = await self._ingest_warning_letters()
            all_new_actions.extend(actions)
            summary["sources"]["warning_letters"] = len(actions)

        if (source is None or source == "ftc") and self.preferences.is_enabled(SourceType.FTC_ACTION):
            actions = await self._ingest_ftc()
            all_new_actions.extend(actions)
            summary["sources"]["ftc"] = len(actions)

        if (source is None or source == "classaction") and self.preferences.is_enabled(SourceType.CLASS_ACTION):
            actions = await self._ingest_classaction()
            all_new_actions.extend(actions)
            summary["sources"]["classaction"] = len(actions)

        if (source is None or source == "cpsc") and self.preferences.is_enabled(SourceType.CPSC_RECALL):
            actions = await self._ingest_cpsc()
            all_new_actions.extend(actions)
            summary["sources"]["cpsc"] = len(actions)

        if (source is None or source == "prop65") and self.preferences.is_enabled(SourceType.PROP_65):
            actions = await self._ingest_prop65()
            all_new_actions.extend(actions)
            summary["sources"]["prop65"] = len(actions)

        if (source is None or source == "nad") and self.preferences.is_enabled(SourceType.NAD_DECISION):
            actions = await self._ingest_nad()
            all_new_actions.extend(actions)
            summary["sources"]["nad"] = len(actions)

        if (source is None or source == "state_ag") and self.preferences.is_enabled(SourceType.STATE_AG):
            actions = await self._ingest_state_ag()
            all_new_actions.extend(actions)
            summary["sources"]["state_ag"] = len(actions)

        if (source is None or source == "eu_rapex") and self.preferences.is_enabled(SourceType.EU_RAPEX):
            actions = await self._ingest_rapex()
            all_new_actions.extend(actions)
            summary["sources"]["eu_rapex"] = len(actions)

        if (source is None or source == "eu_rasff") and self.preferences.is_enabled(SourceType.EU_RASFF):
            actions = await self._ingest_rasff()
            all_new_actions.extend(actions)
            summary["sources"]["eu_rasff"] = len(actions)

        if (source is None or source == "eu_sccs") and self.preferences.is_enabled(SourceType.EU_SCCS):
            actions = await self._ingest_sccs()
            all_new_actions.extend(actions)
            summary["sources"]["eu_sccs"] = len(actions)

        if (source is None or source == "eu_echa") and self.preferences.is_enabled(SourceType.EU_ECHA_REACH):
            actions = await self._ingest_echa()
            all_new_actions.extend(actions)
            summary["sources"]["eu_echa"] = len(actions)

        # Classify all new actions
        self.classifier.classify_batch(all_new_actions)

        # Add to search index (deduplicates)
        new_count = self.search.add_actions(all_new_actions)
        summary["new_actions"] = new_count

        # Check alerts
        if all_new_actions:
            matches = await self.alerts.check_actions(all_new_actions)
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

    async def _ingest_cpsc(self) -> list[RegulatoryAction]:
        """Fetch CPSC product recalls."""
        date_from = self._sync_state.get("cpsc_last_fetch")
        try:
            actions = await fetch_cpsc_recalls(date_from=date_from)
        except Exception as e:
            logger.error("Failed to fetch CPSC recalls: %s", e)
            return []

        self._sync_state["cpsc_last_fetch"] = (
            datetime.now(timezone.utc).strftime("%Y-%m-%d")
        )
        return actions

    async def _ingest_prop65(self) -> list[RegulatoryAction]:
        """Fetch Prop 65 60-day notices."""
        date_from = self._sync_state.get("prop65_last_fetch")
        try:
            actions = await fetch_prop65_notices(date_from=date_from)
        except Exception as e:
            logger.error("Failed to fetch Prop 65 notices: %s", e)
            return []

        self._sync_state["prop65_last_fetch"] = (
            datetime.now(timezone.utc).strftime("%Y-%m-%d")
        )
        return actions

    async def _ingest_nad(self) -> list[RegulatoryAction]:
        """Fetch NAD advertising decisions."""
        date_from = self._sync_state.get("nad_last_fetch")
        try:
            actions = await fetch_nad_decisions(date_from=date_from)
        except Exception as e:
            logger.error("Failed to fetch NAD decisions: %s", e)
            return []

        self._sync_state["nad_last_fetch"] = (
            datetime.now(timezone.utc).strftime("%Y-%m-%d")
        )
        return actions

    async def _ingest_state_ag(self) -> list[RegulatoryAction]:
        """Fetch state AG enforcement actions."""
        date_from = self._sync_state.get("state_ag_last_fetch")
        try:
            actions = await fetch_state_ag_actions(date_from=date_from)
        except Exception as e:
            logger.error("Failed to fetch state AG actions: %s", e)
            return []

        self._sync_state["state_ag_last_fetch"] = (
            datetime.now(timezone.utc).strftime("%Y-%m-%d")
        )
        return actions

    async def _ingest_rapex(self) -> list[RegulatoryAction]:
        """Fetch EU Safety Gate (RAPEX) alerts."""
        date_from = self._sync_state.get("rapex_last_fetch")
        try:
            actions = await fetch_rapex_alerts(date_from=date_from)
        except Exception as e:
            logger.error("Failed to fetch RAPEX alerts: %s", e)
            return []

        self._sync_state["rapex_last_fetch"] = (
            datetime.now(timezone.utc).strftime("%Y-%m-%d")
        )
        return actions

    async def _ingest_rasff(self) -> list[RegulatoryAction]:
        """Fetch EU RASFF food/feed notifications."""
        date_from = self._sync_state.get("rasff_last_fetch")
        try:
            actions = await fetch_rasff_notifications(date_from=date_from)
        except Exception as e:
            logger.error("Failed to fetch RASFF notifications: %s", e)
            return []

        self._sync_state["rasff_last_fetch"] = (
            datetime.now(timezone.utc).strftime("%Y-%m-%d")
        )
        return actions

    async def _ingest_sccs(self) -> list[RegulatoryAction]:
        """Fetch EU SCCS cosmetic safety opinions."""
        date_from = self._sync_state.get("sccs_last_fetch")
        try:
            actions = await fetch_sccs_opinions(date_from=date_from)
        except Exception as e:
            logger.error("Failed to fetch SCCS opinions: %s", e)
            return []

        self._sync_state["sccs_last_fetch"] = (
            datetime.now(timezone.utc).strftime("%Y-%m-%d")
        )
        return actions

    async def _ingest_echa(self) -> list[RegulatoryAction]:
        """Fetch ECHA/REACH substance actions."""
        date_from = self._sync_state.get("echa_last_fetch")
        try:
            actions = await fetch_echa_substances(date_from=date_from)
        except Exception as e:
            logger.error("Failed to fetch ECHA substances: %s", e)
            return []

        self._sync_state["echa_last_fetch"] = (
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
