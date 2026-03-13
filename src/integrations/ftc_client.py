"""FTC cases & proceedings client.

Fetches enforcement actions from the FTC's public case data endpoints
and maps them to RegulatoryAction models.

Primary source: FTC Enforcement Actions API
"""

import logging
import uuid
from datetime import datetime, timedelta

import httpx

from src.models.enums import ProductCategory, Severity, SourceType, ViolationType
from src.models.enforcement import RegulatoryAction

logger = logging.getLogger(__name__)

# FTC publishes case/proceeding data as JSON
FTC_CASES_URL = "https://www.ftc.gov/api/v0/data/cases"
FTC_CASE_BASE_URL = "https://www.ftc.gov/legal-library/browse/cases-proceedings"

# Map FTC case types to our categories
CASE_TYPE_MAP = {
    "food": [ProductCategory.FOOD],
    "dietary supplement": [ProductCategory.DIETARY_SUPPLEMENT],
    "health": [ProductCategory.DIETARY_SUPPLEMENT],
    "cosmetic": [ProductCategory.COSMETIC],
    "drug": [ProductCategory.OTC_DRUG],
    "otc": [ProductCategory.OTC_DRUG],
    "weight loss": [ProductCategory.DIETARY_SUPPLEMENT],
    "supplement": [ProductCategory.DIETARY_SUPPLEMENT],
}

# FTC-relevant violation types based on case description
VIOLATION_KEYWORDS = {
    "deceptive": ViolationType.DECEPTIVE_ADVERTISING,
    "misleading": ViolationType.DECEPTIVE_ADVERTISING,
    "false advertis": ViolationType.DECEPTIVE_ADVERTISING,
    "unsubstantiated": ViolationType.UNSUBSTANTIATED_CLAIM,
    "unfair": ViolationType.DECEPTIVE_ADVERTISING,
    "misbranding": ViolationType.MISBRANDING,
    "labeling": ViolationType.LABELING_VIOLATION,
}


def _classify_categories(text: str) -> list[ProductCategory]:
    """Extract product categories from case text."""
    categories: list[ProductCategory] = []
    lower = text.lower()
    for keyword, cats in CASE_TYPE_MAP.items():
        if keyword in lower:
            for c in cats:
                if c not in categories:
                    categories.append(c)
    return categories or [ProductCategory.FOOD]  # Default


def _classify_violations(text: str) -> list[ViolationType]:
    """Extract violation types from case text."""
    violations: list[ViolationType] = []
    lower = text.lower()
    for keyword, vtype in VIOLATION_KEYWORDS.items():
        if keyword in lower and vtype not in violations:
            violations.append(vtype)
    return violations or [ViolationType.DECEPTIVE_ADVERTISING]


def _parse_date(date_str: str | None) -> str:
    """Parse FTC date formats to ISO."""
    if not date_str:
        return ""
    # FTC API often returns ISO-ish dates
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y"):
        try:
            return datetime.strptime(date_str.strip()[:19], fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return date_str.strip()[:10]


def _map_case(case: dict) -> RegulatoryAction | None:
    """Map an FTC case record to a RegulatoryAction."""
    title = case.get("title", "") or case.get("name", "")
    if not title:
        return None

    case_id = case.get("id") or case.get("case_number") or uuid.uuid4().hex[:12]
    date = _parse_date(
        case.get("date") or case.get("filed_date") or case.get("created")
    )
    summary = case.get("body", "") or case.get("summary", "") or case.get("description", "")

    # Clean HTML from summary if present
    import re
    summary = re.sub(r"<[^>]+>", " ", summary)
    summary = re.sub(r"\s+", " ", summary).strip()

    combined_text = f"{title} {summary}"
    categories = _classify_categories(combined_text)
    violations = _classify_violations(combined_text)

    url = case.get("url") or case.get("path")
    if url and not url.startswith("http"):
        url = f"https://www.ftc.gov{url}"

    return RegulatoryAction(
        id=f"ftc-{case_id}",
        source=SourceType.FTC_ACTION,
        source_id=f"ftc-{case_id}",
        title=title[:200],
        description=summary[:2000] if summary else title,
        company=case.get("defendant", "") or case.get("company", "") or _extract_company(title),
        product_categories=categories,
        violation_types=violations,
        severity=Severity.WARNING,
        date=date,
        url=url,
        status=case.get("status") or case.get("case_status"),
        raw_data=case,
    )


def _extract_company(title: str) -> str:
    """Try to extract company name from title like 'FTC v. Company Name'."""
    for pattern in ["v. ", "vs. ", "V. ", "VS. "]:
        if pattern in title:
            parts = title.split(pattern, 1)
            if len(parts) > 1:
                return parts[1].split(",")[0].strip()
    return title[:100]


async def fetch_ftc_cases(
    date_from: str | None = None,
    max_pages: int = 10,
) -> list[RegulatoryAction]:
    """Fetch FTC enforcement cases.

    Args:
        date_from: ISO date string for incremental sync
        max_pages: Max pages to fetch (50 items per page)

    Returns:
        List of RegulatoryAction records
    """
    results: list[RegulatoryAction] = []
    page_size = 50

    cutoff = None
    if date_from:
        try:
            cutoff = datetime.strptime(date_from, "%Y-%m-%d")
        except ValueError:
            pass
    else:
        cutoff = datetime.now() - timedelta(days=730)

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        for page in range(max_pages):
            params = {
                "page": page,
                "size": page_size,
                "sort": "date",
                "direction": "desc",
            }

            try:
                resp = await client.get(FTC_CASES_URL, params=params)
                if resp.status_code in (404, 403):
                    logger.warning("FTC API returned %d, trying alternate approach", resp.status_code)
                    break
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPStatusError as e:
                logger.error("FTC API error page %d: %s", page, e)
                break
            except httpx.RequestError as e:
                logger.error("FTC request failed: %s", e)
                break
            except Exception as e:
                logger.error("FTC parse error: %s", e)
                break

            # Handle various response shapes
            cases = data if isinstance(data, list) else data.get("results", data.get("data", []))
            if not cases:
                break

            for case in cases:
                action = _map_case(case)
                if not action:
                    continue

                # Date filtering
                if cutoff and action.date:
                    try:
                        action_date = datetime.strptime(action.date, "%Y-%m-%d")
                        if action_date < cutoff:
                            continue
                    except ValueError:
                        pass

                results.append(action)

            # If we got fewer than page_size, we're done
            if len(cases) < page_size:
                break

    logger.info("Fetched %d FTC enforcement cases", len(results))
    return results
