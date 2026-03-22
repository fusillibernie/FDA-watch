"""Tests for FDA Guidance Documents client."""

import pytest
import respx
from httpx import Response

from src.integrations.fda_guidance_client import fetch_fda_guidance, _parse_fda_guidance_html
from src.models.enums import RegulationStage, SourceType


SAMPLE_HTML = """
<html><body>
<div>
  <span>03/15/2025</span>
  <a href="/regulatory-information/guidance-cosmetic-labeling-requirements">Draft Guidance: Cosmetic Labeling Requirements Under MoCRA</a>
  <span>01/20/2025</span>
  <a href="/regulatory-information/guidance-dietary-supplement-cgmp">Final Guidance for Industry: Dietary Supplement CGMP Compliance</a>
</div>
</body></html>
"""


def test_parse_fda_guidance():
    changes = _parse_fda_guidance_html(SAMPLE_HTML, date_from="2024-01-01")
    assert len(changes) == 2

    c0 = changes[0]
    assert c0.source == SourceType.FDA_GUIDANCE
    assert c0.stage == RegulationStage.GUIDANCE_DRAFT
    assert c0.agency == "FDA"
    assert c0.jurisdiction == "US"
    assert "cosmetic" in c0.title.lower()

    c1 = changes[1]
    assert c1.stage == RegulationStage.GUIDANCE_FINAL


def test_parse_fda_guidance_empty():
    changes = _parse_fda_guidance_html("<html><body></body></html>")
    assert changes == []


def test_parse_fda_guidance_date_filter():
    changes = _parse_fda_guidance_html(SAMPLE_HTML, date_from="2025-02-01")
    assert len(changes) == 1
    assert "cosmetic" in changes[0].title.lower()


@pytest.mark.asyncio
@respx.mock
async def test_fetch_fda_guidance_success():
    # JSON endpoint fails, falls back to HTML
    respx.get("https://www.fda.gov/datatables-json/search-for-guidance.json").mock(
        return_value=Response(503)
    )
    respx.get("https://www.fda.gov/regulatory-information/search-fda-guidance-documents").mock(
        return_value=Response(200, text=SAMPLE_HTML)
    )
    changes = await fetch_fda_guidance(date_from="2024-01-01")
    assert len(changes) == 2
    assert all(c.source == SourceType.FDA_GUIDANCE for c in changes)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_fda_guidance_json_success():
    # JSON endpoint works
    respx.get("https://www.fda.gov/datatables-json/search-for-guidance.json").mock(
        return_value=Response(200, json=[
            {
                "field_0": '<a href="/regulatory-information/guidance-cosmetic-safety">Draft Guidance: Cosmetic Safety Standards</a>',
                "field_1": "03/15/2025",
                "field_2": "Draft",
            },
        ])
    )
    changes = await fetch_fda_guidance(date_from="2024-01-01")
    assert len(changes) == 1
    assert changes[0].source == SourceType.FDA_GUIDANCE
    assert "cosmetic" in changes[0].title.lower()


@pytest.mark.asyncio
@respx.mock
async def test_fetch_fda_guidance_error():
    respx.get("https://www.fda.gov/datatables-json/search-for-guidance.json").mock(
        return_value=Response(503)
    )
    respx.get("https://www.fda.gov/regulatory-information/search-fda-guidance-documents").mock(
        return_value=Response(403)
    )
    changes = await fetch_fda_guidance()
    assert changes == []
