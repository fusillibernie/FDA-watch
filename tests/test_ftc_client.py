"""Tests for FTC enforcement client."""

import pytest
import respx
import httpx

from src.integrations.ftc_client import (
    fetch_ftc_cases,
    _classify_categories,
    _classify_violations,
    _extract_company,
    _map_case,
    _parse_date,
)


def test_classify_categories_food():
    cats = _classify_categories("dietary supplement weight loss product")
    assert any(c.value == "dietary_supplement" for c in cats)


def test_classify_categories_cosmetic():
    cats = _classify_categories("cosmetic skin cream")
    assert any(c.value == "cosmetic" for c in cats)


def test_classify_categories_default():
    cats = _classify_categories("unknown product type")
    assert any(c.value == "food" for c in cats)


def test_classify_violations_deceptive():
    viols = _classify_violations("deceptive advertising practices")
    assert any(v.value == "deceptive_advertising" for v in viols)


def test_classify_violations_unsubstantiated():
    viols = _classify_violations("unsubstantiated health claims")
    assert any(v.value == "unsubstantiated_claim" for v in viols)


def test_extract_company_v_pattern():
    assert _extract_company("FTC v. Acme Corp, Inc.") == "Acme Corp"


def test_extract_company_no_pattern():
    assert _extract_company("Some Case Title") == "Some Case Title"


def test_parse_date_iso():
    assert _parse_date("2025-06-15") == "2025-06-15"


def test_parse_date_none():
    assert _parse_date(None) == ""


def test_map_case_basic():
    case = {
        "id": "12345",
        "title": "FTC v. HealthCo",
        "body": "Deceptive advertising of dietary supplement",
        "date": "2025-03-01",
        "defendant": "HealthCo",
        "url": "/legal-library/browse/cases/12345",
    }
    action = _map_case(case)
    assert action is not None
    assert action.id == "ftc-12345"
    assert action.source.value == "ftc_action"
    assert action.company == "HealthCo"
    assert action.date == "2025-03-01"
    assert any(v.value == "deceptive_advertising" for v in action.violation_types)


def test_map_case_empty_title():
    assert _map_case({"id": "1"}) is None


@pytest.mark.asyncio
@respx.mock
async def test_fetch_ftc_cases_success():
    respx.get("https://www.ftc.gov/api/v0/data/cases").mock(
        return_value=httpx.Response(200, json=[
            {
                "id": "99",
                "title": "FTC v. FakeCo",
                "body": "Misleading supplement claims",
                "date": "2025-06-01",
                "defendant": "FakeCo",
            }
        ])
    )
    results = await fetch_ftc_cases(date_from="2025-01-01")
    assert len(results) == 1
    assert results[0].company == "FakeCo"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_ftc_cases_404():
    respx.get("https://www.ftc.gov/api/v0/data/cases").mock(
        return_value=httpx.Response(404)
    )
    results = await fetch_ftc_cases()
    assert results == []


@pytest.mark.asyncio
@respx.mock
async def test_fetch_ftc_cases_error():
    respx.get("https://www.ftc.gov/api/v0/data/cases").mock(
        side_effect=httpx.ConnectError("Connection failed")
    )
    results = await fetch_ftc_cases()
    assert results == []
