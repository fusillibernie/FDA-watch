"""Tests for SQLite database module."""

import tempfile
from pathlib import Path

from src.services.database import init_db, action_to_row, row_to_action_dict


def test_init_db_creates_tables():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        conn = init_db(db_path)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row["name"] for row in cursor.fetchall()}
        assert "actions" in tables
        assert "alert_rules" in tables
        assert "alert_matches" in tables
        assert "sync_state" in tables
        assert "warning_letters" in tables
        conn.close()


def test_init_db_idempotent():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        conn1 = init_db(db_path)
        conn1.close()
        conn2 = init_db(db_path)
        cursor = conn2.execute("SELECT count(*) as cnt FROM sqlite_master WHERE type='table'")
        assert cursor.fetchone()["cnt"] >= 5
        conn2.close()


def test_action_to_row():
    action = {
        "id": "test-1",
        "source": "openfda_enforcement",
        "source_id": "R-001",
        "title": "Test",
        "description": "Desc",
        "company": "Acme",
        "product_categories": ["food"],
        "violation_types": ["adulteration"],
        "severity": "class_i",
        "date": "2026-01-15",
        "url": None,
        "status": None,
        "distribution": None,
        "raw_data": {"key": "value"},
    }
    row = action_to_row(action)
    assert row["product_categories"] == '["food"]'
    assert row["violation_types"] == '["adulteration"]'
    assert '"key"' in row["raw_data"]


def test_row_to_action_dict():
    import sqlite3
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        conn = init_db(db_path)
        conn.execute(
            """INSERT INTO actions
            (id, source, source_id, title, description, company,
             product_categories, violation_types, severity, date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("t1", "openfda_enforcement", "R-001", "Test", "Desc", "Acme",
             '["food"]', '["adulteration"]', "class_i", "2026-01-15"),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM actions WHERE id='t1'").fetchone()
        d = row_to_action_dict(row)
        assert d["product_categories"] == ["food"]
        assert d["violation_types"] == ["adulteration"]
        conn.close()


def test_insert_and_query_actions():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        conn = init_db(db_path)
        conn.execute(
            """INSERT INTO actions
            (id, source, source_id, title, description, company,
             product_categories, violation_types, severity, date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("a1", "openfda_enforcement", "R-001", "Recall 1", "Desc",
             "Acme Inc.", '["food"]', '["adulteration"]', "class_i", "2026-01-15"),
        )
        conn.commit()

        rows = conn.execute("SELECT * FROM actions WHERE company LIKE ?", ("%Acme%",)).fetchall()
        assert len(rows) == 1
        assert rows[0]["title"] == "Recall 1"
        conn.close()


def test_source_id_unique():
    """Duplicate source_id should be rejected."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        conn = init_db(db_path)
        conn.execute(
            """INSERT INTO actions
            (id, source, source_id, title, description, company,
             product_categories, violation_types, severity, date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("a1", "openfda_enforcement", "R-001", "Recall", "Desc",
             "Acme", '[]', '[]', "class_i", "2026-01-15"),
        )
        conn.commit()

        # Same source_id with different id should fail on INSERT OR IGNORE
        conn.execute(
            """INSERT OR IGNORE INTO actions
            (id, source, source_id, title, description, company,
             product_categories, violation_types, severity, date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("a2", "openfda_enforcement", "R-001", "Duplicate", "Desc",
             "Acme", '[]', '[]', "class_i", "2026-01-15"),
        )
        conn.commit()

        count = conn.execute("SELECT count(*) as cnt FROM actions").fetchone()["cnt"]
        assert count == 1
        conn.close()
