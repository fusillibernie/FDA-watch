"""Tests for Federal Register client."""

import pytest
import respx
from httpx import Response

from src.integrations.federal_register_client import fetch_federal_register, _parse_fr_documents
from src.models.enums import RegulationStage, SourceType


def test_parse_fr_documents():
    documents = [
        {
            "document_number": "2025-12345",
            "title": "Food Labeling: Nutrient Content Claims; Definition of Term Healthy",
            "abstract": "FDA is issuing a final rule to update the definition of the term healthy.",
            "agencies": [{"slug": "food-and-drug-administration", "name": "FDA"}],
            "type": "Rule",
            "publication_date": "2025-06-15",
            "effective_on": "2025-12-15",
            "comments_close_on": None,
            "html_url": "https://www.federalregister.gov/d/2025-12345",
            "cfr_references": [{"title": "21", "parts": ["101"]}],
        },
        {
            "document_number": "2025-67890",
            "title": "Proposed Rule: Cosmetic Product Safety Standards",
            "abstract": "FDA proposes to establish safety standards for cosmetic products.",
            "agencies": [{"slug": "food-and-drug-administration", "name": "FDA"}],
            "type": "Proposed Rule",
            "publication_date": "2025-07-01",
            "effective_on": None,
            "comments_close_on": "2025-10-01",
            "html_url": "https://www.federalregister.gov/d/2025-67890",
            "cfr_references": [],
        },
    ]

    changes = _parse_fr_documents(documents)
    assert len(changes) == 2

    c0 = changes[0]
    assert c0.source == SourceType.FEDERAL_REGISTER
    assert c0.source_id == "fr-2025-12345"
    assert c0.agency == "FDA"
    assert c0.stage == RegulationStage.FINAL_RULE
    assert c0.date_published == "2025-06-15"
    assert c0.date_effective == "2025-12-15"
    assert c0.jurisdiction == "US"
    assert "21 CFR 101" in c0.cfr_references

    c1 = changes[1]
    assert c1.stage == RegulationStage.PROPOSED_RULE
    assert c1.date_comments_close == "2025-10-01"


def test_parse_fr_draft_guidance():
    documents = [
        {
            "document_number": "2025-99999",
            "title": "Draft Guidance for Industry: OTC Monograph Drug Products",
            "abstract": "Draft guidance on OTC drug manufacturing.",
            "agencies": [{"slug": "food-and-drug-administration"}],
            "type": "Notice",
            "publication_date": "2025-08-01",
        },
    ]
    changes = _parse_fr_documents(documents)
    assert len(changes) == 1
    assert changes[0].stage == RegulationStage.GUIDANCE_DRAFT


def test_parse_fr_empty():
    assert _parse_fr_documents([]) == []


@pytest.mark.asyncio
@respx.mock
async def test_fetch_federal_register_success():
    respx.get("https://www.federalregister.gov/api/v1/documents.json").mock(
        return_value=Response(200, json={
            "results": [
                {
                    "document_number": "2025-00001",
                    "title": "Test Rule on Medical Device Safety",
                    "abstract": "A test abstract.",
                    "agencies": [{"slug": "food-and-drug-administration"}],
                    "type": "Rule",
                    "publication_date": "2025-09-01",
                    "html_url": "https://www.federalregister.gov/d/2025-00001",
                }
            ]
        })
    )
    changes = await fetch_federal_register(date_from="2025-01-01")
    assert len(changes) == 1
    assert changes[0].source == SourceType.FEDERAL_REGISTER


@pytest.mark.asyncio
@respx.mock
async def test_fetch_federal_register_error():
    respx.get("https://www.federalregister.gov/api/v1/documents.json").mock(
        return_value=Response(500)
    )
    changes = await fetch_federal_register()
    assert changes == []


@pytest.mark.asyncio
@respx.mock
async def test_fetch_federal_register_403():
    respx.get("https://www.federalregister.gov/api/v1/documents.json").mock(
        return_value=Response(429)
    )
    changes = await fetch_federal_register()
    assert changes == []
