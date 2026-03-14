"""Alert matching service — checks new actions against user-defined alert rules."""

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

from src.models.alerts import AlertMatch, AlertRule
from src.models.enforcement import RegulatoryAction

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent.parent / "data" / "alerts"
RULES_FILE = DATA_DIR / "rules.json"
MATCHES_FILE = DATA_DIR / "matches.json"


class AlertService:
    """Manages alert rules and matches new regulatory actions against them."""

    def __init__(self, data_dir: Path | None = None):
        self.data_dir = data_dir or DATA_DIR
        self.rules_file = self.data_dir / "rules.json"
        self.matches_file = self.data_dir / "matches.json"
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def _load_rules(self) -> list[AlertRule]:
        if self.rules_file.exists():
            try:
                with open(self.rules_file, "r", encoding="utf-8") as f:
                    return [AlertRule(**r) for r in json.load(f)]
            except (json.JSONDecodeError, IOError):
                pass
        return []

    def _save_rules(self, rules: list[AlertRule]) -> None:
        with open(self.rules_file, "w", encoding="utf-8") as f:
            json.dump([r.model_dump() for r in rules], f, indent=2)

    def _load_matches(self) -> list[AlertMatch]:
        if self.matches_file.exists():
            try:
                with open(self.matches_file, "r", encoding="utf-8") as f:
                    return [AlertMatch(**m) for m in json.load(f)]
            except (json.JSONDecodeError, IOError):
                pass
        return []

    def _save_matches(self, matches: list[AlertMatch]) -> None:
        with open(self.matches_file, "w", encoding="utf-8") as f:
            json.dump([m.model_dump() for m in matches], f, indent=2)

    # --- Rule CRUD ---

    def list_rules(self) -> list[AlertRule]:
        return self._load_rules()

    def get_rule(self, rule_id: str) -> AlertRule | None:
        for rule in self._load_rules():
            if rule.id == rule_id:
                return rule
        return None

    def create_rule(self, name: str, keywords: list[str], **kwargs) -> AlertRule:
        rules = self._load_rules()
        rule = AlertRule(
            id=uuid.uuid4().hex[:12],
            name=name,
            keywords=keywords,
            created_at=datetime.now(timezone.utc).isoformat(),
            **kwargs,
        )
        rules.append(rule)
        self._save_rules(rules)
        return rule

    def update_rule(self, rule_id: str, updates: dict) -> AlertRule | None:
        rules = self._load_rules()
        for i, rule in enumerate(rules):
            if rule.id == rule_id:
                data = rule.model_dump()
                data.update(updates)
                rules[i] = AlertRule(**data)
                self._save_rules(rules)
                return rules[i]
        return None

    def delete_rule(self, rule_id: str) -> bool:
        rules = self._load_rules()
        original_len = len(rules)
        rules = [r for r in rules if r.id != rule_id]
        if len(rules) < original_len:
            self._save_rules(rules)
            return True
        return False

    # --- Match operations ---

    def list_matches(self, unread_only: bool = False) -> list[AlertMatch]:
        matches = self._load_matches()
        if unread_only:
            matches = [m for m in matches if not m.read]
        # Unread first, then by date descending
        return sorted(matches, key=lambda m: (m.read, m.matched_at), reverse=True)

    def unread_count(self) -> int:
        return sum(1 for m in self._load_matches() if not m.read)

    def mark_read(self, match_id: str) -> bool:
        matches = self._load_matches()
        for m in matches:
            if m.id == match_id:
                m.read = True
                self._save_matches(matches)
                return True
        return False

    # --- Matching engine ---

    async def check_actions(self, actions: list[RegulatoryAction]) -> list[AlertMatch]:
        """Check a list of new actions against all active alert rules.

        Returns newly created AlertMatch records. Fires webhooks for rules that have them.
        """
        rules = [r for r in self._load_rules() if r.active]
        if not rules:
            return []

        existing_matches = self._load_matches()
        existing_pairs = {(m.alert_rule_id, m.action_id) for m in existing_matches}
        new_matches: list[AlertMatch] = []

        for action in actions:
            for rule in rules:
                if (rule.id, action.id) in existing_pairs:
                    continue

                if not self._rule_applies(rule, action):
                    continue

                matched_keywords = self._find_keyword_matches(rule.keywords, action)
                if matched_keywords:
                    match = AlertMatch(
                        id=uuid.uuid4().hex[:12],
                        alert_rule_id=rule.id,
                        action_id=action.id,
                        matched_keywords=matched_keywords,
                        matched_at=datetime.now(timezone.utc).isoformat(),
                        read=False,
                    )
                    new_matches.append(match)
                    existing_pairs.add((rule.id, action.id))

        if new_matches:
            all_matches = existing_matches + new_matches
            self._save_matches(all_matches)
            logger.info("Created %d new alert matches", len(new_matches))
            await self._fire_webhooks(new_matches, actions, rules)

        return new_matches

    async def _fire_webhooks(
        self,
        matches: list[AlertMatch],
        actions: list[RegulatoryAction],
        rules: list[AlertRule],
    ) -> None:
        """Send webhook POST requests for rules that have webhook_url configured."""
        action_map = {a.id: a for a in actions}
        rule_map = {r.id: r for r in rules}

        # Group matches by rule
        rule_matches: dict[str, list[AlertMatch]] = {}
        for m in matches:
            rule_matches.setdefault(m.alert_rule_id, []).append(m)

        tasks = []
        for rule_id, rule_match_list in rule_matches.items():
            rule = rule_map.get(rule_id)
            if not rule or not rule.webhook_url:
                continue

            payload = {
                "event": "alert_match",
                "rule_name": rule.name,
                "rule_id": rule.id,
                "matches": [],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            for m in rule_match_list:
                action = action_map.get(m.action_id)
                match_data = {
                    "action_id": m.action_id,
                    "matched_keywords": m.matched_keywords,
                }
                if action:
                    match_data.update({
                        "title": action.title,
                        "company": action.company,
                        "source": action.source.value,
                        "url": action.url,
                    })
                payload["matches"].append(match_data)

            tasks.append(self._send_webhook(rule.webhook_url, payload))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _send_webhook(self, url: str, payload: dict) -> None:
        """POST payload to a webhook URL."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json=payload)
                logger.info("Webhook to %s returned %d", url, resp.status_code)
        except Exception as e:
            logger.error("Webhook to %s failed: %s", url, e)

    def _rule_applies(self, rule: AlertRule, action: RegulatoryAction) -> bool:
        """Check if rule's scope filters match the action."""
        if rule.product_categories:
            if not set(action.product_categories) & set(rule.product_categories):
                return False
        if rule.sources:
            if action.source not in rule.sources:
                return False
        return True

    def _find_keyword_matches(
        self, keywords: list[str], action: RegulatoryAction
    ) -> list[str]:
        """Find which keywords match the action's text."""
        text = f"{action.title} {action.description}".lower()
        matched = []
        for kw in keywords:
            # Try as regex first, fall back to substring
            try:
                if re.search(kw, text, re.IGNORECASE):
                    matched.append(kw)
            except re.error:
                if kw.lower() in text:
                    matched.append(kw)
        return matched
