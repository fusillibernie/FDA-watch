"""Migrate existing JSON data to SQLite.

Usage: py -m src.services.migrate_json_to_sqlite
"""

import json
import logging
from pathlib import Path

from src.services.database import init_db, action_to_row

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent.parent / "data"


def migrate():
    """Read all JSON files and insert into SQLite."""
    conn = init_db()

    # Migrate actions
    actions_file = DATA_DIR / "enforcement" / "actions.json"
    if actions_file.exists():
        with open(actions_file, "r", encoding="utf-8") as f:
            actions = json.load(f)
        count = 0
        for action in actions:
            row = action_to_row(action)
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO actions
                    (id, source, source_id, title, description, company,
                     product_categories, violation_types, severity, date,
                     url, status, distribution, raw_data)
                    VALUES (:id, :source, :source_id, :title, :description, :company,
                            :product_categories, :violation_types, :severity, :date,
                            :url, :status, :distribution, :raw_data)""",
                    row,
                )
                count += 1
            except Exception as e:
                logger.warning("Skipping action %s: %s", action.get("id"), e)
        conn.commit()
        logger.info("Migrated %d actions", count)

    # Migrate alert rules
    rules_file = DATA_DIR / "alerts" / "rules.json"
    if rules_file.exists():
        with open(rules_file, "r", encoding="utf-8") as f:
            rules = json.load(f)
        for rule in rules:
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO alert_rules
                    (id, name, keywords, product_categories, sources, active, created_at, webhook_url)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        rule["id"], rule["name"],
                        json.dumps(rule.get("keywords", [])),
                        json.dumps(rule.get("product_categories")) if rule.get("product_categories") else None,
                        json.dumps(rule.get("sources")) if rule.get("sources") else None,
                        1 if rule.get("active", True) else 0,
                        rule.get("created_at", ""),
                        rule.get("webhook_url"),
                    ),
                )
            except Exception as e:
                logger.warning("Skipping rule %s: %s", rule.get("id"), e)
        conn.commit()
        logger.info("Migrated %d alert rules", len(rules))

    # Migrate alert matches
    matches_file = DATA_DIR / "alerts" / "matches.json"
    if matches_file.exists():
        with open(matches_file, "r", encoding="utf-8") as f:
            matches = json.load(f)
        for match in matches:
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO alert_matches
                    (id, alert_rule_id, action_id, matched_keywords, matched_at, read)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        match["id"], match["alert_rule_id"], match["action_id"],
                        json.dumps(match.get("matched_keywords", [])),
                        match.get("matched_at", ""),
                        1 if match.get("read", False) else 0,
                    ),
                )
            except Exception as e:
                logger.warning("Skipping match %s: %s", match.get("id"), e)
        conn.commit()
        logger.info("Migrated %d alert matches", len(matches))

    # Migrate sync state
    sync_file = DATA_DIR / "sync_state.json"
    if sync_file.exists():
        with open(sync_file, "r", encoding="utf-8") as f:
            state = json.load(f)
        for key, value in state.items():
            conn.execute(
                "INSERT OR REPLACE INTO sync_state (key, value) VALUES (?, ?)",
                (key, value),
            )
        conn.commit()
        logger.info("Migrated sync state")

    # Migrate warning letters
    letters_file = DATA_DIR / "warning_letters" / "letters.json"
    if letters_file.exists():
        with open(letters_file, "r", encoding="utf-8") as f:
            letters = json.load(f)
        for letter in letters:
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO warning_letters
                    (letter_id, company, subject, issue_date, product_type, close_out_date, url)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        letter["letter_id"], letter["company"],
                        letter.get("subject", ""), letter.get("issue_date", ""),
                        letter.get("product_type", ""),
                        letter.get("close_out_date"), letter.get("url"),
                    ),
                )
            except Exception as e:
                logger.warning("Skipping letter %s: %s", letter.get("letter_id"), e)
        conn.commit()
        logger.info("Migrated %d warning letters", len(letters))

    conn.close()
    logger.info("Migration complete!")


if __name__ == "__main__":
    migrate()
