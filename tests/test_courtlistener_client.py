"""Tests for CourtListener RECAP client — federal court docket monitoring."""

import pytest
import respx
import httpx

from src.integrations.courtlistener_client import (
    fetch_courtlistener_dockets,
    parse_search_response,
    _classify_categories,
    _classify_violations,
    _extract_company,
    _is_food_consumer_relevant,
)


# --- Classification ---

def test_classify_categories_food():
    cats = _classify_categories("ice cream labeling standard of identity")
    assert any(c.value == "food" for c in cats)


def test_classify_categories_supplement():
    cats = _classify_categories("vitamin supplement contamination")
    assert any(c.value == "dietary_supplement" for c in cats)


def test_classify_categories_cosmetic():
    cats = _classify_categories("sunscreen false advertising lawsuit")
    assert any(c.value == "cosmetic" for c in cats)


def test_classify_categories_irrelevant():
    """Non-product cases should return empty categories."""
    cats = _classify_categories("patent infringement software company")
    assert cats == []


def test_classify_violations_soi():
    viols = _classify_violations("standard of identity vanilla ice cream")
    assert any(v.value == "standards_of_identity" for v in viols)


def test_classify_violations_labeling():
    viols = _classify_violations("mislabeling of organic food")
    assert any(v.value == "labeling_violation" for v in viols)


def test_classify_violations_misbranding():
    viols = _classify_violations("misbranding food product")
    assert any(v.value == "misbranding" for v in viols)


def test_classify_violations_default():
    """Generic text should fall back to deceptive advertising."""
    viols = _classify_violations("generic product lawsuit")
    assert any(v.value == "deceptive_advertising" for v in viols)


# --- Company extraction ---

def test_extract_company_v_pattern():
    result = _extract_company("Smith v. Acme Foods Inc.")
    assert "Acme Foods" in result


def test_extract_company_vs_pattern():
    result = _extract_company("Jones vs. Big Dairy Corp")
    assert "Big Dairy Corp" in result


def test_extract_company_in_re():
    result = _extract_company("In re: Honey Mislabeling Litigation")
    assert "Honey Mislabeling Litigation" in result


def test_extract_company_fallback():
    result = _extract_company("Some Random Case Name, Extra Details")
    assert result == "Some Random Case Name"


# --- Relevance filtering ---

def test_is_food_consumer_relevant_yes():
    assert _is_food_consumer_relevant("honey mislabeling class action")


def test_is_food_consumer_relevant_no():
    assert not _is_food_consumer_relevant("patent infringement software")


# --- Response parsing ---

SAMPLE_RESPONSE = {
    "count": 2,
    "next": None,
    "previous": None,
    "results": [
        {
            "case_name": "Smith v. Acme Honey Corp",
            "docket_number": "3:24-cv-01234",
            "date_filed": "2026-03-10",
            "court_id": "cand",
            "absolute_url": "/docket/12345/smith-v-acme-honey-corp/",
            "description": "False advertising honey mislabel food product",
            "docket_id": "12345",
        },
        {
            "case_name": "Jones v. BigTech Software",
            "docket_number": "1:24-cv-99999",
            "date_filed": "2026-03-08",
            "court_id": "nysd",
            "absolute_url": "/docket/99999/jones-v-bigtech-software/",
            "description": "Patent infringement software licensing",
            "docket_id": "99999",
        },
        {
            "caseName": "Williams v. Vanilla Bean Inc",
            "docketNumber": "2:24-cv-05678",
            "dateFiled": "2026-03-05",
            "court": "ilnd",
            "absolute_url": "/docket/56789/williams-v-vanilla-bean-inc/",
            "description": "Standard of identity vanilla ice cream labeling",
            "id": "56789",
        },
    ],
}


def test_parse_search_response_filters():
    """Should keep food cases and filter out irrelevant ones."""
    actions = parse_search_response(SAMPLE_RESPONSE)
    titles = [a.title for a in actions]
    # Honey case should be kept
    assert any("Acme Honey" in t for t in titles), f"Expected honey case, got: {titles}"
    # Vanilla case should be kept
    assert any("Vanilla Bean" in t for t in titles), f"Expected vanilla case, got: {titles}"
    # Software patent case should be filtered
    assert not any("BigTech" in t for t in titles), f"Software case should be excluded: {titles}"


def test_parse_search_response_correct_fields():
    actions = parse_search_response(SAMPLE_RESPONSE)
    honey = next((a for a in actions if "Honey" in a.title), None)
    assert honey is not None
    assert honey.source.value == "courtlistener"
    assert honey.source_id == "cl-12345"
    assert honey.date == "2026-03-10"
    assert "courtlistener.com" in honey.url
    assert honey.status == "Filed"
    assert honey.severity.value == "advisory"


def test_parse_search_response_camelcase_fields():
    """Should handle camelCase fields (opinion-style results)."""
    actions = parse_search_response(SAMPLE_RESPONSE)
    vanilla = next((a for a in actions if "Vanilla" in a.title), None)
    assert vanilla is not None
    assert vanilla.source_id == "cl-56789"
    assert vanilla.date == "2026-03-05"


def test_parse_search_response_company_extraction():
    actions = parse_search_response(SAMPLE_RESPONSE)
    honey = next((a for a in actions if "Honey" in a.title), None)
    assert honey is not None
    assert "Acme Honey Corp" in honey.company


def test_parse_search_response_violation_classification():
    actions = parse_search_response(SAMPLE_RESPONSE)
    vanilla = next((a for a in actions if "Vanilla" in a.title), None)
    assert vanilla is not None
    violation_values = [v.value for v in vanilla.violation_types]
    assert "standards_of_identity" in violation_values


def test_parse_search_response_product_categories():
    actions = parse_search_response(SAMPLE_RESPONSE)
    honey = next((a for a in actions if "Honey" in a.title), None)
    assert honey is not None
    cat_values = [c.value for c in honey.product_categories]
    assert "food" in cat_values


def test_parse_search_response_empty():
    actions = parse_search_response({"results": []})
    assert actions == []


def test_parse_search_response_no_results_key():
    actions = parse_search_response({})
    assert actions == []


def test_parse_search_response_title_truncation():
    """Case names longer than 200 chars should be truncated in title."""
    long_name = "A" * 250
    data = {
        "results": [{
            "case_name": long_name,
            "docket_number": "1:24-cv-00001",
            "date_filed": "2026-01-01",
            "court_id": "cand",
            "absolute_url": "/docket/1/case/",
            "description": "food labeling mislabel organic",
            "docket_id": "1",
        }]
    }
    actions = parse_search_response(data)
    assert len(actions) == 1
    assert len(actions[0].title) <= 200


# --- Fetch ---

@pytest.mark.asyncio
@respx.mock
async def test_fetch_courtlistener_success():
    respx.get(url__startswith="https://www.courtlistener.com/api/rest/v4/search/").mock(
        return_value=httpx.Response(200, json=SAMPLE_RESPONSE)
    )
    results = await fetch_courtlistener_dockets(date_from="2026-01-01", max_pages=1)
    # Should have at least the food-relevant cases
    assert len(results) >= 1
    for r in results:
        assert r.source.value == "courtlistener"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_courtlistener_rate_limit():
    respx.get(url__startswith="https://www.courtlistener.com/api/rest/v4/search/").mock(
        return_value=httpx.Response(429)
    )
    results = await fetch_courtlistener_dockets(date_from="2026-01-01", max_pages=1)
    assert results == []


@pytest.mark.asyncio
@respx.mock
async def test_fetch_courtlistener_error():
    respx.get(url__startswith="https://www.courtlistener.com/api/rest/v4/search/").mock(
        side_effect=httpx.ConnectError("fail")
    )
    results = await fetch_courtlistener_dockets(date_from="2026-01-01", max_pages=1)
    assert results == []


@pytest.mark.asyncio
@respx.mock
async def test_fetch_courtlistener_deduplication():
    """Same docket appearing from different queries should be deduped."""
    respx.get(url__startswith="https://www.courtlistener.com/api/rest/v4/search/").mock(
        return_value=httpx.Response(200, json=SAMPLE_RESPONSE)
    )
    results = await fetch_courtlistener_dockets(date_from="2026-01-01", max_pages=1)
    source_ids = [r.source_id for r in results]
    assert len(source_ids) == len(set(source_ids)), "Duplicate source_ids found"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_courtlistener_pagination():
    """Should follow next URL for pagination."""
    page1 = {
        "count": 2,
        "next": "https://www.courtlistener.com/api/rest/v4/search/?page=2",
        "results": [
            {
                "case_name": "Doe v. Maple Syrup Fakers",
                "docket_number": "1:24-cv-00100",
                "date_filed": "2026-03-01",
                "court_id": "vtd",
                "absolute_url": "/docket/100/doe-v-maple-syrup-fakers/",
                "description": "maple syrup mislabel food",
                "docket_id": "100",
            },
        ],
    }
    page2 = {
        "count": 2,
        "next": None,
        "results": [
            {
                "case_name": "Roe v. Olive Oil Importers",
                "docket_number": "2:24-cv-00200",
                "date_filed": "2026-02-15",
                "court_id": "cacd",
                "absolute_url": "/docket/200/roe-v-olive-oil-importers/",
                "description": "olive oil fraud food labeling",
                "docket_id": "200",
            },
        ],
    }
    respx.get("https://www.courtlistener.com/api/rest/v4/search/", params__contains={"q": '"standard of identity" food'}).mock(
        return_value=httpx.Response(200, json=page1)
    )
    respx.get("https://www.courtlistener.com/api/rest/v4/search/?page=2").mock(
        return_value=httpx.Response(200, json=page2)
    )
    # Mock all other queries with empty results
    respx.get(url__startswith="https://www.courtlistener.com/api/rest/v4/search/").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    results = await fetch_courtlistener_dockets(date_from="2026-01-01", max_pages=3)
    titles = [r.title for r in results]
    assert any("Maple Syrup" in t for t in titles)
    assert any("Olive Oil" in t for t in titles)
