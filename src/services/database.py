"""SQLite database for FDA-watch persistent storage."""

import json
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent.parent / "data"
DB_PATH = DATA_DIR / "fda_watch.db"


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Get a SQLite connection with row factory."""
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: Path | None = None) -> sqlite3.Connection:
    """Create tables if they don't exist. Returns the connection."""
    conn = get_connection(db_path)

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS actions (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            source_id TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            company TEXT NOT NULL DEFAULT '',
            product_categories TEXT NOT NULL DEFAULT '[]',
            violation_types TEXT NOT NULL DEFAULT '[]',
            severity TEXT NOT NULL,
            date TEXT NOT NULL DEFAULT '',
            jurisdiction TEXT NOT NULL DEFAULT 'US',
            url TEXT,
            status TEXT,
            distribution TEXT,
            raw_data TEXT,
            soi_metadata TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_actions_source ON actions(source);
        CREATE INDEX IF NOT EXISTS idx_actions_date ON actions(date);
        CREATE INDEX IF NOT EXISTS idx_actions_company ON actions(company);
        CREATE INDEX IF NOT EXISTS idx_actions_source_id ON actions(source_id);
        CREATE INDEX IF NOT EXISTS idx_actions_severity ON actions(severity);
        CREATE INDEX IF NOT EXISTS idx_actions_jurisdiction ON actions(jurisdiction);

        CREATE TABLE IF NOT EXISTS regulation_changes (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            source_id TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            agency TEXT NOT NULL DEFAULT '',
            stage TEXT NOT NULL,
            product_categories TEXT NOT NULL DEFAULT '[]',
            date_published TEXT NOT NULL DEFAULT '',
            date_effective TEXT,
            date_comments_close TEXT,
            jurisdiction TEXT NOT NULL DEFAULT 'US',
            url TEXT,
            cfr_references TEXT,
            raw_data TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_reg_source ON regulation_changes(source);
        CREATE INDEX IF NOT EXISTS idx_reg_date ON regulation_changes(date_published);
        CREATE INDEX IF NOT EXISTS idx_reg_agency ON regulation_changes(agency);
        CREATE INDEX IF NOT EXISTS idx_reg_stage ON regulation_changes(stage);
        CREATE INDEX IF NOT EXISTS idx_reg_source_id ON regulation_changes(source_id);

        CREATE TABLE IF NOT EXISTS alert_rules (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            keywords TEXT NOT NULL DEFAULT '[]',
            product_categories TEXT,
            sources TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            webhook_url TEXT
        );

        CREATE TABLE IF NOT EXISTS alert_matches (
            id TEXT PRIMARY KEY,
            alert_rule_id TEXT NOT NULL,
            action_id TEXT,
            regulation_change_id TEXT,
            matched_keywords TEXT NOT NULL DEFAULT '[]',
            matched_at TEXT NOT NULL,
            read INTEGER NOT NULL DEFAULT 0,
            UNIQUE(alert_rule_id, action_id),
            UNIQUE(alert_rule_id, regulation_change_id)
        );

        CREATE TABLE IF NOT EXISTS sync_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS warning_letters (
            letter_id TEXT PRIMARY KEY,
            company TEXT NOT NULL,
            subject TEXT NOT NULL DEFAULT '',
            issue_date TEXT NOT NULL DEFAULT '',
            product_type TEXT NOT NULL DEFAULT '',
            close_out_date TEXT,
            url TEXT
        );
    """)
    conn.commit()

    # Migrations for existing databases
    _migrate_add_soi_metadata(conn)

    logger.info("Database initialized at %s", db_path or DB_PATH)
    return conn


def _migrate_add_soi_metadata(conn: sqlite3.Connection) -> None:
    """Add soi_metadata column to actions table if it doesn't exist."""
    cursor = conn.execute("PRAGMA table_info(actions)")
    columns = {row[1] for row in cursor.fetchall()}
    if "soi_metadata" not in columns:
        conn.execute("ALTER TABLE actions ADD COLUMN soi_metadata TEXT")
        conn.commit()
        logger.info("Migrated actions table: added soi_metadata column")


def action_to_row(action_dict: dict) -> dict:
    """Convert an action dict to a row dict (JSON-encode lists)."""
    row = dict(action_dict)
    for field in ("product_categories", "violation_types"):
        if isinstance(row.get(field), list):
            row[field] = json.dumps(row[field])
    if isinstance(row.get("raw_data"), dict):
        row["raw_data"] = json.dumps(row["raw_data"])
    if isinstance(row.get("soi_metadata"), dict):
        row["soi_metadata"] = json.dumps(row["soi_metadata"])
    return row


def row_to_action_dict(row: sqlite3.Row) -> dict:
    """Convert a DB row back to an action dict (JSON-decode lists)."""
    d = dict(row)
    for field in ("product_categories", "violation_types"):
        if isinstance(d.get(field), str):
            try:
                d[field] = json.loads(d[field])
            except (json.JSONDecodeError, TypeError):
                d[field] = []
    if isinstance(d.get("raw_data"), str) and d["raw_data"]:
        try:
            d["raw_data"] = json.loads(d["raw_data"])
        except (json.JSONDecodeError, TypeError):
            d["raw_data"] = None
    if isinstance(d.get("soi_metadata"), str) and d["soi_metadata"]:
        try:
            d["soi_metadata"] = json.loads(d["soi_metadata"])
        except (json.JSONDecodeError, TypeError):
            d["soi_metadata"] = None
    return d


def change_to_row(change_dict: dict) -> dict:
    """Convert a regulation change dict to a row dict."""
    row = dict(change_dict)
    for field in ("product_categories", "cfr_references"):
        if isinstance(row.get(field), list):
            row[field] = json.dumps(row[field])
    if isinstance(row.get("raw_data"), dict):
        row["raw_data"] = json.dumps(row["raw_data"])
    return row


def row_to_change_dict(row: sqlite3.Row) -> dict:
    """Convert a DB row back to a regulation change dict."""
    d = dict(row)
    for field in ("product_categories", "cfr_references"):
        if isinstance(d.get(field), str):
            try:
                d[field] = json.loads(d[field])
            except (json.JSONDecodeError, TypeError):
                d[field] = []
    if isinstance(d.get("raw_data"), str) and d["raw_data"]:
        try:
            d["raw_data"] = json.loads(d["raw_data"])
        except (json.JSONDecodeError, TypeError):
            d["raw_data"] = None
    return d
