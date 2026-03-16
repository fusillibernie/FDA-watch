"""Tests for SCCS client."""

import pytest
import respx
from httpx import Response

from src.integrations.sccs_client import fetch_sccs_opinions, _parse_sccs_opinions
from src.models.enums import SourceType, ViolationType


SAMPLE_HTML = """
<html><body>
<div>
  <p>15 March 2025</p>
  <a href="/scientific-committees/opinion/sccs-1234-56">SCCS/1234/56 - Opinion on Titanium Dioxide (nano)</a>
  <p>20 January 2025</p>
  <a href="/scientific-committees/opinion/sccs-5678-90">SCCS/5678/90 - Opinion on Methylisothiazolinone (sensitisation concern)</a>
</div>
</body></html>
"""


def test_parse_sccs_opinions():
    actions = _parse_sccs_opinions(SAMPLE_HTML, date_from="2024-01-01")
    assert len(actions) == 2

    a0 = actions[0]
    assert a0.source == SourceType.EU_SCCS
    assert a0.source_id == "sccs-1234-56"
    assert a0.jurisdiction == "EU"
    assert "Titanium Dioxide" in a0.description

    a1 = actions[1]
    assert a1.source_id == "sccs-5678-90"
    assert ViolationType.COSMETIC_SAFETY_CONCERN in a1.violation_types


def test_parse_sccs_empty():
    actions = _parse_sccs_opinions("<html><body></body></html>")
    assert actions == []


def test_parse_sccs_date_filter():
    actions = _parse_sccs_opinions(SAMPLE_HTML, date_from="2025-02-01")
    # Only the March 2025 opinion should pass
    assert len(actions) == 1
    assert actions[0].source_id == "sccs-1234-56"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_sccs_success():
    respx.get("https://health.ec.europa.eu/scientific-committees/scientific-committee-consumer-safety-sccs/sccs-opinions_en").mock(
        return_value=Response(200, text=SAMPLE_HTML)
    )
    actions = await fetch_sccs_opinions(date_from="2024-01-01")
    assert len(actions) == 2
    assert all(a.source == SourceType.EU_SCCS for a in actions)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_sccs_error():
    respx.get("https://health.ec.europa.eu/scientific-committees/scientific-committee-consumer-safety-sccs/sccs-opinions_en").mock(
        return_value=Response(403)
    )
    actions = await fetch_sccs_opinions()
    assert actions == []
