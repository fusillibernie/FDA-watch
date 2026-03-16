"""Search, filter, and aggregation service for regulation changes (SQLite-backed)."""

import json
import logging
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from src.models.enums import ProductCategory, RegulationStage, SourceType
from src.models.regulation import RegulationChange
from src.services.database import init_db, change_to_row, row_to_change_dict

logger = logging.getLogger(__name__)

REGULATIONS_FILE = Path(__file__).parent.parent.parent / "data" / "regulations" / "changes.json"


class RegulationSearchService:
    """SQLite-backed index of regulation changes with filtering and aggregation."""

    def __init__(self, db_path: Path | None = None, changes_file: Path | None = None):
        self.changes_file = changes_file or REGULATIONS_FILE
        self._conn = init_db(db_path)
        self._migrated = False
        self._loaded = True  # backward compat for tests

    def _ensure_migrated(self) -> None:
        if self._migrated:
            return
        self._migrated = True
        count = self._conn.execute("SELECT COUNT(*) FROM regulation_changes").fetchone()[0]
        if count == 0 and self.changes_file.exists():
            self._migrate_from_json()

    def _migrate_from_json(self) -> None:
        try:
            with open(self.changes_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not data:
                return
            for item in data:
                row = change_to_row(item)
                self._upsert_change(row)
            self._conn.commit()
            logger.info("Migrated %d regulation changes from JSON to SQLite", len(data))
        except (json.JSONDecodeError, IOError) as e:
            logger.error("Failed to migrate regulation changes: %s", e)

    def _upsert_change(self, row: dict) -> None:
        cols = ["id", "source", "source_id", "title", "summary", "agency", "stage",
                "product_categories", "date_published", "date_effective",
                "date_comments_close", "jurisdiction", "url", "cfr_references", "raw_data"]
        row.setdefault("jurisdiction", "US")
        row.setdefault("date_effective", None)
        row.setdefault("date_comments_close", None)
        row.setdefault("url", None)
        row.setdefault("cfr_references", None)
        row.setdefault("raw_data", None)
        placeholders = ", ".join(f":{c}" for c in cols)
        col_names = ", ".join(cols)
        self._conn.execute(
            f"INSERT OR IGNORE INTO regulation_changes ({col_names}) VALUES ({placeholders})",
            {c: row.get(c) for c in cols},
        )

    def reload(self) -> None:
        self._migrated = False
        self._ensure_migrated()

    def save(self, changes: list[RegulationChange]) -> None:
        """Replace all changes (backward compat for tests)."""
        self._conn.execute("DELETE FROM regulation_changes")
        for c in changes:
            row = change_to_row(c.model_dump())
            self._upsert_change(row)
        self._conn.commit()

    def add_changes(self, new_changes: list[RegulationChange]) -> int:
        """Merge new changes, deduplicating by source_id. Returns count added."""
        self._ensure_migrated()
        added = 0
        for c in new_changes:
            row = change_to_row(c.model_dump())
            row.setdefault("jurisdiction", "US")
            row.setdefault("date_effective", None)
            row.setdefault("date_comments_close", None)
            row.setdefault("url", None)
            row.setdefault("cfr_references", None)
            row.setdefault("raw_data", None)
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO regulation_changes "
                "(id, source, source_id, title, summary, agency, stage, "
                "product_categories, date_published, date_effective, date_comments_close, "
                "jurisdiction, url, cfr_references, raw_data) "
                "VALUES (:id, :source, :source_id, :title, :summary, :agency, :stage, "
                ":product_categories, :date_published, :date_effective, :date_comments_close, "
                ":jurisdiction, :url, :cfr_references, :raw_data)",
                {c: row.get(c) for c in [
                    "id", "source", "source_id", "title", "summary", "agency", "stage",
                    "product_categories", "date_published", "date_effective",
                    "date_comments_close", "jurisdiction", "url", "cfr_references", "raw_data",
                ]},
            )
            if cursor.rowcount > 0:
                added += 1
        if added:
            self._conn.commit()
        return added

    def get_change(self, change_id: str) -> RegulationChange | None:
        self._ensure_migrated()
        row = self._conn.execute(
            "SELECT * FROM regulation_changes WHERE id = ?", (change_id,)
        ).fetchone()
        if not row:
            return None
        return RegulationChange(**row_to_change_dict(row))

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
        self._ensure_migrated()

        where_clauses: list[str] = []
        params: dict = {}

        if q:
            where_clauses.append("(title LIKE :q OR summary LIKE :q)")
            params["q"] = f"%{q}%"

        if stage:
            where_clauses.append("stage = :stage")
            params["stage"] = stage.value

        if agency:
            where_clauses.append("agency LIKE :agency")
            params["agency"] = f"%{agency}%"

        if category:
            where_clauses.append("product_categories LIKE :category")
            params["category"] = f'%"{category.value}"%'

        if source:
            where_clauses.append("source = :source")
            params["source"] = source.value

        if date_from:
            where_clauses.append("date_published >= :date_from")
            params["date_from"] = date_from

        if date_to:
            where_clauses.append("date_published <= :date_to")
            params["date_to"] = date_to

        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

        total = self._conn.execute(
            f"SELECT COUNT(*) FROM regulation_changes WHERE {where_sql}", params
        ).fetchone()[0]

        rows = self._conn.execute(
            f"SELECT * FROM regulation_changes WHERE {where_sql} "
            "ORDER BY date_published DESC LIMIT :limit OFFSET :offset",
            {**params, "limit": limit, "offset": offset},
        ).fetchall()

        results = [RegulationChange(**row_to_change_dict(r)) for r in rows]
        return results, total

    def stats(self) -> dict:
        self._ensure_migrated()

        total = self._conn.execute("SELECT COUNT(*) FROM regulation_changes").fetchone()[0]

        seven_days_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        today = datetime.now().strftime("%Y-%m-%d")

        recent = self._conn.execute(
            "SELECT COUNT(*) FROM regulation_changes WHERE date_published >= ?",
            (seven_days_ago,),
        ).fetchone()[0]

        open_comments = self._conn.execute(
            "SELECT COUNT(*) FROM regulation_changes WHERE date_comments_close >= ?",
            (today,),
        ).fetchone()[0]

        by_stage = Counter[str]()
        by_agency = Counter[str]()
        for row in self._conn.execute("SELECT stage, agency FROM regulation_changes"):
            by_stage[row[0]] += 1
            by_agency[row[1]] += 1

        return {
            "total_changes": total,
            "recent_7_days": recent,
            "open_comment_periods": open_comments,
            "by_stage": dict(by_stage),
            "by_agency": dict(by_agency),
        }
