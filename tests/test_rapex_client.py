"""Tests for RAPEX client (OpenDataSoft API)."""

import pytest
import respx
from httpx import Response

from src.integrations.rapex_client import fetch_rapex_alerts, _parse_rapex_alerts
from src.models.enums import SourceType


def test_parse_rapex_alerts():
    records = [
        {
            "alert_number": "A12/01234/25",
            "product_name": "Cosmetic cream with banned substance",
            "alert_type": "Chemical risk: the product contains a restricted substance.",
            "product_brand": "Test Cosmetics EU",
            "product_category": "Cosmetics",
            "alert_level": "serious",
            "alert_date": "2025-06-15",
            "alert_country": "France",
        },
        {
            "alert_number": "A12/05678/25",
            "product_name": "Children's toy with choking hazard",
            "alert_type": "Choking risk due to small parts that can detach.",
            "product_brand": "Toy Maker Ltd",
            "product_category": "Toys",
            "alert_level": "high",
            "alert_date": "2025-07-01",
            "alert_country": "Germany",
        },
    ]

    actions = _parse_rapex_alerts(records)
    assert len(actions) == 2

    a0 = actions[0]
    assert a0.source == SourceType.EU_RAPEX
    assert a0.source_id == "rapex-A12/01234/25"
    assert a0.jurisdiction == "EU"
    assert a0.company == "Test Cosmetics EU"
    assert a0.date == "2025-06-15"

    a1 = actions[1]
    assert a1.source_id == "rapex-A12/05678/25"
    assert a1.jurisdiction == "EU"


def test_parse_empty_alerts():
    actions = _parse_rapex_alerts([])
    assert actions == []


# Also support legacy field names for backward compatibility
def test_parse_rapex_legacy_fields():
    records = [
        {
            "alertNumber": "A12/99999/25",
            "title": "Legacy format alert",
            "description": "Chemical risk",
            "companyName": "LegacyCo",
            "riskLevel": "medium",
            "notificationDate": "2025-08-01",
        },
    ]
    actions = _parse_rapex_alerts(records)
    assert len(actions) == 1
    assert actions[0].source_id == "rapex-A12/99999/25"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_rapex_alerts_success():
    respx.get("https://public.opendatasoft.com/api/explore/v2.1/catalog/datasets/healthref-europe-rapex-en/records").mock(
        return_value=Response(200, json={
            "total_count": 1,
            "results": [
                {
                    "alert_number": "A12/00001/25",
                    "product_name": "Test product alert",
                    "alert_type": "Test description",
                    "product_brand": "TestCo",
                    "alert_level": "medium",
                    "alert_date": "2025-08-01",
                }
            ]
        })
    )
    actions = await fetch_rapex_alerts(date_from="2025-01-01")
    assert len(actions) == 1
    assert actions[0].source == SourceType.EU_RAPEX
    assert actions[0].jurisdiction == "EU"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_rapex_alerts_error():
    respx.get("https://public.opendatasoft.com/api/explore/v2.1/catalog/datasets/healthref-europe-rapex-en/records").mock(
        return_value=Response(500)
    )
    actions = await fetch_rapex_alerts()
    assert actions == []


@pytest.mark.asyncio
@respx.mock
async def test_fetch_rapex_alerts_403():
    respx.get("https://public.opendatasoft.com/api/explore/v2.1/catalog/datasets/healthref-europe-rapex-en/records").mock(
        return_value=Response(403)
    )
    actions = await fetch_rapex_alerts()
    assert actions == []
