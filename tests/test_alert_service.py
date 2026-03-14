"""Tests for the alert service."""

import tempfile
from pathlib import Path

import pytest

from src.models.enums import ProductCategory, Severity, SourceType, ViolationType
from src.models.enforcement import RegulatoryAction
from src.services.alert_service import AlertService


def _make_action(action_id: str, title: str, description: str) -> RegulatoryAction:
    return RegulatoryAction(
        id=action_id,
        source=SourceType.OPENFDA_ENFORCEMENT,
        source_id=action_id,
        title=title,
        description=description,
        company="Test Foods Inc.",
        product_categories=[ProductCategory.FOOD],
        violation_types=[ViolationType.UNDECLARED_ALLERGEN],
        severity=Severity.CLASS_I,
        date="2025-01-15",
    )


def test_create_and_list_rules():
    with tempfile.TemporaryDirectory() as tmpdir:
        svc = AlertService(data_dir=Path(tmpdir))
        rule = svc.create_rule(
            name="Peanut alerts",
            keywords=["peanut", "tree nut"],
            product_categories=[ProductCategory.FOOD],
        )
        assert rule.name == "Peanut alerts"
        assert rule.active is True

        rules = svc.list_rules()
        assert len(rules) == 1
        assert rules[0].id == rule.id


def test_update_rule():
    with tempfile.TemporaryDirectory() as tmpdir:
        svc = AlertService(data_dir=Path(tmpdir))
        rule = svc.create_rule(name="Test", keywords=["test"])
        updated = svc.update_rule(rule.id, {"name": "Updated", "active": False})
        assert updated is not None
        assert updated.name == "Updated"
        assert updated.active is False


def test_delete_rule():
    with tempfile.TemporaryDirectory() as tmpdir:
        svc = AlertService(data_dir=Path(tmpdir))
        rule = svc.create_rule(name="Delete me", keywords=["x"])
        assert svc.delete_rule(rule.id) is True
        assert len(svc.list_rules()) == 0
        assert svc.delete_rule("nonexistent") is False


@pytest.mark.asyncio
async def test_check_actions_creates_matches():
    with tempfile.TemporaryDirectory() as tmpdir:
        svc = AlertService(data_dir=Path(tmpdir))
        svc.create_rule(name="Peanut", keywords=["peanut"])

        actions = [
            _make_action("a1", "Recall", "Contains undeclared peanut"),
            _make_action("a2", "Recall", "Contains undeclared soy"),
        ]

        matches = await svc.check_actions(actions)
        assert len(matches) == 1
        assert matches[0].action_id == "a1"
        assert "peanut" in matches[0].matched_keywords


@pytest.mark.asyncio
async def test_no_duplicate_matches():
    with tempfile.TemporaryDirectory() as tmpdir:
        svc = AlertService(data_dir=Path(tmpdir))
        svc.create_rule(name="Test", keywords=["peanut"])

        actions = [_make_action("a1", "Recall", "Undeclared peanut")]
        await svc.check_actions(actions)
        # Run again with same action
        matches = await svc.check_actions(actions)
        assert len(matches) == 0


@pytest.mark.asyncio
async def test_mark_read_and_unread_count():
    with tempfile.TemporaryDirectory() as tmpdir:
        svc = AlertService(data_dir=Path(tmpdir))
        svc.create_rule(name="Test", keywords=["peanut"])
        await svc.check_actions([_make_action("a1", "Recall", "Peanut recall")])

        assert svc.unread_count() == 1
        matches = svc.list_matches()
        svc.mark_read(matches[0].id)
        assert svc.unread_count() == 0


@pytest.mark.asyncio
async def test_scope_filtering_by_category():
    with tempfile.TemporaryDirectory() as tmpdir:
        svc = AlertService(data_dir=Path(tmpdir))
        svc.create_rule(
            name="Cosmetic only",
            keywords=["recall"],
            product_categories=[ProductCategory.COSMETIC],
        )

        # Food action should NOT match a cosmetic-scoped rule
        actions = [_make_action("a1", "Recall of food", "Food recall notice")]
        matches = await svc.check_actions(actions)
        assert len(matches) == 0


@pytest.mark.asyncio
async def test_regex_keyword():
    with tempfile.TemporaryDirectory() as tmpdir:
        svc = AlertService(data_dir=Path(tmpdir))
        svc.create_rule(name="Allergens regex", keywords=[r"undeclared \w+"])

        actions = [_make_action("a1", "Recall", "Contains undeclared sesame")]
        matches = await svc.check_actions(actions)
        assert len(matches) == 1


@pytest.mark.asyncio
async def test_webhook_fires_on_match():
    """Webhook POST fires when a rule with webhook_url matches."""
    import respx
    import httpx

    with tempfile.TemporaryDirectory() as tmpdir:
        svc = AlertService(data_dir=Path(tmpdir))
        svc.create_rule(
            name="Webhook test",
            keywords=["peanut"],
            webhook_url="https://webhook.test/hook",
        )

        actions = [_make_action("a1", "Recall", "Contains undeclared peanut")]

        with respx.mock:
            webhook_route = respx.post("https://webhook.test/hook").mock(
                return_value=httpx.Response(200)
            )
            matches = await svc.check_actions(actions)
            assert len(matches) == 1
            assert webhook_route.called
            payload = webhook_route.calls[0].request.content
            import json
            data = json.loads(payload)
            assert data["event"] == "alert_match"
            assert data["rule_name"] == "Webhook test"
            assert len(data["matches"]) == 1
            assert data["matches"][0]["action_id"] == "a1"
