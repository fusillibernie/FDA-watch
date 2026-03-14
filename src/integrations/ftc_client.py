"""FTC cases & proceedings client.

Scrapes enforcement cases from ftc.gov/legal-library/browse/cases-proceedings.
FTC blocks API/JSON requests but serves HTML with a browser User-Agent.

Each case is an <article class="node node--type-case"> with:
  - Title in <h3 class="node-title"><a href="...">Title</a></h3>
  - Type of action in field--name-field-case-action-type
  - Date in <time datetime="...">
"""

import logging
import re
import uuid
from datetime import datetime, timedelta

import httpx

from src.models.enums import ProductCategory, Severity, SourceType, ViolationType
from src.models.enforcement import RegulatoryAction

logger = logging.getLogger(__name__)

FTC_BASE_URL = "https://www.ftc.gov"
FTC_CASES_URL = f"{FTC_BASE_URL}/legal-library/browse/cases-proceedings"

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
}

# Map case text to product categories
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
    return categories or [ProductCategory.FOOD]


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
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%B %d, %Y"):
        try:
            return datetime.strptime(date_str.strip()[:19], fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return date_str.strip()[:10]


def _extract_company(title: str) -> str:
    """Extract company/respondent name from FTC case titles.

    FTC titles use patterns like:
      "Company Name, In the Matter of"
      "FTC v. Company Name"
      "222-3135 Company Name et al."
    """
    # "In the Matter of" pattern
    match = re.search(r'^(.+?),?\s+In the Matter of', title, re.IGNORECASE)
    if match:
        name = match.group(1).strip()
        # Strip docket numbers
        name = re.sub(r'^\d{3}-\d{4}\s+', '', name)
        return name[:200]

    # "FTC v." pattern
    for pattern in ["v. ", "vs. ", "V. ", "FTC et al. v. "]:
        if pattern in title:
            parts = title.split(pattern, 1)
            if len(parts) > 1:
                return parts[1].split(",")[0].strip()[:200]

    # Strip leading docket number
    clean = re.sub(r'^\d{3}-\d{4}\s+', '', title)
    return clean.split(",")[0].strip()[:200]


def _parse_ftc_cases_html(html: str, date_from: str | None = None) -> list[RegulatoryAction]:
    """Parse FTC case entries from the cases-proceedings HTML page.

    Only extracts <article> elements with class "node--type-case".
    """
    results: list[RegulatoryAction] = []

    cutoff = None
    if date_from:
        try:
            cutoff = datetime.strptime(date_from, "%Y-%m-%d")
        except ValueError:
            pass
    else:
        cutoff = datetime.now() - timedelta(days=730)

    # Find all case articles: <article about="..." class="node node--type-case ...">
    case_pattern = re.compile(
        r'<article\s+about="([^"]*)"[^>]*node--type-case[^>]*>(.*?)</article>',
        re.DOTALL | re.IGNORECASE,
    )

    for match in case_pattern.finditer(html):
        case_path = match.group(1)
        block = match.group(2)

        # Extract title from <h3 class="node-title"><a href="...">Title</a></h3>
        title_match = re.search(
            r'<h3[^>]*class="[^"]*node-title[^"]*"[^>]*>\s*<a[^>]*>(.*?)</a>',
            block, re.DOTALL | re.IGNORECASE,
        )
        if not title_match:
            continue
        title = re.sub(r'<[^>]+>', '', title_match.group(1)).strip()
        if not title:
            continue

        # Extract date from <time datetime="...">
        date_match = re.search(r'<time\s+datetime="([^"]*)"', block)
        date_str = _parse_date(date_match.group(1)) if date_match else ""

        # Date filtering
        if cutoff and date_str:
            try:
                if datetime.strptime(date_str, "%Y-%m-%d") < cutoff:
                    continue
            except ValueError:
                pass

        # Extract type of action
        action_type = ""
        type_match = re.search(
            r'field--name-field-case-action-type.*?<div class="field__item">(.*?)</div>',
            block, re.DOTALL | re.IGNORECASE,
        )
        if type_match:
            action_type = re.sub(r'<[^>]+>', '', type_match.group(1)).strip()

        # Build URL
        url = f"{FTC_BASE_URL}{case_path}" if case_path else ""

        # Build a stable ID from the path
        path_slug = case_path.rstrip("/").split("/")[-1] if case_path else uuid.uuid4().hex[:12]
        case_id = f"ftc-{path_slug}"

        company = _extract_company(title)
        combined = f"{title} {action_type}"
        categories = _classify_categories(combined)
        violations = _classify_violations(combined)

        action = RegulatoryAction(
            id=case_id,
            source=SourceType.FTC_ACTION,
            source_id=case_id,
            title=title[:200],
            description=f"{title}. Type: {action_type}" if action_type else title,
            company=company,
            product_categories=categories,
            violation_types=violations,
            severity=Severity.WARNING,
            date=date_str,
            url=url,
            status=action_type or None,
        )
        results.append(action)

    return results


async def fetch_ftc_cases(
    date_from: str | None = None,
    max_pages: int = 5,
) -> list[RegulatoryAction]:
    """Fetch FTC enforcement cases from ftc.gov.

    Scrapes the Cases and Proceedings page with date filtering.

    Args:
        date_from: ISO date string for incremental sync
        max_pages: Max pages to scrape (20 cases per page)

    Returns:
        List of RegulatoryAction records
    """
    all_results: list[RegulatoryAction] = []
    seen_ids: set[str] = set()

    # Build base URL with date filter
    date_min = date_from or (datetime.now() - timedelta(days=730)).strftime("%Y-%m-%d")
    base_params = f"?field_date_text_702%5Bmin%5D={date_min}&items_per_page=20"

    async with httpx.AsyncClient(
        timeout=30.0,
        follow_redirects=True,
        headers=BROWSER_HEADERS,
    ) as client:
        for page in range(max_pages):
            url = f"{FTC_CASES_URL}{base_params}&page={page}"
            try:
                resp = await client.get(url)
                if resp.status_code in (403, 404, 429):
                    logger.warning("FTC returned %d on page %d", resp.status_code, page)
                    break
                resp.raise_for_status()
            except httpx.HTTPError as e:
                logger.error("Failed to fetch FTC cases page %d: %s", page, e)
                break

            cases = _parse_ftc_cases_html(resp.text, date_from)
            if not cases:
                break

            for case in cases:
                if case.source_id not in seen_ids:
                    seen_ids.add(case.source_id)
                    all_results.append(case)

    logger.info("Fetched %d FTC enforcement cases", len(all_results))
    return all_results
