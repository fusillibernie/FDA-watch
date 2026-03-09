"""Search, filter, and aggregation service for regulatory actions."""

import json
import logging
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from src.models.enums import ProductCategory, Severity, SourceType, ViolationType
from src.models.enforcement import RegulatoryAction

logger = logging.getLogger(__name__)

ACTIONS_FILE = Path(__file__).parent.parent.parent / "data" / "enforcement" / "actions.json"


class SearchService:
    """In-memory index of regulatory actions with filtering and aggregation."""

    def __init__(self, actions_file: Path | None = None):
        self.actions_file = actions_file or ACTIONS_FILE
        self._actions: list[RegulatoryAction] = []
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.reload()

    def reload(self) -> None:
        """Load or reload actions from disk."""
        self._actions = []
        if self.actions_file.exists():
            try:
                with open(self.actions_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._actions = [RegulatoryAction(**a) for a in data]
            except (json.JSONDecodeError, IOError) as e:
                logger.error("Failed to load actions: %s", e)
        self._loaded = True
        logger.info("Loaded %d regulatory actions", len(self._actions))

    def save(self, actions: list[RegulatoryAction]) -> None:
        """Save actions to disk and update in-memory index."""
        self.actions_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.actions_file, "w", encoding="utf-8") as f:
            json.dump([a.model_dump() for a in actions], f, indent=2)
        self._actions = actions
        self._loaded = True

    def add_actions(self, new_actions: list[RegulatoryAction]) -> int:
        """Merge new actions, deduplicating by source_id. Returns count of new records."""
        self._ensure_loaded()
        existing_ids = {a.source_id for a in self._actions}
        added = [a for a in new_actions if a.source_id not in existing_ids]
        if added:
            self._actions.extend(added)
            self.save(self._actions)
        return len(added)

    def get_action(self, action_id: str) -> RegulatoryAction | None:
        self._ensure_loaded()
        for a in self._actions:
            if a.id == action_id:
                return a
        return None

    def search(
        self,
        q: str | None = None,
        category: ProductCategory | None = None,
        violation_type: ViolationType | None = None,
        severity: Severity | None = None,
        source: SourceType | None = None,
        company: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[RegulatoryAction], int]:
        """Filter and search actions. Returns (results, total_count)."""
        self._ensure_loaded()
        results = self._actions

        if q:
            pattern = re.compile(re.escape(q), re.IGNORECASE)
            results = [
                a for a in results
                if pattern.search(a.title) or pattern.search(a.description)
            ]

        if category:
            results = [a for a in results if category in a.product_categories]

        if violation_type:
            results = [a for a in results if violation_type in a.violation_types]

        if severity:
            results = [a for a in results if a.severity == severity]

        if source:
            results = [a for a in results if a.source == source]

        if company:
            pattern = re.compile(re.escape(company), re.IGNORECASE)
            results = [a for a in results if pattern.search(a.company)]

        if date_from:
            results = [a for a in results if a.date >= date_from]

        if date_to:
            results = [a for a in results if a.date <= date_to]

        # Sort by date descending
        results.sort(key=lambda a: a.date, reverse=True)

        total = len(results)
        return results[offset : offset + limit], total

    def stats(self) -> dict:
        """Aggregated statistics for the dashboard."""
        self._ensure_loaded()

        now = datetime.now()
        seven_days_ago = (now.replace(hour=0, minute=0, second=0, microsecond=0)).__format__("%Y-%m-%d")
        from datetime import timedelta
        seven_days_ago = (now - timedelta(days=7)).strftime("%Y-%m-%d")

        by_violation = Counter[str]()
        by_severity = Counter[str]()
        by_month = Counter[str]()
        by_company = Counter[str]()
        recent_count = 0

        for a in self._actions:
            for vt in a.violation_types:
                by_violation[vt.value] += 1
            by_severity[a.severity.value] += 1
            if len(a.date) >= 7:
                by_month[a.date[:7]] += 1
            by_company[a.company] += 1
            if a.date >= seven_days_ago:
                recent_count += 1

        # Top 20 companies
        top_companies = dict(by_company.most_common(20))

        # Sort months chronologically
        sorted_months = dict(sorted(by_month.items()))

        return {
            "total_actions": len(self._actions),
            "recent_7_days": recent_count,
            "by_violation_type": dict(by_violation),
            "by_severity": dict(by_severity),
            "by_month": sorted_months,
            "top_companies": top_companies,
        }
