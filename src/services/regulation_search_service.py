"""Search, filter, and aggregation service for regulation changes."""

import json
import logging
import re
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from src.models.enums import ProductCategory, RegulationStage, SourceType
from src.models.regulation import RegulationChange

logger = logging.getLogger(__name__)

REGULATIONS_FILE = Path(__file__).parent.parent.parent / "data" / "regulations" / "changes.json"


class RegulationSearchService:
    """In-memory index of regulation changes with filtering and aggregation."""

    def __init__(self, changes_file: Path | None = None):
        self.changes_file = changes_file or REGULATIONS_FILE
        self._changes: list[RegulationChange] = []
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.reload()

    def reload(self) -> None:
        self._changes = []
        if self.changes_file.exists():
            try:
                with open(self.changes_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._changes = [RegulationChange(**c) for c in data]
            except (json.JSONDecodeError, IOError) as e:
                logger.error("Failed to load regulation changes: %s", e)
        self._loaded = True
        logger.info("Loaded %d regulation changes", len(self._changes))

    def save(self, changes: list[RegulationChange]) -> None:
        self.changes_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.changes_file, "w", encoding="utf-8") as f:
            json.dump([c.model_dump() for c in changes], f, indent=2)
        self._changes = changes
        self._loaded = True

    def add_changes(self, new_changes: list[RegulationChange]) -> int:
        """Merge new changes, deduplicating by source_id. Returns count added."""
        self._ensure_loaded()
        existing_ids = {c.source_id for c in self._changes}
        added = [c for c in new_changes if c.source_id not in existing_ids]
        if added:
            self._changes.extend(added)
            self.save(self._changes)
        return len(added)

    def get_change(self, change_id: str) -> RegulationChange | None:
        self._ensure_loaded()
        for c in self._changes:
            if c.id == change_id:
                return c
        return None

    def search(
        self,
        q: str | None = None,
        stage: RegulationStage | None = None,
        agency: str | None = None,
        category: ProductCategory | None = None,
        source: SourceType | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[RegulationChange], int]:
        self._ensure_loaded()
        results = self._changes

        if q:
            pattern = re.compile(re.escape(q), re.IGNORECASE)
            results = [
                c for c in results
                if pattern.search(c.title) or pattern.search(c.summary)
            ]

        if stage:
            results = [c for c in results if c.stage == stage]

        if agency:
            pattern = re.compile(re.escape(agency), re.IGNORECASE)
            results = [c for c in results if pattern.search(c.agency)]

        if category:
            results = [c for c in results if category in c.product_categories]

        if source:
            results = [c for c in results if c.source == source]

        if date_from:
            results = [c for c in results if c.date_published >= date_from]

        if date_to:
            results = [c for c in results if c.date_published <= date_to]

        results.sort(key=lambda c: c.date_published, reverse=True)
        total = len(results)
        return results[offset : offset + limit], total

    def stats(self) -> dict:
        self._ensure_loaded()

        now = datetime.now()
        seven_days_ago = (now - timedelta(days=7)).strftime("%Y-%m-%d")
        today = now.strftime("%Y-%m-%d")

        by_stage = Counter[str]()
        by_agency = Counter[str]()
        recent_count = 0
        open_comments = 0

        for c in self._changes:
            by_stage[c.stage.value] += 1
            by_agency[c.agency] += 1
            if c.date_published >= seven_days_ago:
                recent_count += 1
            if c.date_comments_close and c.date_comments_close >= today:
                open_comments += 1

        return {
            "total_changes": len(self._changes),
            "recent_7_days": recent_count,
            "open_comment_periods": open_comments,
            "by_stage": dict(by_stage),
            "by_agency": dict(by_agency),
        }
