"""Tests for the FastAPI endpoints."""

import pytest
from fastapi.testclient import TestClient

from api.main import app, search_service, alert_service, source_preferences, regulation_search


@pytest.fixture(autouse=True)
def _reset_services(tmp_path):
    """Reset services to use temp directories for each test."""
    search_service._actions = []
    search_service._loaded = True
    search_service.actions_file = tmp_path / "actions.json"

    alert_service.data_dir = tmp_path
    alert_service.rules_file = tmp_path / "rules.json"
    alert_service.matches_file = tmp_path / "matches.json"

    regulation_search._changes = []
    regulation_search._loaded = True
    regulation_search.changes_file = tmp_path / "reg_changes.json"


@pytest.fixture
def client():
    return TestClient(app)


def test_root(client):
    resp = client.get("/")
    assert resp.status_code == 200


def test_list_actions_empty(client):
    resp = client.get("/api/actions")
    assert resp.status_code == 200
    data = resp.json()
    assert data["results"] == []
    assert data["total"] == 0


def test_get_action_not_found(client):
    resp = client.get("/api/actions/nonexistent")
    assert resp.status_code == 404


def test_action_stats_empty(client):
    resp = client.get("/api/actions/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_actions"] == 0


def test_warning_letters_empty(client):
    resp = client.get("/api/warning-letters")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


def test_alert_rule_crud(client):
    # Create
    resp = client.post("/api/alerts/rules", json={
        "name": "Test rule",
        "keywords": ["peanut", "allergen"],
    })
    assert resp.status_code == 201
    rule = resp.json()
    rule_id = rule["id"]
    assert rule["name"] == "Test rule"

    # List
    resp = client.get("/api/alerts/rules")
    assert len(resp.json()) == 1

    # Update
    resp = client.put(f"/api/alerts/rules/{rule_id}", json={"name": "Updated rule"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Updated rule"

    # Delete
    resp = client.delete(f"/api/alerts/rules/{rule_id}")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True

    # Verify deleted
    resp = client.get("/api/alerts/rules")
    assert len(resp.json()) == 0


def test_unread_count_zero(client):
    resp = client.get("/api/alerts/matches/unread-count")
    assert resp.status_code == 200
    assert resp.json()["unread"] == 0


def test_reference_endpoints(client):
    resp = client.get("/api/reference/violation-types")
    assert resp.status_code == 200
    types = resp.json()
    assert len(types) > 0
    assert any(t["value"] == "adulteration" for t in types)

    resp = client.get("/api/reference/product-categories")
    assert resp.status_code == 200
    cats = resp.json()
    assert any(c["value"] == "food" for c in cats)


def test_litigation_empty(client):
    resp = client.get("/api/litigation")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0
    assert resp.json()["results"] == []


def test_litigation_not_found(client):
    resp = client.get("/api/litigation/test-id")
    assert resp.status_code == 404


def test_litigation_with_source_filter(client):
    resp = client.get("/api/litigation?source=ftc_action")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


def test_litigation_sources_reference(client):
    resp = client.get("/api/reference/litigation-sources")
    assert resp.status_code == 200
    sources = resp.json()
    assert len(sources) == 4
    values = {s["value"] for s in sources}
    assert "ftc_action" in values
    assert "class_action" in values
    assert "nad_decision" in values
    assert "state_ag" in values


def test_ingest_status(client):
    resp = client.get("/api/ingest/status")
    assert resp.status_code == 200
    assert "sync_state" in resp.json()


def test_source_preferences_get(client):
    resp = client.get("/api/settings/sources")
    assert resp.status_code == 200
    sources = resp.json()
    assert len(sources) > 0
    keys = {s["source_key"] for s in sources}
    assert "openfda_enforcement" in keys
    assert "eu_rapex" in keys
    # US sources enabled by default
    us = next(s for s in sources if s["source_key"] == "openfda_enforcement")
    assert us["enabled"] is True
    assert us["jurisdiction"] == "US"
    # EU sources disabled by default
    eu = next(s for s in sources if s["source_key"] == "eu_rapex")
    assert eu["enabled"] is False
    assert eu["jurisdiction"] == "EU"


def test_source_preferences_update(client):
    resp = client.put("/api/settings/sources", json={
        "source_key": "eu_rapex",
        "enabled": True,
    })
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True

    # Verify it persisted
    resp = client.get("/api/settings/sources")
    eu = next(s for s in resp.json() if s["source_key"] == "eu_rapex")
    assert eu["enabled"] is True

    # Reset
    client.put("/api/settings/sources", json={"source_key": "eu_rapex", "enabled": False})


def test_source_preferences_invalid_key(client):
    resp = client.put("/api/settings/sources", json={
        "source_key": "nonexistent",
        "enabled": True,
    })
    assert resp.status_code == 400


def test_source_types_reference(client):
    resp = client.get("/api/reference/source-types")
    assert resp.status_code == 200
    types = resp.json()
    assert len(types) == 16  # 10 US + 6 EU
    values = {t["value"] for t in types}
    assert "eu_rapex" in values
    assert "eu_rasff" in values
    assert "eu_sccs" in values
    assert "eu_echa_reach" in values
    # Check jurisdiction field
    eu_rapex = next(t for t in types if t["value"] == "eu_rapex")
    assert eu_rapex["jurisdiction"] == "EU"
    openfda = next(t for t in types if t["value"] == "openfda_enforcement")
    assert openfda["jurisdiction"] == "US"


def test_regulations_empty(client):
    resp = client.get("/api/regulations")
    assert resp.status_code == 200
    data = resp.json()
    assert data["results"] == []
    assert data["total"] == 0


def test_regulation_not_found(client):
    resp = client.get("/api/regulations/nonexistent")
    assert resp.status_code == 404


def test_regulation_stats_empty(client):
    resp = client.get("/api/regulations/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_changes"] == 0


def test_regulation_stages_reference(client):
    resp = client.get("/api/reference/regulation-stages")
    assert resp.status_code == 200
    stages = resp.json()
    assert len(stages) == 8
    values = {s["value"] for s in stages}
    assert "proposed_rule" in values
    assert "final_rule" in values
    assert "amendment" in values
    assert "guidance_draft" in values


def test_source_types_include_regulation_sources(client):
    resp = client.get("/api/reference/source-types")
    values = {t["value"] for t in resp.json()}
    assert "federal_register" in values
    assert "fda_guidance" in values
    assert "eu_official_journal" in values
    assert "ifra_amendment" in values
