"""Tests for class action lawsuit client."""

import pytest
import respx
import httpx

from src.integrations.classaction_client import (
    fetch_classaction_lawsuits,
    _classify_categories,
    _classify_violations,
    _extract_company,
    _parse_classaction_page,
)


def test_classify_categories_food():
    cats = _classify_categories("organic cereal mislabeling lawsuit")
    assert any(c.value == "food" for c in cats)


def test_classify_categories_supplement():
    cats = _classify_categories("vitamin supplement contamination")
    assert any(c.value == "dietary_supplement" for c in cats)


def test_classify_categories_cosmetic():
    cats = _classify_categories("sunscreen lotion false advertising")
    assert any(c.value == "cosmetic" for c in cats)


def test_classify_categories_default():
    cats = _classify_categories("unknown product")
    assert any(c.value == "food" for c in cats)


def test_classify_violations_labeling():
    viols = _classify_violations("mislabeling of organic food")
    assert any(v.value == "labeling_violation" for v in viols)


def test_classify_violations_contamination():
    viols = _classify_violations("heavy metal contamination in baby food")
    assert any(v.value == "contamination" for v in viols)


def test_classify_violations_default():
    viols = _classify_violations("generic lawsuit")
    assert any(v.value == "deceptive_advertising" for v in viols)


def test_extract_company_sued_pattern():
    assert _extract_company("Acme Foods Sued Over False Labeling") == "Acme Foods"


def test_extract_company_class_action_pattern():
    assert _extract_company("BigCorp Class Action Filed") == "BigCorp"


def test_extract_company_no_pattern():
    result = _extract_company("Some Generic Title")
    assert isinstance(result, str)
    assert len(result) > 0


def test_parse_page_with_articles():
    html = """
    <html><body>
    <article>
    <h3><a href="/news/organic-cereal-mislabeled">Organic Cereal Company Sued Over Mislabeling</a></h3>
    <p>03/01/2025 - A class action lawsuit alleges that the cereal food product was mislabeled as organic</p>
    </article>
    <article>
    <h3><a href="/news/supplement-lead">Supplement Contains Lead Contamination</a></h3>
    <p>02/15/2025 - Class action filed over vitamin supplement contamination with heavy metal lead</p>
    </article>
    <article>
    <h3><a href="/news/unrelated">Tech Company Data Breach</a></h3>
    <p>01/01/2025 - Data breach at technology company</p>
    </article>
    </body></html>
    """
    actions = _parse_classaction_page(html, date_from="2024-01-01")
    # Should find food/supplement related cases, not the tech one
    for a in actions:
        assert a.source.value == "class_action"
        assert a.title.startswith("Class Action:")


def test_parse_page_heading_fallback():
    html = """
    <html><body>
    <h2><a href="/food-labeling-suit">Food Labeling Fraud Lawsuit Against Major Grocery Brand</a></h2>
    <h2><a href="/tech-suit">Tech Company Patent Dispute</a></h2>
    </body></html>
    """
    actions = _parse_classaction_page(html, date_from="2024-01-01")
    # Should find the food-related one
    for a in actions:
        assert a.source.value == "class_action"


def test_parse_page_empty():
    actions = _parse_classaction_page("<html><body><p>No lawsuits</p></body></html>")
    assert actions == []


@pytest.mark.asyncio
@respx.mock
async def test_fetch_classaction_success():
    html = """
    <html><body>
    <article>
    <h3><a href="/news/food-recall">Food Company Sued Over Allergen Labeling</a></h3>
    <p>03/01/2025 - Undeclared allergen in food product leads to class action lawsuit</p>
    </article>
    </body></html>
    """
    respx.get("https://www.classaction.org/news").mock(
        return_value=httpx.Response(200, text=html)
    )
    respx.get("https://topclassactions.com/category/lawsuit-settlements/open-lawsuit-settlements/").mock(
        return_value=httpx.Response(200, text="<html><body></body></html>")
    )
    results = await fetch_classaction_lawsuits(date_from="2024-01-01")
    assert isinstance(results, list)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_classaction_all_fail():
    respx.get("https://www.classaction.org/news").mock(
        side_effect=httpx.ConnectError("fail")
    )
    respx.get("https://topclassactions.com/category/lawsuit-settlements/open-lawsuit-settlements/").mock(
        side_effect=httpx.ConnectError("fail")
    )
    results = await fetch_classaction_lawsuits()
    assert results == []


@pytest.mark.asyncio
@respx.mock
async def test_fetch_classaction_partial_fail():
    respx.get("https://www.classaction.org/news").mock(
        return_value=httpx.Response(403)
    )
    respx.get("https://topclassactions.com/category/lawsuit-settlements/open-lawsuit-settlements/").mock(
        return_value=httpx.Response(200, text="<html><body></body></html>")
    )
    results = await fetch_classaction_lawsuits()
    assert isinstance(results, list)
