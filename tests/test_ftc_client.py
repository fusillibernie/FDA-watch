"""Tests for FTC enforcement client — scrapes ftc.gov cases page."""

import pytest
import respx
import httpx

from src.integrations.ftc_client import (
    fetch_ftc_cases,
    _classify_categories,
    _classify_violations,
    _extract_company,
    _parse_date,
    _parse_ftc_cases_html,
)


# --- Classification ---

def test_classify_categories_supplement():
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


# --- Company extraction ---

def test_extract_company_in_matter_of():
    assert _extract_company("Acme Corp, In the Matter of") == "Acme Corp"


def test_extract_company_ftc_v():
    assert _extract_company("FTC v. HealthCo") == "HealthCo"


def test_extract_company_with_docket():
    result = _extract_company("222-3135 Asbury Automotive Group Inc et al., In the Matter of")
    assert "Asbury Automotive Group" in result


def test_extract_company_fallback():
    assert _extract_company("Some Case Title") == "Some Case Title"


# --- Date parsing ---

def test_parse_date_iso():
    assert _parse_date("2025-06-15") == "2025-06-15"


def test_parse_date_datetime():
    assert _parse_date("2026-03-11T12:00:00Z") == "2026-03-11"


def test_parse_date_none():
    assert _parse_date(None) == ""


# --- HTML parsing ---

SAMPLE_FTC_HTML = """
<html><body>
<article about="/legal-library/browse/cases-proceedings/craig-lewis-matter" class="node node--type-case node--view-mode-search-result node--329672">
  <div class="node__content">
    <h3 class="node-title"><a href="/legal-library/browse/cases-proceedings/craig-lewis-matter" hreflang="en">Craig Lewis, In the Matter of</a></h3>
    <div class="field field--name-field-case-action-type field--type-list-string field--label-inline">
      <div class="field__label">Type of Action</div>
      <div class="field__items"><div class="field__item">Administrative</div></div>
    </div>
    <div class="field field--name-field-date field--type-datetime field--label-inline">
      <div class="field__label">Last Updated</div>
      <div class="field__items"><div class="field__item"><time datetime="2026-03-11T12:00:00Z">March 11, 2026</time></div></div>
    </div>
  </div>
</article>

<article about="/legal-library/browse/cases-proceedings/242-3079-mercury-marketing-llc-ftc-v" class="node node--type-case node--view-mode-search-result node--88513">
  <div class="node__content">
    <h3 class="node-title"><a href="/legal-library/browse/cases-proceedings/242-3079-mercury-marketing-llc-ftc-v" hreflang="en">Mercury Marketing, LLC, FTC v.</a></h3>
    <div class="field field--name-field-case-action-type field--type-list-string field--label-inline">
      <div class="field__label">Type of Action</div>
      <div class="field__items"><div class="field__item">Federal</div></div>
    </div>
    <div class="field field--name-field-date field--type-datetime field--label-inline">
      <div class="field__label">Last Updated</div>
      <div class="field__items"><div class="field__item"><time datetime="2026-02-20T12:00:00Z">February 20, 2026</time></div></div>
    </div>
  </div>
</article>

<article about="/news-events/news/press-releases/2026/03/something" class="node node--type-press-release node--view-mode-snippet node--329726">
  <h3><a href="/news-events/...">Some Press Release</a></h3>
</article>
</body></html>
"""


def test_parse_ftc_cases_html():
    results = _parse_ftc_cases_html(SAMPLE_FTC_HTML, date_from="2025-01-01")
    # Should find 2 cases (not the press release)
    assert len(results) == 2
    assert results[0].source.value == "ftc_action"
    assert "Craig Lewis" in results[0].company
    assert results[0].date == "2026-03-11"
    assert "ftc.gov" in results[0].url


def test_parse_ftc_cases_filters_non_cases():
    """Press releases and other node types should be excluded."""
    html = """
    <article about="/news" class="node node--type-press-release"><h3 class="node-title"><a>Press Release</a></h3></article>
    <article about="/events" class="node node--type-event"><h3 class="node-title"><a>Event</a></h3></article>
    """
    results = _parse_ftc_cases_html(html)
    assert results == []


def test_parse_ftc_empty():
    results = _parse_ftc_cases_html("<html><body>No cases</body></html>")
    assert results == []


# --- Fetch ---

@pytest.mark.asyncio
@respx.mock
async def test_fetch_ftc_cases_success():
    respx.get(url__startswith="https://www.ftc.gov/legal-library/browse/cases-proceedings").mock(
        return_value=httpx.Response(200, text=SAMPLE_FTC_HTML)
    )
    results = await fetch_ftc_cases(date_from="2025-01-01")
    assert len(results) == 2


@pytest.mark.asyncio
@respx.mock
async def test_fetch_ftc_cases_403():
    respx.get(url__startswith="https://www.ftc.gov/legal-library/browse/cases-proceedings").mock(
        return_value=httpx.Response(403)
    )
    results = await fetch_ftc_cases()
    assert results == []


@pytest.mark.asyncio
@respx.mock
async def test_fetch_ftc_cases_error():
    respx.get(url__startswith="https://www.ftc.gov/legal-library/browse/cases-proceedings").mock(
        side_effect=httpx.ConnectError("Connection failed")
    )
    results = await fetch_ftc_cases()
    assert results == []
