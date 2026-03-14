"""Search, filter, and aggregation service for regulatory actions."""

import json
import logging
import re
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from src.models.enums import ProductCategory, Severity, SourceType, ViolationType
from src.models.enforcement import RegulatoryAction
from src.services.dedup_service import find_duplicates, _normalize_company

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

    # --- Company profiles ---

    def company_profile(self, name: str) -> dict:
        """Build a profile for a company with fuzzy matching."""
        self._ensure_loaded()
        normalized = _normalize_company(name)

        matching = [
            a for a in self._actions
            if _normalize_company(a.company) == normalized
        ]
        if not matching:
            # Fallback to substring match
            pattern = re.compile(re.escape(name), re.IGNORECASE)
            matching = [a for a in self._actions if pattern.search(a.company)]

        if not matching:
            return {"company": name, "total_actions": 0}

        by_violation = Counter[str]()
        by_severity = Counter[str]()
        by_source = Counter[str]()
        dates: list[str] = []

        for a in matching:
            for vt in a.violation_types:
                by_violation[vt.value] += 1
            by_severity[a.severity.value] += 1
            by_source[a.source.value] += 1
            if a.date:
                dates.append(a.date)

        dates.sort()
        return {
            "company": matching[0].company,
            "total_actions": len(matching),
            "violation_distribution": dict(by_violation),
            "severity_distribution": dict(by_severity),
            "source_breakdown": dict(by_source),
            "date_range": {
                "earliest": dates[0] if dates else None,
                "latest": dates[-1] if dates else None,
            },
            "actions": [a.model_dump() for a in sorted(matching, key=lambda x: x.date, reverse=True)],
        }

    def list_companies(
        self, q: str | None = None, offset: int = 0, limit: int = 50
    ) -> tuple[list[dict], int]:
        """List companies with action counts."""
        self._ensure_loaded()
        company_counts = Counter[str]()
        for a in self._actions:
            company_counts[a.company] += 1

        items = company_counts.most_common()
        if q:
            pattern = re.compile(re.escape(q), re.IGNORECASE)
            items = [(name, count) for name, count in items if pattern.search(name)]

        total = len(items)
        page = items[offset: offset + limit]
        return [{"company": name, "action_count": count} for name, count in page], total

    # --- Trends ---

    def trends(self, months: int = 6) -> dict:
        """Violation count trends with month-over-month changes."""
        self._ensure_loaded()
        now = datetime.now()

        by_month = Counter[str]()
        by_viol_month: dict[str, Counter[str]] = {}

        for a in self._actions:
            if len(a.date) >= 7:
                month_key = a.date[:7]
                by_month[month_key] += 1
                for vt in a.violation_types:
                    by_viol_month.setdefault(month_key, Counter())[vt.value] += 1

        # Get last N months
        month_keys = sorted(by_month.keys())
        recent = month_keys[-months:] if len(month_keys) >= months else month_keys

        monthly_counts = [{"month": m, "count": by_month[m]} for m in recent]

        # Month-over-month changes
        mom_changes = []
        for i in range(1, len(monthly_counts)):
            prev = monthly_counts[i - 1]["count"]
            curr = monthly_counts[i]["count"]
            change = ((curr - prev) / prev * 100) if prev > 0 else 0
            mom_changes.append({
                "month": monthly_counts[i]["month"],
                "count": curr,
                "change_pct": round(change, 1),
            })

        # Emerging patterns: violation types with >50% increase in last 2 vs prior 4 months
        emerging = []
        if len(recent) >= 3:
            split = max(1, len(recent) - 2)
            prior_months = recent[:split]
            recent_months = recent[split:]

            all_vtypes: set[str] = set()
            for m in recent:
                if m in by_viol_month:
                    all_vtypes.update(by_viol_month[m].keys())

            for vtype in all_vtypes:
                prior_sum = sum(by_viol_month.get(m, Counter()).get(vtype, 0) for m in prior_months)
                recent_sum = sum(by_viol_month.get(m, Counter()).get(vtype, 0) for m in recent_months)

                prior_avg = prior_sum / len(prior_months) if prior_months else 0
                recent_avg = recent_sum / len(recent_months) if recent_months else 0

                if prior_avg > 0 and recent_avg > prior_avg * 1.5:
                    increase = ((recent_avg - prior_avg) / prior_avg) * 100
                    emerging.append({
                        "violation_type": vtype,
                        "prior_avg": round(prior_avg, 1),
                        "recent_avg": round(recent_avg, 1),
                        "increase_pct": round(increase, 1),
                    })

            emerging.sort(key=lambda x: x["increase_pct"], reverse=True)

        return {
            "monthly_counts": monthly_counts,
            "mom_changes": mom_changes,
            "emerging_patterns": emerging[:10],
        }

    # --- Duplicate/related actions ---

    def get_related(self, action_id: str) -> list[RegulatoryAction]:
        """Find actions related/duplicate to the given action."""
        self._ensure_loaded()
        action = self.get_action(action_id)
        if not action:
            return []

        groups = find_duplicates(self._actions)
        for group in groups:
            if action_id in group:
                return [
                    a for a in self._actions
                    if a.id in group and a.id != action_id
                ]
        return []
