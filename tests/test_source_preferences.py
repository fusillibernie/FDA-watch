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
    assert len(us_entries) == 11
    for entry in us_entries:
        assert entry["enabled"] is True


def test_categories_default(tmp_path):
    svc = SourcePreferencesService(settings_file=tmp_path / "prefs.json")
    cats = svc.get_categories("eu_rapex")
    assert cats is not None
    assert "Cosmetics" in cats
    assert "Chemical products" in cats


def test_categories_update(tmp_path):
    svc = SourcePreferencesService(settings_file=tmp_path / "prefs.json")
    svc.update_categories("eu_rapex", ["Cosmetics"])
    assert svc.get_categories("eu_rapex") == ["Cosmetics"]

    # Persistence
    svc2 = SourcePreferencesService(settings_file=tmp_path / "prefs.json")
    assert svc2.get_categories("eu_rapex") == ["Cosmetics"]


def test_categories_in_get_all(tmp_path):
    svc = SourcePreferencesService(settings_file=tmp_path / "prefs.json")
    all_prefs = svc.get_all()
    rapex = next(p for p in all_prefs if p["source_key"] == "eu_rapex")
    assert "categories" in rapex
    assert "Cosmetics" in rapex["categories"]

    # Non-RAPEX source should not have categories
    openfda = next(p for p in all_prefs if p["source_key"] == "openfda_enforcement")
    assert "categories" not in openfda


def test_categories_invalid_key(tmp_path):
    svc = SourcePreferencesService(settings_file=tmp_path / "prefs.json")
    result = svc.update_categories("nonexistent", ["Cosmetics"])
    assert result is False


def test_lookback_days_default(tmp_path):
    svc = SourcePreferencesService(settings_file=tmp_path / "prefs.json")
    assert svc.get_lookback_days("openfda_enforcement") == 1825
    assert svc.get_lookback_days("ftc_action") == 1825


def test_lookback_days_update(tmp_path):
    svc = SourcePreferencesService(settings_file=tmp_path / "prefs.json")
    result = svc.update_lookback_days("ftc_action", 7300)  # 20 years
    assert result is True
    assert svc.get_lookback_days("ftc_action") == 7300
    # Other sources still default
    assert svc.get_lookback_days("openfda_enforcement") == 1825


def test_lookback_days_clamp(tmp_path):
    svc = SourcePreferencesService(settings_file=tmp_path / "prefs.json")
    svc.update_lookback_days("ftc_action", 5)  # too low, clamp to 30
    assert svc.get_lookback_days("ftc_action") == 30
    svc.update_lookback_days("ftc_action", 99999)  # too high, clamp to 36500
    assert svc.get_lookback_days("ftc_action") == 36500


def test_lookback_days_persistence(tmp_path):
    settings_file = tmp_path / "prefs.json"
    svc = SourcePreferencesService(settings_file=settings_file)
    svc.update_lookback_days("class_action", 3650)
    svc2 = SourcePreferencesService(settings_file=settings_file)
    assert svc2.get_lookback_days("class_action") == 3650


def test_lookback_days_invalid_key(tmp_path):
    svc = SourcePreferencesService(settings_file=tmp_path / "prefs.json")
    result = svc.update_lookback_days("nonexistent", 365)
    assert result is False


def test_lookback_days_in_get_all(tmp_path):
    svc = SourcePreferencesService(settings_file=tmp_path / "prefs.json")
    svc.update_lookback_days("ftc_action", 7300)
    all_prefs = svc.get_all()
    ftc = next(p for p in all_prefs if p["source_key"] == "ftc_action")
    assert ftc["lookback_days"] == 7300
    openfda = next(p for p in all_prefs if p["source_key"] == "openfda_enforcement")
    assert openfda["lookback_days"] == 1825
