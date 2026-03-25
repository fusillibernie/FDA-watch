"""CourtListener RECAP client for federal court docket monitoring.

Uses the free CourtListener API to find federal court filings related to
food labeling, standards of identity, and consumer protection.

API docs: https://www.courtlistener.com/api/rest/v4/
Rate limit: 100 requests/hour (unauthenticated)
"""

import asyncio
import logging
import re
from datetime import datetime, timedelta

import httpx

from src.models.enums import ProductCategory, Severity, SourceType, ViolationType
from src.models.enforcement import RegulatoryAction

logger = logging.getLogger(__name__)

COURTLISTENER_API = "https://www.courtlistener.com/api/rest/v4/search/"

# Search queries to cover SOI and food/consumer protection cases
SEARCH_QUERIES = [
    '"standard of identity" food',
    '"misbranding" food label',
    'consumer protection "false advertising" food',
    '"slack fill"',
    '"ice cream" labeling',
    '"honey" mislabel',
    '"vanilla" "standard of identity"',
    '"olive oil" fraud',
    '"chocolate" "standard of identity"',
    '"parmesan" mislabel',
    '"maple syrup" labeling',
    '"mayo" "standard of identity"',
]

# Product category keywords — reused from classaction_client pattern
PRODUCT_KEYWORDS = {
    "food": ProductCategory.FOOD,
    "grocery": ProductCategory.FOOD,
    "cereal": ProductCategory.FOOD,
    "snack": ProductCategory.FOOD,
    "beverage": ProductCategory.FOOD,
    "juice": ProductCategory.FOOD,
    "organic": ProductCategory.FOOD,
    "natural": ProductCategory.FOOD,
    "protein bar": ProductCategory.FOOD,
    "dog food": ProductCategory.FOOD,
    "ice cream": ProductCategory.FOOD,
    "honey": ProductCategory.FOOD,
    "vanilla": ProductCategory.FOOD,
    "olive oil": ProductCategory.FOOD,
    "chocolate": ProductCategory.FOOD,
    "parmesan": ProductCategory.FOOD,
    "maple syrup": ProductCategory.FOOD,
    "mayo": ProductCategory.FOOD,
    "yogurt": ProductCategory.FOOD,
    "cheese": ProductCategory.FOOD,
    "butter": ProductCategory.FOOD,
    "milk": ProductCategory.FOOD,
    "supplement": ProductCategory.DIETARY_SUPPLEMENT,
    "vitamin": ProductCategory.DIETARY_SUPPLEMENT,
    "herbal": ProductCategory.DIETARY_SUPPLEMENT,
    "probiotic": ProductCategory.DIETARY_SUPPLEMENT,
    "protein powder": ProductCategory.DIETARY_SUPPLEMENT,
    "cosmetic": ProductCategory.COSMETIC,
    "skincare": ProductCategory.COSMETIC,
    "shampoo": ProductCategory.COSMETIC,
    "sunscreen": ProductCategory.COSMETIC,
    "drug": ProductCategory.OTC_DRUG,
    "medicine": ProductCategory.OTC_DRUG,
}

VIOLATION_KEYWORDS = {
    "mislabel": ViolationType.LABELING_VIOLATION,
    "labeling": ViolationType.LABELING_VIOLATION,
    "falsely advertised": ViolationType.DECEPTIVE_ADVERTISING,
    "false advertising": ViolationType.DECEPTIVE_ADVERTISING,
    "misleading": ViolationType.DECEPTIVE_ADVERTISING,
    "deceptive": ViolationType.DECEPTIVE_ADVERTISING,
    "fraud": ViolationType.DECEPTIVE_ADVERTISING,
    "misrepresent": ViolationType.DECEPTIVE_ADVERTISING,
    "contaminat": ViolationType.CONTAMINATION,
    "heavy metal": ViolationType.CONTAMINATION,
    "pfas": ViolationType.CONTAMINATION,
    "undeclared": ViolationType.UNDECLARED_INGREDIENT,
    "allergen": ViolationType.UNDECLARED_ALLERGEN,
    "health claim": ViolationType.UNSUBSTANTIATED_CLAIM,
    "unproven": ViolationType.UNSUBSTANTIATED_CLAIM,
    "standard of identity": ViolationType.STANDARDS_OF_IDENTITY,
    "not real": ViolationType.STANDARDS_OF_IDENTITY,
    "imitation": ViolationType.STANDARDS_OF_IDENTITY,
    "slack fill": ViolationType.STANDARDS_OF_IDENTITY,
    "misbranding": ViolationType.MISBRANDING,
    "adulterat": ViolationType.ADULTERATION,
}


def _classify_categories(text: str) -> list[ProductCategory]:
    """Classify product categories from text using keyword matching."""
    categories: list[ProductCategory] = []
    lower = text.lower()
    for keyword, cat in PRODUCT_KEYWORDS.items():
        if keyword in lower and cat not in categories:
            categories.append(cat)
    return categories


def _classify_violations(text: str) -> list[ViolationType]:
    """Classify violation types from text using keyword matching."""
    violations: list[ViolationType] = []
    lower = text.lower()
    for keyword, vtype in VIOLATION_KEYWORDS.items():
        if keyword in lower and vtype not in violations:
            violations.append(vtype)
    return violations or [ViolationType.DECEPTIVE_ADVERTISING]


def _extract_company(case_name: str) -> str:
    """Extract defendant company from case name.

    Federal case names typically follow:
    - "Plaintiff v. Defendant" or "Plaintiff vs. Defendant"
    - "In re: Company Name"
    - "United States v. Company"
    """
    # "In re: X" or "In Re X"
    match = re.search(r'[Ii]n\s+[Rr]e[:\s]+(.+?)(?:\s*$)', case_name)
    if match:
        return match.group(1).strip()[:200]

    # "X v. Y" or "X vs. Y" — defendant is after the v./vs.
    match = re.search(r'\sv\.?s?\.?\s+(.+?)(?:\s*$)', case_name)
    if match:
        return match.group(1).strip()[:200]

    # Fallback: first segment
    return case_name.split(",")[0].strip()[:200]


def _is_food_consumer_relevant(text: str) -> bool:
    """Check if text is relevant to food/consumer protection."""
    return len(_classify_categories(text)) > 0


def _parse_result(result: dict) -> RegulatoryAction | None:
    """Parse a single CourtListener search result into a RegulatoryAction.

    Handles both opinion results and RECAP docket results.
    """
    # RECAP docket fields use snake_case; opinion fields use camelCase
    case_name = result.get("caseName") or result.get("case_name") or ""
    docket_number = result.get("docketNumber") or result.get("docket_number") or ""
    date_filed = result.get("dateFiled") or result.get("date_filed") or ""
    court = result.get("court") or result.get("court_id") or ""
    absolute_url = result.get("absolute_url") or ""
    description = result.get("description") or ""
    docket_id = result.get("docket_id") or result.get("id") or ""

    if not case_name:
        return None

    # Combine text for classification
    full_text = f"{case_name} {description} {docket_number}"

    # Filter: only keep food/consumer/labeling cases
    if not _is_food_consumer_relevant(full_text):
        return None

    categories = _classify_categories(full_text)
    violations = _classify_violations(full_text)
    company = _extract_company(case_name)

    # Normalize date
    date_str = ""
    if date_filed:
        try:
            parsed = datetime.strptime(date_filed[:10], "%Y-%m-%d")
            date_str = parsed.strftime("%Y-%m-%d")
        except ValueError:
            date_str = datetime.now().strftime("%Y-%m-%d")
    else:
        date_str = datetime.now().strftime("%Y-%m-%d")

    source_id = f"cl-{docket_id}"
    url = f"https://www.courtlistener.com{absolute_url}" if absolute_url else None

    return RegulatoryAction(
        id=source_id,
        source=SourceType.COURTLISTENER,
        source_id=source_id,
        title=f"Federal Filing: {case_name}"[:200],
        description=f"{case_name} ({docket_number}) — {court}".strip(" —"),
        company=company,
        product_categories=categories,
        violation_types=violations,
        severity=Severity.ADVISORY,
        date=date_str,
        url=url,
        status="Filed",
    )


def parse_search_response(data: dict) -> list[RegulatoryAction]:
    """Parse a CourtListener search API response into RegulatoryAction objects."""
    actions: list[RegulatoryAction] = []
    results = data.get("results", [])
    for result in results:
        action = _parse_result(result)
        if action is not None:
            actions.append(action)
    return actions


async def fetch_courtlistener_dockets(
    date_from: str | None = None,
    max_pages: int = 3,
) -> list[RegulatoryAction]:
    """Fetch federal court docket filings related to food/consumer protection.

    Searches CourtListener RECAP dockets using multiple food/SOI queries.

    Args:
        date_from: ISO date string for incremental sync (YYYY-MM-DD)
        max_pages: Max pages to fetch per query

    Returns:
        List of RegulatoryAction records (only food/consumer-relevant cases)
    """
    all_actions: list[RegulatoryAction] = []
    seen_ids: set[str] = set()

    # Default to 90 days back if no date_from
    if not date_from:
        date_from = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")

    async with httpx.AsyncClient(
        timeout=30.0,
        follow_redirects=True,
    ) as client:
        for query in SEARCH_QUERIES:
            pages_fetched = 0
            next_url: str | None = None

            while pages_fetched < max_pages:
                try:
                    if next_url:
                        resp = await client.get(next_url)
                    else:
                        params = {
                            "q": query,
                            "type": "r",
                            "filed_after": date_from,
                            "order_by": "dateFiled desc",
                            "format": "json",
                        }
                        resp = await client.get(COURTLISTENER_API, params=params)

                    if resp.status_code == 429:
                        logger.warning("CourtListener rate limit hit, stopping")
                        break
                    if resp.status_code in (403, 404):
                        logger.warning("CourtListener returned %d for query: %s", resp.status_code, query)
                        break
                    resp.raise_for_status()

                except httpx.HTTPError as e:
                    logger.error("Failed to fetch CourtListener query '%s': %s", query, e)
                    break

                data = resp.json()
                actions = parse_search_response(data)

                for a in actions:
                    if a.source_id not in seen_ids:
                        seen_ids.add(a.source_id)
                        all_actions.append(a)

                # Handle pagination
                next_url = data.get("next")
                if not next_url:
                    break

                pages_fetched += 1

                # Respect rate limit: 1-second delay between requests
                await asyncio.sleep(1.0)

            # Rate limit delay between queries
            await asyncio.sleep(1.0)

    logger.info("Fetched %d CourtListener docket filings", len(all_actions))
    return all_actions
