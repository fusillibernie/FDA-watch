"""Proposition 65 60-day notice client.

Scrapes OEHHA's Proposition 65 60-day notice listings.
HTML tables with: alleged violator, product, chemical, date, notice type.
"""

import logging
import re
import uuid
from datetime import datetime, timedelta

import httpx

from src.models.enums import ProductCategory, Severity, SourceType, ViolationType
from src.models.enforcement import RegulatoryAction

logger = logging.getLogger(__name__)

PROP65_URL = "https://oehha.ca.gov/proposition-65/60-day-notice-search-results"

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
}

PRODUCT_KEYWORDS = {
    "food": ProductCategory.FOOD,
    "beverage": ProductCategory.FOOD,
    "candy": ProductCategory.FOOD,
    "chocolate": ProductCategory.FOOD,
    "juice": ProductCategory.FOOD,
    "snack": ProductCategory.FOOD,
    "cereal": ProductCategory.FOOD,
    "supplement": ProductCategory.DIETARY_SUPPLEMENT,
    "vitamin": ProductCategory.DIETARY_SUPPLEMENT,
    "herbal": ProductCategory.DIETARY_SUPPLEMENT,
    "protein": ProductCategory.DIETARY_SUPPLEMENT,
    "cosmetic": ProductCategory.COSMETIC,
    "lotion": ProductCategory.COSMETIC,
    "cream": ProductCategory.COSMETIC,
    "shampoo": ProductCategory.COSMETIC,
    "sunscreen": ProductCategory.COSMETIC,
    "makeup": ProductCategory.COSMETIC,
    "skincare": ProductCategory.COSMETIC,
    "drug": ProductCategory.OTC_DRUG,
    "medicine": ProductCategory.OTC_DRUG,
}

VIOLATION_MAP = {
    "lead": ViolationType.CONTAMINATION,
    "cadmium": ViolationType.CONTAMINATION,
    "mercury": ViolationType.CONTAMINATION,
    "arsenic": ViolationType.CONTAMINATION,
    "acrylamide": ViolationType.CONTAMINATION,
    "bpa": ViolationType.CONTAMINATION,
    "phthalate": ViolationType.CONTAMINATION,
    "formaldehyde": ViolationType.CONTAMINATION,
    "benzene": ViolationType.CONTAMINATION,
    "pfas": ViolationType.CONTAMINATION,
    "labeling": ViolationType.LABELING_VIOLATION,
    "warning": ViolationType.LABELING_VIOLATION,
}


def _classify_categories(text: str) -> list[ProductCategory]:
    categories: list[ProductCategory] = []
    lower = text.lower()
    for keyword, cat in PRODUCT_KEYWORDS.items():
        if keyword in lower and cat not in categories:
            categories.append(cat)
    return categories


def _classify_violations(text: str) -> list[ViolationType]:
    violations: list[ViolationType] = []
    lower = text.lower()
    for keyword, vtype in VIOLATION_MAP.items():
        if keyword in lower and vtype not in violations:
            violations.append(vtype)
    return violations or [ViolationType.CONTAMINATION]


def _extract_company(text: str) -> str:
    """Extract company name from alleged violator cell."""
    # Strip common suffixes
    name = re.sub(r'\s*(?:d/b/a|aka|formerly)\s+.*$', '', text, flags=re.IGNORECASE)
    return name.strip()[:200] or "Unknown"


def _parse_prop65_html(html: str, date_from: str | None = None) -> list[RegulatoryAction]:
    """Parse Prop 65 60-day notice listings from HTML.

    Looks for table rows with notice data.
    """
    actions: list[RegulatoryAction] = []
    seen_ids: set[str] = set()

    cutoff = None
    if date_from:
        try:
            cutoff = datetime.strptime(date_from, "%Y-%m-%d")
        except ValueError:
            pass
    else:
        cutoff = datetime.now() - timedelta(days=730)

    # Find table rows: <tr> blocks with <td> cells
    row_pattern = re.compile(r'<tr[^>]*>(.*?)</tr>', re.DOTALL | re.IGNORECASE)
    cell_pattern = re.compile(r'<td[^>]*>(.*?)</td>', re.DOTALL | re.IGNORECASE)

    for row_match in row_pattern.finditer(html):
        row_html = row_match.group(1)
        cells = cell_pattern.findall(row_html)
        if len(cells) < 4:
            continue

        # Clean cell contents
        clean_cells = [re.sub(r'<[^>]+>', '', c).strip() for c in cells]

        # Try to identify columns — look for date-like content
        date_str = ""
        company = ""
        product = ""
        chemical = ""

        # Common format: Date | Alleged Violator | Product | Chemical | Notice Type
        # or: Alleged Violator | Product | Chemical | Date | Notice Type
        for i, cell in enumerate(clean_cells):
            # Date detection
            date_match = re.search(
                r'(\d{1,2}/\d{1,2}/\d{2,4})', cell
            )
            if date_match and not date_str:
                raw = date_match.group(1)
                for fmt in ("%m/%d/%Y", "%m/%d/%y"):
                    try:
                        date_str = datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
                        break
                    except ValueError:
                        continue

        # Assume common column order
        if len(clean_cells) >= 5:
            company = clean_cells[0] or clean_cells[1]
            product = clean_cells[1] if clean_cells[0] == company else clean_cells[2]
            chemical = clean_cells[2] if product == clean_cells[1] else clean_cells[3]
        elif len(clean_cells) >= 4:
            company = clean_cells[0]
            product = clean_cells[1]
            chemical = clean_cells[2]

        if not company or company.lower() in ("alleged violator", "company", "respondent"):
            continue

        # Date filtering
        if cutoff and date_str:
            try:
                if datetime.strptime(date_str, "%Y-%m-%d") < cutoff:
                    continue
            except ValueError:
                pass

        combined = f"{company} {product} {chemical}"
        categories = _classify_categories(combined)
        if not categories:
            # Prop 65 is broadly relevant — default to food
            categories = [ProductCategory.FOOD]

        violations = _classify_violations(combined)
        company_name = _extract_company(company)

        # Stable ID from company + date
        slug = re.sub(r'[^a-z0-9]+', '-', company_name.lower())[:40]
        notice_id = f"p65-{slug}-{date_str or uuid.uuid4().hex[:8]}"
        if notice_id in seen_ids:
            notice_id = f"{notice_id}-{uuid.uuid4().hex[:4]}"
        seen_ids.add(notice_id)

        title = f"Prop 65 Notice: {company_name}"
        if product:
            title += f" - {product}"
        description = f"Alleged Violator: {company_name}."
        if product:
            description += f" Product: {product}."
        if chemical:
            description += f" Chemical: {chemical}."

        action = RegulatoryAction(
            id=notice_id,
            source=SourceType.PROP_65,
            source_id=notice_id,
            title=title[:200],
            description=description[:2000],
            company=company_name,
            product_categories=categories,
            violation_types=violations,
            severity=Severity.WARNING,
            date=date_str or datetime.now().strftime("%Y-%m-%d"),
            url="https://oehha.ca.gov/proposition-65/60-day-notice-search-results",
            status="60-Day Notice",
        )
        actions.append(action)

    return actions


async def fetch_prop65_notices(
    date_from: str | None = None,
    max_pages: int = 3,
) -> list[RegulatoryAction]:
    """Fetch Prop 65 60-day notices from OEHHA.

    Args:
        date_from: ISO date string for incremental sync
        max_pages: Max pages to scrape

    Returns:
        List of RegulatoryAction records
    """
    all_actions: list[RegulatoryAction] = []
    seen_ids: set[str] = set()

    async with httpx.AsyncClient(
        timeout=30.0,
        follow_redirects=True,
        headers=BROWSER_HEADERS,
    ) as client:
        for page in range(max_pages):
            url = PROP65_URL if page == 0 else f"{PROP65_URL}?page={page}"
            try:
                resp = await client.get(url)
                if resp.status_code in (403, 404, 429):
                    logger.warning("OEHHA returned %d on page %d", resp.status_code, page)
                    break
                resp.raise_for_status()
            except httpx.HTTPError as e:
                logger.error("Failed to fetch Prop 65 page %d: %s", page, e)
                break

            actions = _parse_prop65_html(resp.text, date_from)
            if not actions:
                break

            for a in actions:
                if a.source_id not in seen_ids:
                    seen_ids.add(a.source_id)
                    all_actions.append(a)

    logger.info("Fetched %d Prop 65 notices", len(all_actions))
    return all_actions
