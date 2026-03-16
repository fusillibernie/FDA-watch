"""Tests for IFRA amendments client."""

import pytest
import respx
from httpx import Response

from src.integrations.ifra_client import fetch_ifra_amendments, _parse_ifra_html
from src.models.enums import RegulationStage, SourceType, ProductCategory


SAMPLE_HTML = """
<html><body>
<div>
  <p>15 June 2025</p>
  <a href="/safe-use/amendments/51st-amendment">IFRA 51st Amendment - Restriction Updates</a>
  <p>20 January 2025</p>
  <a href="/safe-use/standards/standard-update-50">IFRA Standard 50th Amendment</a>
</div>
</body></html>
"""


def test_parse_ifra():
    changes = _parse_ifra_html(SAMPLE_HTML, date_from="2024-01-01")
    assert len(changes) == 2

    c0 = changes[0]
    assert c0.source == SourceType.IFRA_AMENDMENT
    assert c0.stage == RegulationStage.AMENDMENT
    assert c0.agency == "IFRA"
    assert c0.jurisdiction == "EU"
    assert ProductCategory.COSMETIC in c0.product_categories
    assert "ifra-amendment-51" in c0.source_id


def test_parse_ifra_empty():
    changes = _parse_ifra_html("<html><body></body></html>")
    assert changes == []


def test_parse_ifra_date_filter():
    changes = _parse_ifra_html(SAMPLE_HTML, date_from="2025-03-01")
    assert len(changes) == 1
    assert "51" in changes[0].source_id


@pytest.mark.asyncio
@respx.mock
async def test_fetch_ifra_success():
    respx.get("https://ifrafragrance.org/standards-library").mock(
        return_value=Response(200, text=SAMPLE_HTML)
    )
    changes = await fetch_ifra_amendments(date_from="2024-01-01")
    assert len(changes) == 2
    assert all(c.source == SourceType.IFRA_AMENDMENT for c in changes)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_ifra_error():
    respx.get("https://ifrafragrance.org/standards-library").mock(
        return_value=Response(500)
    )
    changes = await fetch_ifra_amendments()
    assert changes == []
