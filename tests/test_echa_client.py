"""Tests for ECHA/REACH client (Candidate List scraper)."""

import pytest
import respx
from httpx import Response

from src.integrations.echa_client import fetch_echa_substances, _parse_echa_substances, _parse_echa_html
from src.models.enums import SourceType, ViolationType


def test_parse_echa_substances():
    substances = [
        {
            "ec_number": "200-001-8",
            "cas_number": "50-00-0",
            "name": "Formaldehyde",
            "reason": "Carcinogenic (CMR), Mutagenic",
            "type": "Candidate List",
            "date": "2025-01-15",
        },
        {
            "ec_number": "201-245-8",
            "cas_number": "80-05-7",
            "name": "Bisphenol A",
            "reason": "Endocrine disrupting properties",
            "type": "Restriction",
            "date": "2025-03-01",
        },
    ]

    actions = _parse_echa_substances(substances)
    assert len(actions) == 2

    a0 = actions[0]
    assert a0.source == SourceType.EU_ECHA_REACH
    assert "echa-200-001-8" in a0.source_id
    assert a0.jurisdiction == "EU"
    assert ViolationType.RESTRICTED_SUBSTANCE in a0.violation_types
    assert "CAS: 50-00-0" in a0.description

    a1 = actions[1]
    assert "echa-201-245-8" in a1.source_id
    assert ViolationType.COSMETIC_SAFETY_CONCERN in a1.violation_types


def test_parse_empty_substances():
    actions = _parse_echa_substances([])
    assert actions == []


def test_parse_echa_html():
    html = """
    <html><body><table>
    <tr>
        <td>Formaldehyde</td>
        <td>200-001-8</td>
        <td>50-00-0</td>
        <td>2025-01-15</td>
        <td>Carcinogenic (CMR)</td>
    </tr>
    <tr>
        <td>Bisphenol A</td>
        <td>201-245-8</td>
        <td>80-05-7</td>
        <td>2025-03-01</td>
        <td>Endocrine disrupting properties</td>
    </tr>
    </table></body></html>
    """
    actions = _parse_echa_html(html, date_from="2024-01-01")
    assert len(actions) == 2
    assert all(a.source == SourceType.EU_ECHA_REACH for a in actions)
    assert all(a.jurisdiction == "EU" for a in actions)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_echa_success():
    html = """
    <html><body><table>
    <tr>
        <td>Test Substance</td>
        <td>999-999-9</td>
        <td>12345-67-8</td>
        <td>2025-06-01</td>
        <td>SVHC candidate</td>
    </tr>
    </table></body></html>
    """
    respx.get("https://echa.europa.eu/candidate-list-table").mock(
        return_value=Response(200, text=html)
    )
    actions = await fetch_echa_substances(date_from="2025-01-01")
    assert len(actions) == 1
    assert actions[0].source == SourceType.EU_ECHA_REACH
    assert actions[0].jurisdiction == "EU"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_echa_error():
    respx.get("https://echa.europa.eu/candidate-list-table").mock(
        return_value=Response(500)
    )
    actions = await fetch_echa_substances()
    assert actions == []


@pytest.mark.asyncio
@respx.mock
async def test_fetch_echa_403():
    respx.get("https://echa.europa.eu/candidate-list-table").mock(
        return_value=Response(403)
    )
    actions = await fetch_echa_substances()
    assert actions == []
