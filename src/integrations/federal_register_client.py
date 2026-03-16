"""Federal Register client.

Fetches proposed rules, final rules, and notices from the Federal Register API.
Focuses on FDA, FTC, and CPSC documents relevant to food, cosmetics, OTC, and devices.
"""

import logging
import uuid
from datetime import datetime, timedelta

import httpx

from src.models.enums import ProductCategory, RegulationStage, SourceType
from src.models.regulation import RegulationChange

logger = logging.getLogger(__name__)

FR_API_URL = "https://www.federalregister.gov/api/v1/documents.json"

# Agencies to monitor
AGENCIES = [
    "food-and-drug-administration",
    "federal-trade-commission",
    "consumer-product-safety-commission",
]

# Federal Register type → RegulationStage
TYPE_MAP = {
    "Rule": RegulationStage.FINAL_RULE,
    "Proposed Rule": RegulationStage.PROPOSED_RULE,
    "Notice": RegulationStage.NOTICE,
    "Presidential Document": RegulationStage.NOTICE,
}

# API type filter codes (different from display names returned in results)
FR_TYPE_CODES = ["RULE", "PRORULE", "NOTICE"]

# Keywords → ProductCategory
CATEGORY_KEYWORDS = {
    "food": ProductCategory.FOOD,
    "dietary supplement": ProductCategory.DIETARY_SUPPLEMENT,
    "nutrition": ProductCategory.FOOD,
    "food additive": ProductCategory.FOOD,
    "GRAS": ProductCategory.FOOD,
    "food labeling": ProductCategory.FOOD,
    "allergen": ProductCategory.FOOD,
    "cosmetic": ProductCategory.COSMETIC,
    "sunscreen": ProductCategory.COSMETIC,
    "color additive": ProductCategory.COSMETIC,
    "over-the-counter": ProductCategory.OTC_DRUG,
    "OTC": ProductCategory.OTC_DRUG,
    "drug": ProductCategory.OTC_DRUG,
    "monograph": ProductCategory.OTC_DRUG,
    "device": ProductCategory.DEVICE,
    "medical device": ProductCategory.DEVICE,
    "in vitro diagnostic": ProductCategory.DEVICE,
    "510(k)": ProductCategory.DEVICE,
    "advertising": ProductCategory.FOOD,
    "deceptive": ProductCategory.FOOD,
}

# Agency name mapping
AGENCY_MAP = {
    "food-and-drug-administration": "FDA",
    "federal-trade-commission": "FTC",
    "consumer-product-safety-commission": "CPSC",
}

FR_FIELDS = [
    "document_number",
    "title",
    "abstract",
    "agencies",
    "type",
    "publication_date",
    "effective_on",
    "comments_close_on",
    "html_url",
    "cfr_references",
]


def _classify_categories(text: str) -> list[ProductCategory]:
    categories: list[ProductCategory] = []
    lower = text.lower()
    for keyword, cat in CATEGORY_KEYWORDS.items():
        if keyword.lower() in lower and cat not in categories:
            categories.append(cat)
    return categories or [ProductCategory.FOOD]


def _map_stage(fr_type: str, title: str) -> RegulationStage:
    stage = TYPE_MAP.get(fr_type, RegulationStage.NOTICE)
    lower = title.lower()
    if "interim final" in lower:
        return RegulationStage.INTERIM_FINAL_RULE
    if "advance notice" in lower or "anprm" in lower:
        return RegulationStage.ADVANCE_NOTICE
    if "draft guidance" in lower:
        return RegulationStage.GUIDANCE_DRAFT
    if "final guidance" in lower or "guidance for industry" in lower:
        return RegulationStage.GUIDANCE_FINAL
    return stage


def _extract_agency(agencies: list[dict]) -> str:
    # Prefer child agencies (FDA, FTC, CPSC) over parent depts (HHS, etc.)
    for a in agencies:
        slug = a.get("slug", "")
        if slug in AGENCY_MAP:
            return AGENCY_MAP[slug]
    # Fallback to first named agency
    for a in agencies:
        name = a.get("name", "")
        if name:
            return name
    return "FDA"


def _extract_cfr_refs(cfr_references: list[dict] | None) -> list[str]:
    if not cfr_references:
        return []
    refs = []
    for ref in cfr_references:
        title = ref.get("title", "")
        parts = ref.get("parts", [])
        for part in parts:
            refs.append(f"{title} CFR {part}")
    return refs[:10]


def _parse_fr_documents(documents: list[dict]) -> list[RegulationChange]:
    changes: list[RegulationChange] = []

    for doc in documents:
        doc_number = doc.get("document_number", "")
        title = doc.get("title", "") or ""
        abstract = doc.get("abstract", "") or ""
        fr_type = doc.get("type", "") or ""
        agencies = doc.get("agencies", []) or []
        pub_date = doc.get("publication_date", "") or ""
        effective_date = doc.get("effective_on") or None
        comments_close = doc.get("comments_close_on") or None
        url = doc.get("html_url", "") or ""
        cfr_refs_raw = doc.get("cfr_references") or []

        combined = f"{title} {abstract}"
        categories = _classify_categories(combined)
        stage = _map_stage(fr_type, title)
        agency = _extract_agency(agencies)
        cfr_refs = _extract_cfr_refs(cfr_refs_raw)

        source_id = f"fr-{doc_number}" if doc_number else f"fr-{uuid.uuid4().hex[:12]}"

        change = RegulationChange(
            id=source_id,
            source=SourceType.FEDERAL_REGISTER,
            source_id=source_id,
            title=title[:300],
            summary=abstract[:2000] or title,
            agency=agency,
            stage=stage,
            product_categories=categories,
            date_published=pub_date,
            date_effective=effective_date,
            date_comments_close=comments_close,
            jurisdiction="US",
            url=url or None,
            cfr_references=cfr_refs or None,
        )
        changes.append(change)

    return changes


async def fetch_federal_register(
    date_from: str | None = None,
    agencies: list[str] | None = None,
    max_records: int = 200,
) -> list[RegulationChange]:
    """Fetch regulation documents from the Federal Register API.

    Args:
        date_from: ISO date string (YYYY-MM-DD) for incremental sync
        agencies: Agency slugs to filter (defaults to FDA, FTC, CPSC)
        max_records: Maximum records to return

    Returns:
        List of RegulationChange records
    """
    start_date = date_from or (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
    agency_list = agencies or AGENCIES

    params = {
        "conditions[publication_date][gte]": start_date,
        "conditions[type][]": FR_TYPE_CODES,
        "fields[]": FR_FIELDS,
        "per_page": min(max_records, 100),
        "order": "newest",
    }
    for ag in agency_list:
        params.setdefault("conditions[agencies][]", [])
        if isinstance(params["conditions[agencies][]"], list):
            params["conditions[agencies][]"].append(ag)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(FR_API_URL, params=params)
            if resp.status_code in (403, 404, 429):
                logger.warning("Federal Register API returned %d", resp.status_code)
                return []
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        logger.error("Failed to fetch Federal Register: %s", e)
        return []
    except Exception as e:
        logger.error("Federal Register parse error: %s", e)
        return []

    documents = data.get("results", [])
    if not isinstance(documents, list):
        return []

    changes = _parse_fr_documents(documents[:max_records])
    logger.info("Fetched %d Federal Register documents", len(changes))
    return changes
