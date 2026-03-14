"""Tests for API authentication."""

import os

import pytest
from fastapi.testclient import TestClient

from api.main import app, search_service, alert_service


@pytest.fixture(autouse=True)
def _reset_services(tmp_path):
    search_service._actions = []
    search_service._loaded = True
    search_service.actions_file = tmp_path / "actions.json"
    alert_service.data_dir = tmp_path
    alert_service.rules_file = tmp_path / "rules.json"
    alert_service.matches_file = tmp_path / "matches.json"


@pytest.fixture
def client():
    return TestClient(app)


def test_read_endpoints_no_auth(client):
    """GET endpoints should work without auth."""
    resp = client.get("/api/actions")
    assert resp.status_code == 200
    resp = client.get("/api/alerts/rules")
    assert resp.status_code == 200


def test_write_without_key_env_unset(client, monkeypatch):
    """Write endpoints work when FDA_WATCH_API_KEY is not set."""
    monkeypatch.delenv("FDA_WATCH_API_KEY", raising=False)
    resp = client.post("/api/alerts/rules", json={
        "name": "Test", "keywords": ["test"],
    })
    assert resp.status_code == 201


def test_write_with_valid_key(client, monkeypatch):
    """Write endpoints work with correct API key."""
    monkeypatch.setenv("FDA_WATCH_API_KEY", "secret123")
    resp = client.post(
        "/api/alerts/rules",
        json={"name": "Test", "keywords": ["test"]},
        headers={"X-API-Key": "secret123"},
    )
    assert resp.status_code == 201


def test_write_with_invalid_key(client, monkeypatch):
    """Write endpoints reject wrong API key."""
    monkeypatch.setenv("FDA_WATCH_API_KEY", "secret123")
    resp = client.post(
        "/api/alerts/rules",
        json={"name": "Test", "keywords": ["test"]},
        headers={"X-API-Key": "wrong"},
    )
    assert resp.status_code == 401


def test_write_with_missing_key(client, monkeypatch):
    """Write endpoints reject missing API key when env var is set."""
    monkeypatch.setenv("FDA_WATCH_API_KEY", "secret123")
    resp = client.post(
        "/api/alerts/rules",
        json={"name": "Test", "keywords": ["test"]},
    )
    assert resp.status_code == 401


def test_delete_requires_auth(client, monkeypatch):
    """DELETE endpoints require auth."""
    monkeypatch.setenv("FDA_WATCH_API_KEY", "secret123")
    resp = client.delete("/api/alerts/rules/nonexistent")
    assert resp.status_code == 401

    resp = client.delete(
        "/api/alerts/rules/nonexistent",
        headers={"X-API-Key": "secret123"},
    )
    assert resp.status_code == 404  # Auth passes, rule not found
