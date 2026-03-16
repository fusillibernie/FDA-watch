"""EU Safety Gate (RAPEX) client.

Fetches product safety alerts from the OpenDataSoft mirror of the EU Safety Gate dataset.
The official EC endpoint is a JavaScript SPA that blocks programmatic access (403).
The OpenDataSoft API provides the same data as public JSON with no authentication.
"""

import logging
import uuid
from datetime import datetime, timedelta

import httpx

from src.models.enums import ProductCategory, Severity, SourceType, ViolationType
from src.models.enforcement import RegulatoryAction

logger = logging.getLogger(__name__)

# OpenDataSoft public mirror of EU Safety Gate / RAPEX data (~30k+ records)
RAPEX_API_URL = "https://public.opendatasoft.com/api/explore/v2.1/catalog/datasets/healthref-europe-rapex-en/records"

CATEGORY_KEYWORDS = {
    "cosmetic": ProductCategory.COSMETIC,
    "food": ProductCategory.FOOD,
    "food contact": ProductCategory.FOOD,
    "childcare": ProductCategory.DEVICE,
    "toy": ProductCategory.DEVICE,
    "chemical": ProductCategory.COSMETIC,
    "skin": ProductCategory.COSMETIC,
}

RISK_KEYWORDS = {
    "chemical": ViolationType.RESTRICTED_SUBSTANCE,
    "burns": ViolationType.PRODUCT_SAFETY_RISK,
    "choking": ViolationType.PRODUCT_SAFETY_RISK,
    "injuries": ViolationType.PRODUCT_SAFETY_RISK,
    "strangulation": ViolationType.PRODUCT_SAFETY_RISK,
    "electric shock": ViolationType.PRODUCT_SAFETY_RISK,
    "allergen": ViolationType.COSMETIC_SAFETY_CONCERN,
    "sensitisation": ViolationType.COSMETIC_SAFETY_CONCERN,
    "cosmetic": ViolationType.COSMETIC_SAFETY_CONCERN,
}

SEVERITY_MAP = {
    "serious": Severity.CLASS_I,
    "high": Severity.CLASS_I,
    "medium": Severity.CLASS_II,
    "low": Severity.CLASS_III,
}


def _classify_categories(text: str) -> list[ProductCategory]:
    categories: list[ProductCategory] = []
    lower = text.lower()
    for keyword, cat in CATEGORY_KEYWORDS.items():
        if keyword in lower and cat not in categories:
            categories.append(cat)
    return categories or [ProductCategory.DEVICE]


def _classify_risks(text: str) -> list[ViolationType]:
    violations: list[ViolationType] = []
    lower = text.lower()
    for keyword, vtype in RISK_KEYWORDS.items():
        if keyword in lower and vtype not in violations:
            violations.append(vtype)
    return violations or [ViolationType.PRODUCT_SAFETY_RISK]


def _classify_severity(risk_level: str) -> Severity:
    return SEVERITY_MAP.get(risk_level.lower().strip(), Severity.WARNING)


def _parse_rapex_alerts(records: list[dict]) -> list[RegulatoryAction]:
    actions: list[RegulatoryAction] = []

    for record in records:
        alert_number = record.get("alert_number", "") or record.get("alertNumber", "") or ""
        title = record.get("product_name", "") or record.get("title", "") or ""
        description = record.get("alert_type", "") or record.get("description", "") or title
        company = record.get("product_brand", "") or record.get("companyName", "") or "Unknown"
        product_category = record.get("product_category", "") or record.get("productCategory", "") or ""
        risk_level = record.get("alert_level", "") or record.get("riskLevel", "") or ""
        country = record.get("alert_country", "") or ""

        # Build richer description
        full_desc = description
        if country:
            full_desc = f"[{country}] {full_desc}"
        if product_category:
            full_desc += f" — Category: {product_category}"

        date_str = ""
        raw_date = record.get("alert_date", "") or record.get("notificationDate", "")
        if raw_date:
            try:
                date_str = datetime.strptime(str(raw_date)[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
            except ValueError:
                pass

        combined_text = f"{title} {full_desc} {product_category} {risk_level}"
        categories = _classify_categories(combined_text)
        violations = _classify_risks(combined_text)
        severity = _classify_severity(risk_level)

        source_id = f"rapex-{alert_number}" if alert_number else f"rapex-{uuid.uuid4().hex[:12]}"

        action = RegulatoryAction(
            id=source_id,
            source=SourceType.EU_RAPEX,
            source_id=source_id,
            title=title[:200] or f"RAPEX Alert {alert_number}",
            description=full_desc[:2000],
            company=company[:200],
            product_categories=categories,
            violation_types=violations,
            severity=severity,
            date=date_str,
            jurisdiction="EU",
            url=None,
            status=risk_level or None,
        )
        actions.append(action)

    return actions


async def fetch_rapex_alerts(
    date_from: str | None = None,
    max_records: int = 200,
) -> list[RegulatoryAction]:
    """Fetch EU Safety Gate (RAPEX) alerts from OpenDataSoft mirror.

    Args:
        date_from: ISO date string (YYYY-MM-DD) for incremental sync
        max_records: Maximum records to return

    Returns:
        List of RegulatoryAction records
    """
    start_date = date_from or (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")

    params = {
        "where": f"alert_date>='{start_date}'",
        "limit": min(max_records, 100),
        "offset": 0,
        "order_by": "alert_date DESC",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(RAPEX_API_URL, params=params)
            if resp.status_code in (403, 404, 429):
                logger.warning("RAPEX API returned %d", resp.status_code)
                return []
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        logger.error("Failed to fetch RAPEX alerts: %s", e)
        return []
    except Exception as e:
        logger.error("RAPEX response parse error: %s", e)
        return []

    records = data.get("results", [])
    if not isinstance(records, list):
        return []

    actions = _parse_rapex_alerts(records[:max_records])
    logger.info("Fetched %d RAPEX alerts", len(actions))
    return actions
