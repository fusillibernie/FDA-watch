"""Tests for source preferences service."""

import json

from src.models.enums import SourceType
from src.services.source_preferences import SourcePreferencesService


def test_default_us_enabled(tmp_path):
    svc = SourcePreferencesService(settings_file=tmp_path / "prefs.json")
    assert svc.is_enabled(SourceType.OPENFDA_ENFORCEMENT) is True
    assert svc.is_enabled(SourceType.FDA_WARNING_LETTER) is True
    assert svc.is_enabled(SourceType.FTC_ACTION) is True
    assert svc.is_enabled(SourceType.CPSC_RECALL) is True


def test_default_eu_disabled(tmp_path):
    svc = SourcePreferencesService(settings_file=tmp_path / "prefs.json")
    assert svc.is_enabled(SourceType.EU_RAPEX) is False
    assert svc.is_enabled(SourceType.EU_RASFF) is False
    assert svc.is_enabled(SourceType.EU_SCCS) is False
    assert svc.is_enabled(SourceType.EU_ECHA_REACH) is False


def test_toggle_source(tmp_path):
    svc = SourcePreferencesService(settings_file=tmp_path / "prefs.json")
    assert svc.is_enabled(SourceType.EU_RAPEX) is False

    result = svc.update("eu_rapex", True)
    assert result is True
    assert svc.is_enabled(SourceType.EU_RAPEX) is True

    result = svc.update("eu_rapex", False)
    assert result is True
    assert svc.is_enabled(SourceType.EU_RAPEX) is False


def test_invalid_source_key(tmp_path):
    svc = SourcePreferencesService(settings_file=tmp_path / "prefs.json")
    result = svc.update("nonexistent_source", True)
    assert result is False


def test_persistence(tmp_path):
    settings_file = tmp_path / "prefs.json"
    svc = SourcePreferencesService(settings_file=settings_file)
    svc.update("eu_rapex", True)
    svc.update("openfda_enforcement", False)

    # Reload from disk
    svc2 = SourcePreferencesService(settings_file=settings_file)
    assert svc2.is_enabled(SourceType.EU_RAPEX) is True
    assert svc2.is_enabled(SourceType.OPENFDA_ENFORCEMENT) is False
    # Unmodified defaults preserved
    assert svc2.is_enabled(SourceType.FTC_ACTION) is True
    assert svc2.is_enabled(SourceType.EU_RASFF) is False


def test_get_all(tmp_path):
    svc = SourcePreferencesService(settings_file=tmp_path / "prefs.json")
    all_prefs = svc.get_all()

    assert len(all_prefs) == len(SourceType)
    keys = {p["source_key"] for p in all_prefs}
    assert "eu_rapex" in keys
    assert "openfda_enforcement" in keys

    eu_entries = [p for p in all_prefs if p["jurisdiction"] == "EU"]
    assert len(eu_entries) == 6
    for entry in eu_entries:
        assert entry["enabled"] is False

    us_entries = [p for p in all_prefs if p["jurisdiction"] == "US"]
    assert len(us_entries) == 10
    for entry in us_entries:
        assert entry["enabled"] is True
