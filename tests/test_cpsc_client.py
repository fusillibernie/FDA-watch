"""Tests for CPSC product recall client."""

import pytest
import respx
import httpx

from src.integrations.cpsc_client import (
    fetch_cpsc_recalls,
    _classify_categories,
    _classify_violations,
    _extract_company,
    _parse_cpsc_records,
)


# --- Classification ---

def test_classify_categories_food_contact():
    cats = _classify_categories("stainless steel cookware kitchen set")
    assert any(c.value == "food" for c in cats)


def test_classify_categories_cosmetic():
    cats = _classify_categories("skin lotion cream")
    assert any(c.value == "cosmetic" for c in cats)


def test_classify_categories_irrelevant():
    cats = _classify_categories("power tool drill")
    assert cats == []


def test_classify_violations_lead():
    viols = _classify_violations("contains lead paint")
    assert any(v.value == "contamination" for v in viols)


def test_classify_violations_default():
    viols = _classify_violations("general product issue")
    assert any(v.value == "other" for v in viols)


# --- Company extraction ---

def test_extract_company_manufacturer():
    record = {"Manufacturers": [{"Name": "Acme Corp"}], "Retailers": []}
    assert _extract_company(record) == "Acme Corp"


def test_extract_company_retailer():
    record = {"Manufacturers": [], "Retailers": [{"Name": "Target"}]}
    assert _extract_company(record) == "Target"


def test_extract_company_unknown():
    record = {"Manufacturers": [], "Retailers": []}
    assert _extract_company(record) == "Unknown"


# --- Record parsing ---

SAMPLE_CPSC_RECORDS = [
    {
        "RecallID": "12345",
        "Title": "Baby Bottle Recall Due to Choking Hazard",
        "Description": [{"Description": "Nipple can detach from bottle causing choking hazard for infants"}],
        "Products": [{"Description": "infant baby bottle"}],
        "Manufacturers": [{"Name": "BabyGoods Inc."}],
        "Retailers": [],
        "RecallDate": "2026-03-01",
        "URL": "https://www.cpsc.gov/Recalls/2026/baby-bottle-recall",
    },
    {
        "RecallID": "12346",
        "Title": "Power Drill Recall Due to Shock Hazard",
        "Description": [{"Description": "Drill can shock users"}],
        "Products": [{"Description": "cordless power drill"}],
        "Manufacturers": [{"Name": "ToolCo"}],
        "Retailers": [],
        "RecallDate": "2026-03-02",
        "URL": "",
    },
]


def test_parse_cpsc_records_filters():
    actions = _parse_cpsc_records(SAMPLE_CPSC_RECORDS)
    # Should include baby bottle, exclude power drill
    assert len(actions) == 1
    assert "Baby Bottle" in actions[0].title
    assert actions[0].source.value == "cpsc_recall"
    assert actions[0].company == "BabyGoods Inc."


def test_parse_cpsc_records_fields():
    actions = _parse_cpsc_records(SAMPLE_CPSC_RECORDS)
    a = actions[0]
    assert a.date == "2026-03-01"
    assert a.severity.value == "warning"
    assert a.source_id.startswith("cpsc-")
    assert a.status == "Recalled"


def test_parse_cpsc_empty():
    assert _parse_cpsc_records([]) == []


# --- Fetch ---

@pytest.mark.asyncio
@respx.mock
async def test_fetch_cpsc_success():
    respx.get(url__startswith="https://www.saferproducts.gov").mock(
        return_value=httpx.Response(200, json=SAMPLE_CPSC_RECORDS)
    )
    results = await fetch_cpsc_recalls(date_from="2025-01-01")
    assert len(results) == 1


@pytest.mark.asyncio
@respx.mock
async def test_fetch_cpsc_403():
    respx.get(url__startswith="https://www.saferproducts.gov").mock(
        return_value=httpx.Response(403)
    )
    results = await fetch_cpsc_recalls()
    assert results == []


@pytest.mark.asyncio
@respx.mock
async def test_fetch_cpsc_error():
    respx.get(url__startswith="https://www.saferproducts.gov").mock(
        side_effect=httpx.ConnectError("Connection failed")
    )
    results = await fetch_cpsc_recalls()
    assert results == []
