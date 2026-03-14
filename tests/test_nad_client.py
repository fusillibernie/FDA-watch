"""Tests for NAD (National Advertising Division) decision client."""

import pytest
import respx
import httpx

from src.integrations.nad_client import (
    fetch_nad_decisions,
    _classify_categories,
    _classify_violations,
    _extract_company,
    _parse_nad_html,
)


# --- Classification ---

def test_classify_categories_food():
    cats = _classify_categories("NAD reviews yogurt beverage claims")
    assert any(c.value == "food" for c in cats)


def test_classify_categories_supplement():
    cats = _classify_categories("NAD examines weight loss supplement advertising")
    assert any(c.value == "dietary_supplement" for c in cats)


def test_classify_categories_cosmetic():
    cats = _classify_categories("NAD reviews skincare beauty product claims")
    assert any(c.value == "cosmetic" for c in cats)


def test_classify_categories_irrelevant():
    cats = _classify_categories("NAD reviews telecom service advertising")
    assert cats == []


def test_classify_violations_deceptive():
    viols = _classify_violations("misleading advertising claims")
    assert any(v.value == "deceptive_advertising" for v in viols)


def test_classify_violations_unsubstantiated():
    viols = _classify_violations("unsubstantiated health claims lack substantiation")
    assert any(v.value == "unsubstantiated_claim" for v in viols)


# --- Company extraction ---

def test_extract_company_recommends():
    result = _extract_company("NAD Recommends Acme Corp Discontinue Advertising Claims")
    assert "Acme Corp" in result


def test_extract_company_finds():
    result = _extract_company("NAD Finds HealthCo Claims Misleading")
    assert "HealthCo" in result


def test_extract_company_voluntarily():
    result = _extract_company("NutraBrand Voluntarily Modifies Advertising")
    assert "NutraBrand" in result


def test_extract_company_possessive():
    result = _extract_company("NAD Reviews Validity of GlowSkin's Anti-Aging Claims")
    assert "GlowSkin" in result


# --- HTML parsing ---

SAMPLE_NAD_HTML = """
<html><body>
<div class="media-list">
  <div class="media-item">
    <span class="date">March 10, 2026</span>
    <a href="/media-center/nad-recommends-acme-supplements-discontinue-weight-loss-claims">
      NAD Recommends Acme Supplements Discontinue Weight Loss Claims
    </a>
  </div>
  <div class="media-item">
    <span class="date">March 8, 2026</span>
    <a href="/media-center/nad-finds-techco-broadband-speed-claims-misleading">
      NAD Finds TechCo Broadband Speed Claims Misleading
    </a>
  </div>
  <div class="media-item">
    <span class="date">March 5, 2026</span>
    <a href="/media-center/nad-reviews-glowskin-beauty-cream-advertising">
      NAD Reviews GlowSkin Beauty Cream Advertising Claims
    </a>
  </div>
</div>
</body></html>
"""


def test_parse_nad_html():
    actions = _parse_nad_html(SAMPLE_NAD_HTML, date_from="2025-01-01")
    # Should find supplement and beauty cream cases
    # Should NOT find TechCo broadband (no product keywords)
    titles = [a.title for a in actions]
    assert any("Acme" in t for t in titles), f"Expected supplement case, got: {titles}"
    assert any("GlowSkin" in t for t in titles), f"Expected beauty case, got: {titles}"
    assert not any("TechCo" in t for t in titles), f"TechCo should be filtered: {titles}"


def test_parse_nad_fields():
    actions = _parse_nad_html(SAMPLE_NAD_HTML, date_from="2025-01-01")
    supp = next((a for a in actions if "Acme" in a.title), None)
    assert supp is not None
    assert supp.source.value == "nad_decision"
    assert supp.severity.value == "warning"
    assert supp.date == "2026-03-10"
    assert supp.source_id.startswith("nad-")
    assert "bbbprograms.org" in supp.url


def test_parse_nad_empty():
    actions = _parse_nad_html("<html><body>No press releases</body></html>")
    assert actions == []


# --- Fetch ---

@pytest.mark.asyncio
@respx.mock
async def test_fetch_nad_success():
    respx.get(url__startswith="https://bbbprograms.org").mock(
        return_value=httpx.Response(200, text=SAMPLE_NAD_HTML)
    )
    results = await fetch_nad_decisions(date_from="2025-01-01")
    assert len(results) >= 1


@pytest.mark.asyncio
@respx.mock
async def test_fetch_nad_403():
    respx.get(url__startswith="https://bbbprograms.org").mock(
        return_value=httpx.Response(403)
    )
    results = await fetch_nad_decisions()
    assert results == []


@pytest.mark.asyncio
@respx.mock
async def test_fetch_nad_error():
    respx.get(url__startswith="https://bbbprograms.org").mock(
        side_effect=httpx.ConnectError("fail")
    )
    results = await fetch_nad_decisions()
    assert results == []
