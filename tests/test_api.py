"""Tests for the FastAPI endpoints."""

import pytest
from fastapi.testclient import TestClient

from api.main import app, search_service, alert_service


@pytest.fixture(autouse=True)
def _reset_services(tmp_path):
    """Reset services to use temp directories for each test."""
    search_service._actions = []
    search_service._loaded = True
    search_service.actions_file = tmp_path / "actions.json"

    alert_service.data_dir = tmp_path
    alert_service.rules_file = tmp_path / "rules.json"
    alert_service.matches_file = tmp_path / "matches.json"


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


def test_litigation_placeholder(client):
    resp = client.get("/api/litigation")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0

    resp = client.get("/api/litigation/test-id")
    assert resp.status_code == 501


def test_ingest_status(client):
    resp = client.get("/api/ingest/status")
    assert resp.status_code == 200
    assert "sync_state" in resp.json()
