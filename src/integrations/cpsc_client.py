"""CPSC product recall client.

Fetches consumer product recalls from the CPSC SaferProducts API.
Only keeps recalls relevant to food-contact, cosmetic, children's skin-contact,
or drug-adjacent products.
"""

import logging
import uuid
from datetime import datetime, timedelta

import httpx

from src.models.enums import ProductCategory, Severity, SourceType, ViolationType
from src.models.enforcement import RegulatoryAction

logger = logging.getLogger(__name__)

CPSC_API_URL = "https://www.saferproducts.gov/RestWebServices/Recall"

# Keywords that indicate product relevance
PRODUCT_FILTER_KEYWORDS = {
    "food": ProductCategory.FOOD,
    "beverage": ProductCategory.FOOD,
    "bottle": ProductCategory.FOOD,
    "cup": ProductCategory.FOOD,
    "plate": ProductCategory.FOOD,
    "cookware": ProductCategory.FOOD,
    "kitchen": ProductCategory.FOOD,
    "baby food": ProductCategory.FOOD,
    "infant formula": ProductCategory.FOOD,
    "cosmetic": ProductCategory.COSMETIC,
    "lotion": ProductCategory.COSMETIC,
    "cream": ProductCategory.COSMETIC,
    "shampoo": ProductCategory.COSMETIC,
    "soap": ProductCategory.COSMETIC,
    "sunscreen": ProductCategory.COSMETIC,
    "skincare": ProductCategory.COSMETIC,
    "skin": ProductCategory.COSMETIC,
    "children": ProductCategory.DEVICE,
    "infant": ProductCategory.DEVICE,
    "baby": ProductCategory.DEVICE,
    "toy": ProductCategory.DEVICE,
    "crib": ProductCategory.DEVICE,
    "pacifier": ProductCategory.DEVICE,
    "teething": ProductCategory.DEVICE,
    "supplement": ProductCategory.DIETARY_SUPPLEMENT,
    "vitamin": ProductCategory.DIETARY_SUPPLEMENT,
    "drug": ProductCategory.OTC_DRUG,
    "medicine": ProductCategory.OTC_DRUG,
    "thermometer": ProductCategory.OTC_DRUG,
}

VIOLATION_KEYWORDS = {
    "choking": ViolationType.CONTAMINATION,
    "burn": ViolationType.CONTAMINATION,
    "lead": ViolationType.CONTAMINATION,
    "chemical": ViolationType.CONTAMINATION,
    "toxic": ViolationType.CONTAMINATION,
    "injury": ViolationType.CONTAMINATION,
    "laceration": ViolationType.CONTAMINATION,
    "mislabel": ViolationType.LABELING_VIOLATION,
    "labeling": ViolationType.LABELING_VIOLATION,
    "undeclared": ViolationType.UNDECLARED_INGREDIENT,
}


def _classify_categories(text: str) -> list[ProductCategory]:
    categories: list[ProductCategory] = []
    lower = text.lower()
    for keyword, cat in PRODUCT_FILTER_KEYWORDS.items():
        if keyword in lower and cat not in categories:
            categories.append(cat)
    return categories


def _classify_violations(text: str) -> list[ViolationType]:
    violations: list[ViolationType] = []
    lower = text.lower()
    for keyword, vtype in VIOLATION_KEYWORDS.items():
        if keyword in lower and vtype not in violations:
            violations.append(vtype)
    return violations or [ViolationType.OTHER]


def _extract_company(record: dict) -> str:
    """Extract manufacturer/retailer name from CPSC record."""
    manufacturers = record.get("Manufacturers", [])
    if manufacturers:
        name = manufacturers[0].get("Name", "")
        if name:
            return name[:200]
    retailers = record.get("Retailers", [])
    if retailers:
        name = retailers[0].get("Name", "")
        if name:
            return name[:200]
    return "Unknown"


def _parse_cpsc_records(records: list[dict]) -> list[RegulatoryAction]:
    """Parse CPSC recall JSON records, filtering to relevant products only."""
    actions: list[RegulatoryAction] = []

    for record in records:
        recall_id = record.get("RecallID", "")
        title = record.get("Title", "") or ""
        description_parts = []
        for desc in record.get("Description", []):
            description_parts.append(desc.get("Description", ""))
        description = " ".join(description_parts) or title

        # Build product description text for classification
        product_text = title + " " + description
        for prod in record.get("Products", []):
            product_text += " " + prod.get("Description", "")

        categories = _classify_categories(product_text)
        if not categories:
            continue

        violations = _classify_violations(product_text)
        company = _extract_company(record)

        date_str = ""
        recall_date = record.get("RecallDate", "")
        if recall_date:
            try:
                date_str = datetime.strptime(
                    recall_date[:10], "%Y-%m-%d"
                ).strftime("%Y-%m-%d")
            except ValueError:
                date_str = recall_date[:10]

        url = record.get("URL", "")
        source_id = f"cpsc-{recall_id}" if recall_id else f"cpsc-{uuid.uuid4().hex[:12]}"

        action = RegulatoryAction(
            id=source_id,
            source=SourceType.CPSC_RECALL,
            source_id=source_id,
            title=title[:200] or f"CPSC Recall {recall_id}",
            description=description[:2000],
            company=company,
            product_categories=categories,
            violation_types=violations,
            severity=Severity.WARNING,
            date=date_str,
            url=url or None,
            status="Recalled",
        )
        actions.append(action)

    return actions


async def fetch_cpsc_recalls(
    date_from: str | None = None,
    max_records: int = 200,
) -> list[RegulatoryAction]:
    """Fetch CPSC product recalls from SaferProducts API.

    Args:
        date_from: ISO date string (YYYY-MM-DD) for incremental sync
        max_records: Maximum records to return

    Returns:
        List of RegulatoryAction records (only product-relevant recalls)
    """
    start_date = date_from or (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")

    params = {
        "format": "json",
        "RecallDateStart": start_date,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(CPSC_API_URL, params=params)
            if resp.status_code in (403, 404, 429):
                logger.warning("CPSC API returned %d", resp.status_code)
                return []
            resp.raise_for_status()
            records = resp.json()
    except httpx.HTTPError as e:
        logger.error("Failed to fetch CPSC recalls: %s", e)
        return []
    except Exception as e:
        logger.error("CPSC response parse error: %s", e)
        return []

    if not isinstance(records, list):
        return []

    actions = _parse_cpsc_records(records[:max_records])
    logger.info("Fetched %d relevant CPSC recalls from %d total", len(actions), len(records))
    return actions
