"""Tests for Proposition 65 60-day notice client."""

import pytest
import respx
import httpx

from src.integrations.prop65_client import (
    fetch_prop65_notices,
    _classify_categories,
    _classify_violations,
    _extract_company,
    _parse_prop65_html,
)


# --- Classification ---

def test_classify_categories_food():
    cats = _classify_categories("chocolate candy bar snack")
    assert any(c.value == "food" for c in cats)


def test_classify_categories_supplement():
    cats = _classify_categories("herbal supplement vitamin")
    assert any(c.value == "dietary_supplement" for c in cats)


def test_classify_categories_cosmetic():
    cats = _classify_categories("sunscreen lotion cream")
    assert any(c.value == "cosmetic" for c in cats)


def test_classify_violations_lead():
    viols = _classify_violations("product contains lead and cadmium")
    assert any(v.value == "contamination" for v in viols)


def test_classify_violations_default():
    viols = _classify_violations("general exposure issue")
    assert any(v.value == "contamination" for v in viols)


# --- Company extraction ---

def test_extract_company():
    assert _extract_company("Acme Foods, Inc.") == "Acme Foods, Inc."


def test_extract_company_dba():
    result = _extract_company("Acme Corp d/b/a Acme Foods")
    assert "Acme Corp" in result
    assert "d/b/a" not in result


# --- HTML parsing ---

SAMPLE_PROP65_HTML = """
<html><body>
<table>
<tr><th>Alleged Violator</th><th>Product</th><th>Chemical</th><th>Date</th><th>Notice Type</th></tr>
<tr>
  <td>Acme Chocolate Co.</td>
  <td>Dark Chocolate Bars</td>
  <td>Lead, Cadmium</td>
  <td>03/01/2026</td>
  <td>60-Day Notice</td>
</tr>
<tr>
  <td>TechCo Electronics</td>
  <td>Laptop Charger</td>
  <td>BPA</td>
  <td>02/15/2026</td>
  <td>60-Day Notice</td>
</tr>
<tr>
  <td>Beauty Plus LLC</td>
  <td>Sunscreen Lotion SPF 50</td>
  <td>Benzene</td>
  <td>02/20/2026</td>
  <td>60-Day Notice</td>
</tr>
</table>
</body></html>
"""


def test_parse_prop65_html():
    actions = _parse_prop65_html(SAMPLE_PROP65_HTML, date_from="2025-01-01")
    # Should find chocolate (food) and sunscreen (cosmetic)
    # TechCo may also match due to BPA being broadly relevant
    assert len(actions) >= 2
    companies = [a.company for a in actions]
    assert any("Acme Chocolate" in c for c in companies)
    assert any("Beauty Plus" in c for c in companies)


def test_parse_prop65_fields():
    actions = _parse_prop65_html(SAMPLE_PROP65_HTML, date_from="2025-01-01")
    choc = next((a for a in actions if "Chocolate" in a.company), None)
    assert choc is not None
    assert choc.source.value == "prop_65"
    assert choc.severity.value == "warning"
    assert choc.status == "60-Day Notice"
    assert any(v.value == "contamination" for v in choc.violation_types)


def test_parse_prop65_empty():
    actions = _parse_prop65_html("<html><body><p>No notices</p></body></html>")
    assert actions == []


# --- Fetch ---

@pytest.mark.asyncio
@respx.mock
async def test_fetch_prop65_success():
    respx.get(url__startswith="https://oehha.ca.gov").mock(
        return_value=httpx.Response(200, text=SAMPLE_PROP65_HTML)
    )
    results = await fetch_prop65_notices(date_from="2025-01-01")
    assert len(results) >= 1


@pytest.mark.asyncio
@respx.mock
async def test_fetch_prop65_403():
    respx.get(url__startswith="https://oehha.ca.gov").mock(
        return_value=httpx.Response(403)
    )
    results = await fetch_prop65_notices()
    assert results == []


@pytest.mark.asyncio
@respx.mock
async def test_fetch_prop65_error():
    respx.get(url__startswith="https://oehha.ca.gov").mock(
        side_effect=httpx.ConnectError("fail")
    )
    results = await fetch_prop65_notices()
    assert results == []
