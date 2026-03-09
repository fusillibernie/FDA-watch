"""Client for the openFDA enforcement API."""

import logging
import uuid
from datetime import datetime, timedelta

import httpx

from src.models.enums import ProductCategory, Severity, SourceType, ViolationType
from src.models.enforcement import RegulatoryAction

logger = logging.getLogger(__name__)

OPENFDA_BASE = "https://api.fda.gov"
ENDPOINTS = {
    "food": f"{OPENFDA_BASE}/food/enforcement.json",
    "drug": f"{OPENFDA_BASE}/drug/enforcement.json",
}
PAGE_SIZE = 100
MAX_PAGES = 50  # Safety limit: 5000 records per endpoint per fetch


def _map_severity(classification: str) -> Severity:
    mapping = {
        "Class I": Severity.CLASS_I,
        "Class II": Severity.CLASS_II,
        "Class III": Severity.CLASS_III,
    }
    return mapping.get(classification, Severity.ADVISORY)


def _map_product_category(endpoint: str) -> list[ProductCategory]:
    if endpoint == "food":
        return [ProductCategory.FOOD]
    if endpoint == "drug":
        return [ProductCategory.OTC_DRUG]
    return []


def _build_search_query(date_from: str | None = None) -> str:
    """Build openFDA search query string for date filtering."""
    if date_from:
        return f"report_date:[{date_from} TO *]"
    # Default: last 2 years
    two_years_ago = (datetime.now() - timedelta(days=730)).strftime("%Y%m%d")
    return f"report_date:[{two_years_ago} TO *]"


def _map_record(record: dict, endpoint: str) -> RegulatoryAction:
    """Map an openFDA enforcement record to our unified model."""
    recall_number = record.get("recall_number", "")
    return RegulatoryAction(
        id=f"openfda-{recall_number}" if recall_number else f"openfda-{uuid.uuid4().hex[:12]}",
        source=SourceType.OPENFDA_ENFORCEMENT,
        source_id=recall_number,
        title=record.get("product_description", "")[:200],
        description=record.get("reason_for_recall", ""),
        company=record.get("recalling_firm", "Unknown"),
        product_categories=_map_product_category(endpoint),
        violation_types=[],  # Filled in by classifier
        severity=_map_severity(record.get("classification", "")),
        date=_parse_date(record.get("report_date", "")),
        url=f"https://api.fda.gov/food/enforcement.json?search=recall_number:{recall_number}"
        if recall_number
        else None,
        status=record.get("status", None),
        distribution=record.get("distribution_pattern", None),
        raw_data=record,
    )


def _parse_date(date_str: str) -> str:
    """Convert openFDA date (YYYYMMDD) to ISO format."""
    if len(date_str) == 8:
        try:
            return datetime.strptime(date_str, "%Y%m%d").strftime("%Y-%m-%d")
        except ValueError:
            pass
    return date_str


async def fetch_enforcement(
    endpoint: str = "food",
    date_from: str | None = None,
    api_key: str | None = None,
) -> list[RegulatoryAction]:
    """Fetch enforcement actions from openFDA.

    Args:
        endpoint: "food" or "drug"
        date_from: YYYYMMDD date string for incremental sync
        api_key: Optional openFDA API key for higher rate limits
    """
    url = ENDPOINTS.get(endpoint)
    if not url:
        raise ValueError(f"Unknown endpoint: {endpoint}")

    results: list[RegulatoryAction] = []
    search = _build_search_query(date_from)

    async with httpx.AsyncClient(timeout=30.0) as client:
        for page in range(MAX_PAGES):
            params: dict[str, str | int] = {
                "search": search,
                "limit": PAGE_SIZE,
                "skip": page * PAGE_SIZE,
            }
            if api_key:
                params["api_key"] = api_key

            try:
                resp = await client.get(url, params=params)
                if resp.status_code == 404:
                    # No results for this query
                    break
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPStatusError as e:
                logger.error("openFDA API error for %s page %d: %s", endpoint, page, e)
                break
            except httpx.RequestError as e:
                logger.error("openFDA request failed for %s: %s", endpoint, e)
                break

            records = data.get("results", [])
            if not records:
                break

            for record in records:
                results.append(_map_record(record, endpoint))

            # Check if we've fetched all available results
            total = data.get("meta", {}).get("results", {}).get("total", 0)
            if (page + 1) * PAGE_SIZE >= total:
                break

    logger.info("Fetched %d enforcement actions from openFDA/%s", len(results), endpoint)
    return results
