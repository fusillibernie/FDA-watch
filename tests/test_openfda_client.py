"""Tests for openFDA client — uses mocked HTTP responses."""

import pytest
import respx
import httpx

from src.services.openfda_client import fetch_enforcement, _map_record, _parse_date


SAMPLE_RESPONSE = {
    "meta": {
        "results": {"skip": 0, "limit": 1, "total": 1}
    },
    "results": [
        {
            "recall_number": "F-1234-2025",
            "product_description": "Organic Peanut Butter, 16oz jars",
            "reason_for_recall": "Product contains undeclared milk allergen.",
            "classification": "Class I",
            "recalling_firm": "Nut Corp",
            "report_date": "20250115",
            "status": "Ongoing",
            "distribution_pattern": "Nationwide",
        }
    ],
}


def test_parse_date_valid():
    assert _parse_date("20250115") == "2025-01-15"


def test_parse_date_invalid():
    assert _parse_date("not-a-date") == "not-a-date"


def test_parse_date_empty():
    assert _parse_date("") == ""


def test_map_record():
    record = SAMPLE_RESPONSE["results"][0]
    action = _map_record(record, "food")
    assert action.source_id == "F-1234-2025"
    assert action.company == "Nut Corp"
    assert action.date == "2025-01-15"
    assert action.severity.value == "class_i"
    assert action.raw_data == record


@pytest.mark.asyncio
@respx.mock
async def test_fetch_enforcement_success():
    url_pattern = respx.get("https://api.fda.gov/food/enforcement.json").mock(
        return_value=httpx.Response(200, json=SAMPLE_RESPONSE)
    )

    results = await fetch_enforcement(endpoint="food", date_from="20250101")
    assert len(results) == 1
    assert results[0].source_id == "F-1234-2025"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_enforcement_404():
    respx.get("https://api.fda.gov/food/enforcement.json").mock(
        return_value=httpx.Response(404)
    )

    results = await fetch_enforcement(endpoint="food")
    assert results == []


@pytest.mark.asyncio
@respx.mock
async def test_fetch_enforcement_with_api_key():
    route = respx.get("https://api.fda.gov/drug/enforcement.json").mock(
        return_value=httpx.Response(200, json=SAMPLE_RESPONSE)
    )

    results = await fetch_enforcement(endpoint="drug", api_key="test-key-123")
    assert len(results) == 1
    # Verify API key was sent
    assert "api_key=test-key-123" in str(route.calls[0].request.url)
