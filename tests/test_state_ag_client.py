"""Tests for State AG enforcement action client (CA + NY)."""

import pytest
import respx
import httpx

from src.integrations.state_ag_client import (
    fetch_state_ag_actions,
    fetch_ca_ag_actions,
    fetch_ny_ag_actions,
    _classify_categories,
    _classify_violations,
    _extract_company,
    _parse_ca_ag_html,
    _parse_ny_ag_html,
)


# --- Classification ---

def test_classify_categories_food():
    cats = _classify_categories("AG sues grocery company over food safety")
    assert any(c.value == "food" for c in cats)


def test_classify_categories_supplement():
    cats = _classify_categories("AG targets supplement maker for false health claims")
    assert any(c.value == "dietary_supplement" for c in cats)


def test_classify_categories_irrelevant():
    cats = _classify_categories("AG sues tech company over data privacy")
    assert cats == []


def test_classify_violations_deceptive():
    viols = _classify_violations("deceptive business practices false advertising")
    assert any(v.value == "deceptive_advertising" for v in viols)


# --- Company extraction ---

def test_extract_company_ag_sues():
    result = _extract_company("Attorney General Sues FoodCo for Deceptive Labeling")
    assert "FoodCo" in result


def test_extract_company_settles():
    result = _extract_company("HealthBrand Settles Consumer Protection Lawsuit")
    assert "HealthBrand" in result


# --- CA AG HTML parsing ---

SAMPLE_CA_AG_HTML = """
<html><body>
<div class="views-row">
  <span class="date-display-single">March 10, 2026</span>
  <h3><a href="/news/attorney-general-sues-supplement-maker-false-health-claims">
    Attorney General Sues Supplement Maker for False Health Claims
  </a></h3>
</div>
<div class="views-row">
  <span class="date-display-single">March 8, 2026</span>
  <h3><a href="/news/ag-targets-tech-company-data-privacy">
    AG Targets Tech Company Over Data Privacy Violations
  </a></h3>
</div>
<div class="views-row">
  <span class="date-display-single">March 5, 2026</span>
  <h3><a href="/news/consumer-protection-food-safety-organic-fraud">
    Consumer Protection Action Against Organic Food Fraud Company
  </a></h3>
</div>
</body></html>
"""


def test_parse_ca_ag_html():
    actions = _parse_ca_ag_html(SAMPLE_CA_AG_HTML, date_from="2025-01-01")
    # Should find supplement and food cases, not tech/privacy case
    titles = [a.title for a in actions]
    assert any("Supplement" in t for t in titles), f"Expected supplement case, got: {titles}"
    assert any("Food" in t or "Organic" in t for t in titles), f"Expected food case, got: {titles}"
    assert not any("Data Privacy" in t for t in titles), f"Tech case should be filtered: {titles}"


def test_parse_ca_ag_fields():
    actions = _parse_ca_ag_html(SAMPLE_CA_AG_HTML, date_from="2025-01-01")
    supp = next((a for a in actions if "Supplement" in a.title), None)
    assert supp is not None
    assert supp.source.value == "state_ag"
    assert supp.severity.value == "warning"
    assert supp.source_id.startswith("ca-ag-")
    assert "oag.ca.gov" in supp.url


# --- NY AG HTML parsing ---

SAMPLE_NY_AG_HTML = """
<html><body>
<div class="views-row">
  <span>March 11, 2026</span>
  <a href="/press-release/2026/attorney-general-targets-cosmetic-company-misleading-claims">
    Attorney General Targets Cosmetic Company for Misleading Beauty Claims
  </a>
</div>
<div class="views-row">
  <span>March 9, 2026</span>
  <a href="/press-release/2026/ag-investigates-car-dealership-fraud">
    AG Investigates Car Dealership Fraud Scheme
  </a>
</div>
</body></html>
"""


def test_parse_ny_ag_html():
    actions = _parse_ny_ag_html(SAMPLE_NY_AG_HTML, date_from="2025-01-01")
    # Should find cosmetic case, not car dealership
    titles = [a.title for a in actions]
    assert any("Cosmetic" in t for t in titles), f"Expected cosmetic case, got: {titles}"
    assert not any("Car" in t for t in titles), f"Car case should be filtered: {titles}"


def test_parse_ny_ag_fields():
    actions = _parse_ny_ag_html(SAMPLE_NY_AG_HTML, date_from="2025-01-01")
    if actions:
        a = actions[0]
        assert a.source.value == "state_ag"
        assert a.source_id.startswith("ny-ag-")
        assert "ag.ny.gov" in a.url


def test_parse_empty():
    assert _parse_ca_ag_html("<html><body>No news</body></html>") == []
    assert _parse_ny_ag_html("<html><body>No news</body></html>") == []


# --- Fetch ---

@pytest.mark.asyncio
@respx.mock
async def test_fetch_ca_ag_success():
    respx.get(url__startswith="https://oag.ca.gov").mock(
        return_value=httpx.Response(200, text=SAMPLE_CA_AG_HTML)
    )
    results = await fetch_ca_ag_actions(date_from="2025-01-01")
    assert len(results) >= 1


@pytest.mark.asyncio
@respx.mock
async def test_fetch_ny_ag_success():
    respx.get(url__startswith="https://ag.ny.gov").mock(
        return_value=httpx.Response(200, text=SAMPLE_NY_AG_HTML)
    )
    results = await fetch_ny_ag_actions(date_from="2025-01-01")
    assert len(results) >= 1


@pytest.mark.asyncio
@respx.mock
async def test_fetch_state_ag_combined():
    respx.get(url__startswith="https://oag.ca.gov").mock(
        return_value=httpx.Response(200, text=SAMPLE_CA_AG_HTML)
    )
    respx.get(url__startswith="https://ag.ny.gov").mock(
        return_value=httpx.Response(200, text=SAMPLE_NY_AG_HTML)
    )
    results = await fetch_state_ag_actions(date_from="2025-01-01")
    assert len(results) >= 2
    sources = {a.source_id[:5] for a in results}
    assert "ca-ag" in sources
    assert "ny-ag" in sources


@pytest.mark.asyncio
@respx.mock
async def test_fetch_state_ag_error():
    respx.get(url__startswith="https://oag.ca.gov").mock(
        side_effect=httpx.ConnectError("fail")
    )
    respx.get(url__startswith="https://ag.ny.gov").mock(
        side_effect=httpx.ConnectError("fail")
    )
    results = await fetch_state_ag_actions()
    assert results == []
