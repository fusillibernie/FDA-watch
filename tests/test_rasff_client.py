"""Tests for RASFF client (RASFF Window HTML scraper)."""

import pytest
import respx
from httpx import Response

from src.integrations.rasff_client import fetch_rasff_notifications, _parse_rasff_notifications, _parse_rasff_html
from src.models.enums import SourceType, ViolationType


def test_parse_rasff_notifications():
    notifications = [
        {
            "reference": "2025.1234",
            "subject": "Salmonella in chicken from Poland",
            "productCategory": "meat and meat products",
            "notificationType": "alert",
            "countryOrigin": "Poland",
            "notificationDate": "2025-06-20",
        },
        {
            "reference": "2025.5678",
            "subject": "Aflatoxin in peanuts from Argentina",
            "productCategory": "nuts and seeds",
            "notificationType": "border rejection",
            "countryOrigin": "Argentina",
            "notificationDate": "2025-07-10",
        },
    ]

    actions = _parse_rasff_notifications(notifications)
    assert len(actions) == 2

    a0 = actions[0]
    assert a0.source == SourceType.EU_RASFF
    assert a0.source_id == "rasff-2025.1234"
    assert a0.jurisdiction == "EU"
    assert ViolationType.FOOD_SAFETY_ALERT in a0.violation_types

    a1 = actions[1]
    assert a1.source_id == "rasff-2025.5678"
    assert "Argentina" in a1.description


def test_parse_empty_notifications():
    actions = _parse_rasff_notifications([])
    assert actions == []


def test_parse_rasff_html_with_embedded_json():
    html = """
    <html><body>
    <script>
    var data = [{"reference": "2025.9999", "subject": "Pesticide residues in apples"}];
    </script>
    </body></html>
    """
    actions = _parse_rasff_html(html)
    assert len(actions) == 1
    assert actions[0].source_id == "rasff-2025.9999"


def test_parse_rasff_html_with_table():
    html = """
    <html><body><table>
    <tr>
        <td>2025.1111</td>
        <td>alert</td>
        <td>Salmonella in poultry from Brazil - serious food safety concern</td>
        <td>2025-06-15</td>
    </tr>
    </table></body></html>
    """
    actions = _parse_rasff_html(html, date_from="2025-01-01")
    assert len(actions) == 1
    assert actions[0].source_id == "rasff-2025.1111"


def test_parse_rasff_html_empty():
    actions = _parse_rasff_html("<html><body></body></html>")
    assert actions == []


@pytest.mark.asyncio
@respx.mock
async def test_fetch_rasff_success():
    html = '<html><body><script>var x = {"reference": "2025.8888", "subject": "Mercury in fish"};</script></body></html>'
    respx.get("https://webgate.ec.europa.eu/rasff-window/screen/list").mock(
        return_value=Response(200, text=html)
    )
    actions = await fetch_rasff_notifications(date_from="2025-01-01")
    assert len(actions) == 1
    assert actions[0].source == SourceType.EU_RASFF
    assert actions[0].jurisdiction == "EU"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_rasff_error():
    respx.get("https://webgate.ec.europa.eu/rasff-window/screen/list").mock(
        return_value=Response(500)
    )
    actions = await fetch_rasff_notifications()
    assert actions == []


@pytest.mark.asyncio
@respx.mock
async def test_fetch_rasff_403():
    respx.get("https://webgate.ec.europa.eu/rasff-window/screen/list").mock(
        return_value=Response(403)
    )
    actions = await fetch_rasff_notifications()
    assert actions == []
