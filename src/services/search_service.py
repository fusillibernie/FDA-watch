"""Search, filter, and aggregation service for regulatory actions (SQLite-backed)."""

import json
import logging
import re
import sqlite3
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from src.models.enums import FoodSubcategory, ProductCategory, Severity, SourceType, ViolationType
from src.models.enforcement import RegulatoryAction
from src.services.database import init_db, action_to_row, row_to_action_dict
from src.services.dedup_service import find_duplicates, _normalize_company

logger = logging.getLogger(__name__)

ACTIONS_FILE = Path(__file__).parent.parent.parent / "data" / "enforcement" / "actions.json"


class SearchService:
    """SQLite-backed index of regulatory actions with filtering and aggregation."""

    def __init__(self, db_path: Path | None = None, actions_file: Path | None = None):
        self.actions_file = actions_file or ACTIONS_FILE
        self._conn = init_db(db_path)
        self._migrated = False
        self._loaded = True  # backward compat for tests that set this

    def _ensure_migrated(self) -> None:
        """Auto-migrate from JSON file if DB is empty and JSON exists."""
        if self._migrated:
            return
        self._migrated = True
        count = self._conn.execute("SELECT COUNT(*) FROM actions").fetchone()[0]
        if count == 0 and self.actions_file.exists():
            self._migrate_from_json()

    def _migrate_from_json(self) -> None:
        """One-time migration from JSON file to SQLite."""
        try:
            with open(self.actions_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not data:
                return
            for item in data:
                row = action_to_row(item)
                self._upsert_action(row)
            self._conn.commit()
            logger.info("Migrated %d actions from JSON to SQLite", len(data))
        except (json.JSONDecodeError, IOError) as e:
            logger.error("Failed to migrate actions from JSON: %s", e)

    def _upsert_action(self, row: dict) -> None:
        """Insert or ignore a single action row."""
        cols = ["id", "source", "source_id", "title", "description", "company",
                "product_categories", "violation_types", "severity", "date",
                "jurisdiction", "url", "status", "distribution", "raw_data",
                "soi_metadata"]
        placeholders = ", ".join(f":{c}" for c in cols)
        col_names = ", ".join(cols)
        # Fill defaults for missing keys
        row.setdefault("jurisdiction", "US")
        row.setdefault("distribution", None)
        row.setdefault("raw_data", None)
        row.setdefault("soi_metadata", None)
        row.setdefault("url", None)
        row.setdefault("status", None)
        self._conn.execute(
            f"INSERT OR IGNORE INTO actions ({col_names}) VALUES ({placeholders})",
            {c: row.get(c) for c in cols},
        )

    def reload(self) -> None:
        """Re-migrate from JSON if needed (backward compat)."""
        self._migrated = False
        self._ensure_migrated()

    def save(self, actions: list[RegulatoryAction]) -> None:
        """Replace all actions in DB (backward compat for tests)."""
        self._conn.execute("DELETE FROM actions")
        for a in actions:
            row = action_to_row(a.model_dump())
            self._upsert_action(row)
        self._conn.commit()

    def add_actions(self, new_actions: list[RegulatoryAction]) -> int:
        """Merge new actions, deduplicating by source_id. Returns count of new records."""
        self._ensure_migrated()
        added = 0
        for a in new_actions:
            row = action_to_row(a.model_dump())
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO actions (id, source, source_id, title, description, "
                "company, product_categories, violation_types, severity, date, jurisdiction, "
                "url, status, distribution, raw_data, soi_metadata) "
                "VALUES (:id, :source, :source_id, :title, :description, :company, "
                ":product_categories, :violation_types, :severity, :date, :jurisdiction, "
                ":url, :status, :distribution, :raw_data, :soi_metadata)",
                {
                    "id": row.get("id"),
                    "source": row.get("source"),
                    "source_id": row.get("source_id"),
                    "title": row.get("title"),
                    "description": row.get("description", ""),
                    "company": row.get("company", ""),
                    "product_categories": row.get("product_categories", "[]"),
                    "violation_types": row.get("violation_types", "[]"),
                    "severity": row.get("severity"),
                    "date": row.get("date", ""),
                    "jurisdiction": row.get("jurisdiction", "US"),
                    "url": row.get("url"),
                    "status": row.get("status"),
                    "distribution": row.get("distribution"),
                    "raw_data": row.get("raw_data"),
                    "soi_metadata": row.get("soi_metadata"),
                },
            )
            if cursor.rowcount > 0:
                added += 1
        if added:
            self._conn.commit()
        return added

    def get_action(self, action_id: str) -> RegulatoryAction | None:
        self._ensure_migrated()
        row = self._conn.execute(
            "SELECT * FROM actions WHERE id = ?", (action_id,)
        ).fetchone()
        if not row:
            return None
        return RegulatoryAction(**row_to_action_dict(row))

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
        food_subcategory: FoodSubcategory | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[RegulatoryAction], int]:
        """Filter and search actions. Returns (results, total_count)."""
        self._ensure_migrated()

        where_clauses: list[str] = []
        params: dict = {}

        if q:
            where_clauses.append("(title LIKE :q OR description LIKE :q)")
            params["q"] = f"%{q}%"

        if category:
            where_clauses.append("product_categories LIKE :category")
            params["category"] = f'%"{category.value}"%'

        if violation_type:
            where_clauses.append("violation_types LIKE :violation_type")
            params["violation_type"] = f'%"{violation_type.value}"%'

        if severity:
            where_clauses.append("severity = :severity")
            params["severity"] = severity.value

        if source:
            where_clauses.append("source = :source")
            params["source"] = source.value

        if company:
            where_clauses.append("company LIKE :company")
            params["company"] = f"%{company}%"

        if date_from:
            where_clauses.append("date >= :date_from")
            params["date_from"] = date_from

        if date_to:
            where_clauses.append("date <= :date_to")
            params["date_to"] = date_to

        if food_subcategory:
            where_clauses.append("soi_metadata LIKE :food_subcategory")
            params["food_subcategory"] = f'%"food_subcategory": "{food_subcategory.value}"%'

        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

        count_row = self._conn.execute(
            f"SELECT COUNT(*) FROM actions WHERE {where_sql}", params
        ).fetchone()
        total = count_row[0]

        rows = self._conn.execute(
            f"SELECT * FROM actions WHERE {where_sql} ORDER BY date DESC LIMIT :limit OFFSET :offset",
            {**params, "limit": limit, "offset": offset},
        ).fetchall()

        results = [RegulatoryAction(**row_to_action_dict(r)) for r in rows]
        return results, total

    def stats(self) -> dict:
        """Aggregated statistics for the dashboard."""
        self._ensure_migrated()

        total = self._conn.execute("SELECT COUNT(*) FROM actions").fetchone()[0]

        seven_days_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        recent = self._conn.execute(
            "SELECT COUNT(*) FROM actions WHERE date >= ?", (seven_days_ago,)
        ).fetchone()[0]

        # Violation types — need to parse JSON arrays
        by_violation = Counter[str]()
        by_severity = Counter[str]()
        by_month = Counter[str]()
        by_company = Counter[str]()

        for row in self._conn.execute("SELECT violation_types, severity, date, company FROM actions"):
            try:
                vtypes = json.loads(row[0]) if row[0] else []
            except (json.JSONDecodeError, TypeError):
                vtypes = []
            for vt in vtypes:
                by_violation[vt] += 1
            by_severity[row[1]] += 1
            date_val = row[2] or ""
            if len(date_val) >= 7:
                by_month[date_val[:7]] += 1
            by_company[row[3]] += 1

        top_companies = dict(by_company.most_common(20))
        sorted_months = dict(sorted(by_month.items()))

        return {
            "total_actions": total,
            "recent_7_days": recent,
            "by_violation_type": dict(by_violation),
            "by_severity": dict(by_severity),
            "by_month": sorted_months,
            "top_companies": top_companies,
        }

    # --- Company profiles ---

    def company_profile(self, name: str) -> dict:
        """Build a profile for a company."""
        self._ensure_migrated()
        # Exact match first, then LIKE
        rows = self._conn.execute(
            "SELECT * FROM actions WHERE company = ?", (name,)
        ).fetchall()
        if not rows:
            rows = self._conn.execute(
                "SELECT * FROM actions WHERE company LIKE ?", (f"%{name}%",)
            ).fetchall()
        if not rows:
            return {"company": name, "total_actions": 0}

        matching = [RegulatoryAction(**row_to_action_dict(r)) for r in rows]

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
        self._ensure_migrated()
        if q:
            rows = self._conn.execute(
                "SELECT company, COUNT(*) as cnt FROM actions WHERE company LIKE ? "
                "GROUP BY company ORDER BY cnt DESC",
                (f"%{q}%",),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT company, COUNT(*) as cnt FROM actions "
                "GROUP BY company ORDER BY cnt DESC"
            ).fetchall()

        total = len(rows)
        page = rows[offset: offset + limit]
        return [{"company": r[0], "action_count": r[1]} for r in page], total

    # --- Trends ---

    def trends(self, months: int = 6) -> dict:
        """Violation count trends with month-over-month changes."""
        self._ensure_migrated()

        by_month = Counter[str]()
        by_viol_month: dict[str, Counter[str]] = {}

        for row in self._conn.execute("SELECT date, violation_types FROM actions"):
            date_val = row[0] or ""
            if len(date_val) >= 7:
                month_key = date_val[:7]
                by_month[month_key] += 1
                try:
                    vtypes = json.loads(row[1]) if row[1] else []
                except (json.JSONDecodeError, TypeError):
                    vtypes = []
                for vt in vtypes:
                    by_viol_month.setdefault(month_key, Counter())[vt] += 1

        month_keys = sorted(by_month.keys())
        recent = month_keys[-months:] if len(month_keys) >= months else month_keys

        monthly_counts = [{"month": m, "count": by_month[m]} for m in recent]

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
        self._ensure_migrated()
        action = self.get_action(action_id)
        if not action:
            return []

        # Find by same company + similar date range
        rows = self._conn.execute(
            "SELECT * FROM actions WHERE company = ? AND id != ? ORDER BY date DESC LIMIT 10",
            (action.company, action_id),
        ).fetchall()
        return [RegulatoryAction(**row_to_action_dict(r)) for r in rows]
