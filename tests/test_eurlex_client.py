"""Tests for EUR-Lex regulation change client (SPARQL endpoint)."""

import pytest
import respx
from httpx import Response

from src.integrations.eurlex_client import fetch_eurlex_changes, _parse_sparql_results
from src.models.enums import RegulationStage, SourceType


SAMPLE_SPARQL_RESPONSE = {
    "results": {
        "bindings": [
            {
                "work": {"type": "uri", "value": "http://publications.europa.eu/resource/cellar/abc123"},
                "title": {"type": "literal", "value": "Commission Delegated Regulation amending Annexes II and III to Regulation (EC) No 1223/2009 on cosmetic products", "xml:lang": "en"},
                "date": {"type": "literal", "value": "2025-03-15", "datatype": "http://www.w3.org/2001/XMLSchema#date"},
                "celex": {"type": "literal", "value": "32025R0123"},
            },
            {
                "work": {"type": "uri", "value": "http://publications.europa.eu/resource/cellar/def456"},
                "title": {"type": "literal", "value": "Regulation implementing Regulation (EC) No 1907/2006 (REACH) as regards restrictions on substances", "xml:lang": "en"},
                "date": {"type": "literal", "value": "2025-01-10", "datatype": "http://www.w3.org/2001/XMLSchema#date"},
                "celex": {"type": "literal", "value": "32025R0456"},
            },
        ]
    }
}


def test_parse_sparql_results():
    changes = _parse_sparql_results(SAMPLE_SPARQL_RESPONSE)
    assert len(changes) == 2

    c0 = changes[0]
    assert c0.source == SourceType.EU_OFFICIAL_JOURNAL
    assert c0.agency == "EU Commission"
    assert c0.jurisdiction == "EU"
    assert c0.stage == RegulationStage.AMENDMENT
    assert "1223/2009" in c0.title
    assert c0.url == "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32025R0123"

    c1 = changes[1]
    assert c1.stage == RegulationStage.FINAL_RULE  # "implementing"


def test_parse_sparql_empty():
    changes = _parse_sparql_results({"results": {"bindings": []}})
    assert changes == []


def test_parse_sparql_no_results_key():
    changes = _parse_sparql_results({})
    assert changes == []


@pytest.mark.asyncio
@respx.mock
async def test_fetch_eurlex_success():
    respx.get("https://publications.europa.eu/webapi/rdf/sparql").mock(
        return_value=Response(200, json=SAMPLE_SPARQL_RESPONSE)
    )
    changes = await fetch_eurlex_changes(date_from="2024-01-01")
    assert len(changes) == 2
    assert all(c.source == SourceType.EU_OFFICIAL_JOURNAL for c in changes)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_eurlex_error():
    respx.get("https://publications.europa.eu/webapi/rdf/sparql").mock(
        return_value=Response(403)
    )
    changes = await fetch_eurlex_changes()
    assert changes == []
