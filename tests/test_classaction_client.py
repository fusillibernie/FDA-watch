"""Tests for class action lawsuit client — scrapes classaction.org."""

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


# --- Classification ---

def test_classify_categories_food():
    cats = _classify_categories("organic cereal mislabeling lawsuit")
    assert any(c.value == "food" for c in cats)


def test_classify_categories_supplement():
    cats = _classify_categories("vitamin supplement contamination")
    assert any(c.value == "dietary_supplement" for c in cats)


def test_classify_categories_cosmetic():
    cats = _classify_categories("sunscreen lotion false advertising")
    assert any(c.value == "cosmetic" for c in cats)


def test_classify_categories_irrelevant():
    """Non-product lawsuits should return empty categories."""
    cats = _classify_categories("data breach at tech company")
    assert cats == []


def test_classify_violations_labeling():
    viols = _classify_violations("mislabeling of organic food")
    assert any(v.value == "labeling_violation" for v in viols)


def test_classify_violations_contamination():
    viols = _classify_violations("heavy metal contamination in baby food")
    assert any(v.value == "contamination" for v in viols)


def test_classify_violations_default():
    viols = _classify_violations("generic product lawsuit")
    assert any(v.value == "deceptive_advertising" for v in viols)


# --- Company extraction ---

def test_extract_company_sued_pattern():
    assert _extract_company("Acme Foods Sued Over False Labeling") == "Acme Foods"


def test_extract_company_class_action_claims():
    result = _extract_company("Class Action Claims Thrive Causemetics' Charitable Donation Promises are False")
    assert "Thrive Causemetics" in result


def test_extract_company_settlement():
    result = _extract_company("$11.5M Nutramax Settlement Ends Litigation")
    assert "Nutramax" in result


# --- HTML parsing (real classaction.org structure) ---

SAMPLE_CA_HTML = """
<html><body>
<article>
<header class="measure">
<h3 class="mt2 f4"><a href="/news/instinct-dog-food-is-not-preservative-free-as-advertised-class-action-alleges" class="link near-black">Instinct Dog Food Is Not Preservative-Free as Advertised, Class Action Alleges</a></h3>
</header>
<p class="serif"><span class="sans-serif ttu tracked f8 pr2 black-50">March 12, 2026</span> A class action lawsuit alleges several Instinct dog foods are falsely advertised and labeled as preservative-free.</p>
</article>

<article>
<header>
<h3 class="f6 lh-copy mt0"><a href="/news/laird-superfood-lawsuit-claims-protein-is-not-the-primary-macronutrient-in-protein-bars" class="link near-black">Laird Superfood Lawsuit Claims Protein Is Not the Primary Macronutrient in Protein Bars</a></h3>
</header>
<p><span class="sans-serif">March 9, 2026</span> A class action lawsuit claims that Laird Superfood falsely advertises protein bars.</p>
</article>

<article>
<header>
<h3 class="f6 lh-copy mt0"><a href="/news/cadence-bank-data-breach" class="link near-black">Cadence Bank Hit with Class Action After Data Breach</a></h3>
</header>
<p><span>March 13, 2026</span> A data breach class action lawsuit.</p>
</article>

<article>
<header>
<h3 class="f6 lh-copy mt0"><a href="/news/3.6-million-unilever-dry-shampoo-settlement" class="link near-black">$3.6 Million Unilever Dry Shampoo Settlement Over Alleged Benzene Contamination</a></h3>
</header>
<p><span>February 23, 2026</span> A shampoo benzene contamination settlement.</p>
</article>
</body></html>
"""


def test_parse_page_finds_product_cases():
    actions = _parse_classaction_page(SAMPLE_CA_HTML, date_from="2025-01-01")
    # Should find dog food, protein bar, and shampoo cases
    # Should NOT find the data breach case (no product keywords)
    titles = [a.title for a in actions]
    assert any("Instinct" in t for t in titles), f"Expected dog food case, got: {titles}"
    assert any("Laird" in t for t in titles), f"Expected protein bar case, got: {titles}"
    assert any("Unilever" in t for t in titles), f"Expected shampoo case, got: {titles}"
    # Data breach should be filtered out
    assert not any("Cadence" in t for t in titles), f"Data breach should be excluded: {titles}"


def test_parse_page_correct_fields():
    actions = _parse_classaction_page(SAMPLE_CA_HTML, date_from="2025-01-01")
    dog_food = next((a for a in actions if "Instinct" in a.title), None)
    assert dog_food is not None
    assert dog_food.source.value == "class_action"
    assert dog_food.date == "2026-03-12"
    assert "classaction.org" in dog_food.url
    assert dog_food.status == "Filed"


def test_parse_page_stable_ids():
    """IDs should be based on URL slug, not random UUIDs."""
    actions = _parse_classaction_page(SAMPLE_CA_HTML, date_from="2025-01-01")
    for a in actions:
        assert a.source_id.startswith("ca-")
        assert "uuid" not in a.source_id.lower()


def test_parse_page_empty():
    actions = _parse_classaction_page("<html><body><p>No lawsuits</p></body></html>")
    assert actions == []


# --- Fetch ---

@pytest.mark.asyncio
@respx.mock
async def test_fetch_classaction_success():
    respx.get(url__startswith="https://www.classaction.org/news").mock(
        return_value=httpx.Response(200, text=SAMPLE_CA_HTML)
    )
    results = await fetch_classaction_lawsuits(date_from="2025-01-01")
    assert len(results) >= 1
    for r in results:
        assert r.source.value == "class_action"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_classaction_403():
    respx.get(url__startswith="https://www.classaction.org/news").mock(
        return_value=httpx.Response(403)
    )
    results = await fetch_classaction_lawsuits()
    assert results == []


@pytest.mark.asyncio
@respx.mock
async def test_fetch_classaction_error():
    respx.get(url__startswith="https://www.classaction.org/news").mock(
        side_effect=httpx.ConnectError("fail")
    )
    results = await fetch_classaction_lawsuits()
    assert results == []
