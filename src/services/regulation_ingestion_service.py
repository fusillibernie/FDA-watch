"""Orchestrates regulation change ingestion from all sources."""

import logging
from datetime import datetime, timezone

from src.models.enums import SourceType
from src.models.regulation import RegulationChange
from src.integrations.federal_register_client import fetch_federal_register
from src.integrations.fda_guidance_client import fetch_fda_guidance
from src.integrations.eurlex_client import fetch_eurlex_changes
from src.integrations.ifra_client import fetch_ifra_amendments
from src.services.regulation_search_service import RegulationSearchService
from src.services.source_preferences import SourcePreferencesService

logger = logging.getLogger(__name__)


class RegulationIngestionService:
    """Orchestrates fetching regulation changes from all sources."""

    def __init__(
        self,
        regulation_search: RegulationSearchService | None = None,
        preferences: SourcePreferencesService | None = None,
    ):
        self.search = regulation_search or RegulationSearchService()
        self.preferences = preferences or SourcePreferencesService()
        self._sync_state: dict = {}

    async def ingest_all(self, source: str | None = None) -> dict:
        """Run regulation ingestion pipeline.

        Args:
            source: Optional specific source to ingest
                    ("federal_register", "fda_guidance", "eurlex", "ifra").
                    If None, ingests from all enabled sources.

        Returns:
            Summary dict with counts.
        """
        all_changes: list[RegulationChange] = []
        summary: dict = {"sources": {}}

        if (source is None or source == "federal_register") and self.preferences.is_enabled(SourceType.FEDERAL_REGISTER):
            changes = await self._ingest_federal_register()
            all_changes.extend(changes)
            summary["sources"]["federal_register"] = len(changes)

        if (source is None or source == "fda_guidance") and self.preferences.is_enabled(SourceType.FDA_GUIDANCE):
            changes = await self._ingest_fda_guidance()
            all_changes.extend(changes)
            summary["sources"]["fda_guidance"] = len(changes)

        if (source is None or source == "eurlex") and self.preferences.is_enabled(SourceType.EU_OFFICIAL_JOURNAL):
            changes = await self._ingest_eurlex()
            all_changes.extend(changes)
            summary["sources"]["eurlex"] = len(changes)

        if (source is None or source == "ifra") and self.preferences.is_enabled(SourceType.IFRA_AMENDMENT):
            changes = await self._ingest_ifra()
            all_changes.extend(changes)
            summary["sources"]["ifra"] = len(changes)

        new_count = self.search.add_changes(all_changes)
        summary["new_changes"] = new_count

        logger.info("Regulation ingestion complete: %s", summary)
        return summary

    async def _ingest_federal_register(self) -> list[RegulationChange]:
        date_from = self._sync_state.get("federal_register_last_fetch")
        try:
            changes = await fetch_federal_register(date_from=date_from)
        except Exception as e:
            logger.error("Failed to fetch Federal Register: %s", e)
            return []

        self._sync_state["federal_register_last_fetch"] = (
            datetime.now(timezone.utc).strftime("%Y-%m-%d")
        )
        return changes

    async def _ingest_fda_guidance(self) -> list[RegulationChange]:
        date_from = self._sync_state.get("fda_guidance_last_fetch")
        try:
            changes = await fetch_fda_guidance(date_from=date_from)
        except Exception as e:
            logger.error("Failed to fetch FDA guidance: %s", e)
            return []

        self._sync_state["fda_guidance_last_fetch"] = (
            datetime.now(timezone.utc).strftime("%Y-%m-%d")
        )
        return changes

    async def _ingest_eurlex(self) -> list[RegulationChange]:
        date_from = self._sync_state.get("eurlex_last_fetch")
        try:
            changes = await fetch_eurlex_changes(date_from=date_from)
        except Exception as e:
            logger.error("Failed to fetch EUR-Lex: %s", e)
            return []

        self._sync_state["eurlex_last_fetch"] = (
            datetime.now(timezone.utc).strftime("%Y-%m-%d")
        )
        return changes

    async def _ingest_ifra(self) -> list[RegulationChange]:
        date_from = self._sync_state.get("ifra_last_fetch")
        try:
            changes = await fetch_ifra_amendments(date_from=date_from)
        except Exception as e:
            logger.error("Failed to fetch IFRA amendments: %s", e)
            return []

        self._sync_state["ifra_last_fetch"] = (
            datetime.now(timezone.utc).strftime("%Y-%m-%d")
        )
        return changes
