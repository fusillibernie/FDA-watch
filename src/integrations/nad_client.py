"""NAD (National Advertising Division) decision client.

Scrapes BBB National Programs press releases for NAD case decisions.
Falls back to news/press release pages rather than the JS-rendered case library.

Heavy category filtering — only keeps food, supplement, cosmetic, OTC drug cases.
"""

import logging
import re
import uuid
from datetime import datetime, timedelta

import httpx

from src.models.enums import ProductCategory, Severity, SourceType, ViolationType
from src.models.enforcement import RegulatoryAction

logger = logging.getLogger(__name__)

NAD_URL = "https://bbbprograms.org/media-center"

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
}

PRODUCT_KEYWORDS = {
    "food": ProductCategory.FOOD,
    "beverage": ProductCategory.FOOD,
    "snack": ProductCategory.FOOD,
    "cereal": ProductCategory.FOOD,
    "dairy": ProductCategory.FOOD,
    "yogurt": ProductCategory.FOOD,
    "juice": ProductCategory.FOOD,
    "water": ProductCategory.FOOD,
    "coffee": ProductCategory.FOOD,
    "tea": ProductCategory.FOOD,
    "protein bar": ProductCategory.FOOD,
    "supplement": ProductCategory.DIETARY_SUPPLEMENT,
    "vitamin": ProductCategory.DIETARY_SUPPLEMENT,
    "probiotic": ProductCategory.DIETARY_SUPPLEMENT,
    "nutraceutical": ProductCategory.DIETARY_SUPPLEMENT,
    "weight loss": ProductCategory.DIETARY_SUPPLEMENT,
    "diet": ProductCategory.DIETARY_SUPPLEMENT,
    "cosmetic": ProductCategory.COSMETIC,
    "skincare": ProductCategory.COSMETIC,
    "skin care": ProductCategory.COSMETIC,
    "shampoo": ProductCategory.COSMETIC,
    "beauty": ProductCategory.COSMETIC,
    "lotion": ProductCategory.COSMETIC,
    "sunscreen": ProductCategory.COSMETIC,
    "hair": ProductCategory.COSMETIC,
    "toothpaste": ProductCategory.COSMETIC,
    "deodorant": ProductCategory.COSMETIC,
    "drug": ProductCategory.OTC_DRUG,
    "otc": ProductCategory.OTC_DRUG,
    "pain relief": ProductCategory.OTC_DRUG,
    "medicine": ProductCategory.OTC_DRUG,
}

VIOLATION_KEYWORDS = {
    "misleading": ViolationType.DECEPTIVE_ADVERTISING,
    "deceptive": ViolationType.DECEPTIVE_ADVERTISING,
    "false": ViolationType.DECEPTIVE_ADVERTISING,
    "advertising": ViolationType.DECEPTIVE_ADVERTISING,
    "unsubstantiated": ViolationType.UNSUBSTANTIATED_CLAIM,
    "claims": ViolationType.UNSUBSTANTIATED_CLAIM,
    "substantiation": ViolationType.UNSUBSTANTIATED_CLAIM,
    "labeling": ViolationType.LABELING_VIOLATION,
    "label": ViolationType.LABELING_VIOLATION,
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
    for keyword, vtype in VIOLATION_KEYWORDS.items():
        if keyword in lower and vtype not in violations:
            violations.append(vtype)
    return violations or [ViolationType.DECEPTIVE_ADVERTISING]


def _extract_company(title: str) -> str:
    """Extract company name from NAD press release title.

    Common patterns:
      "NAD Recommends Company Discontinue..."
      "NAD Finds Company's Claims..."
      "Company Voluntarily Modifies..."
    """
    # "NAD Recommends X Discontinue/Modify..."
    match = re.search(
        r'NAD\s+(?:Recommends?|Finds?|Refers?|Reviews?)\s+(.+?)\s+(?:Discontinue|Modify|Claims?|Advertising|Should)',
        title, re.IGNORECASE,
    )
    if match:
        return match.group(1).strip("'\"").strip()[:200]

    # "X Voluntarily..." or "X Agrees..."
    match = re.search(
        r'^(.+?)\s+(?:Voluntarily|Agrees?|Will)\s',
        title, re.IGNORECASE,
    )
    if match:
        name = match.group(1).strip()
        if len(name) > 3 and not name.startswith("NAD"):
            return name[:200]

    # "NAD ... of X's ..."
    match = re.search(r"of\s+(.+?)'s\s", title)
    if match:
        return match.group(1).strip()[:200]

    return title.split(",")[0].split(" — ")[0].strip()[:200]


def _parse_nad_html(html: str, date_from: str | None = None) -> list[RegulatoryAction]:
    """Parse NAD press releases from BBB National Programs media center."""
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

    # Look for press release links with titles
    # Pattern: <a href="/media-center/slug">Title</a> near date text
    link_pattern = re.compile(
        r'<a\s+href="(/media-center/[^"]+)"[^>]*>(.*?)</a>',
        re.DOTALL | re.IGNORECASE,
    )

    date_pattern = re.compile(
        r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}'
    )

    entries: list[tuple[int, str, str]] = []
    for m in link_pattern.finditer(html):
        path = m.group(1)
        title = re.sub(r'<[^>]+>', '', m.group(2)).strip()
        if not title or len(title) < 15:
            continue
        # Only keep NAD-related entries
        if "nad" not in title.lower() and "national advertising" not in title.lower():
            continue
        entries.append((m.start(), path, title))

    for pos, path, title in entries:
        # Look for a date near this entry
        nearby = html[max(0, pos - 200):pos + 500]
        date_match = date_pattern.search(nearby)
        date_str = ""
        if date_match:
            raw = date_match.group(0)
            for fmt in ("%B %d, %Y", "%B %d %Y"):
                try:
                    date_str = datetime.strptime(raw.strip(), fmt).strftime("%Y-%m-%d")
                    break
                except ValueError:
                    continue

        if cutoff and date_str:
            try:
                if datetime.strptime(date_str, "%Y-%m-%d") < cutoff:
                    continue
            except ValueError:
                pass

        # Category filtering — skip non-product cases
        categories = _classify_categories(title)
        if not categories:
            continue

        violations = _classify_violations(title)
        company = _extract_company(title)

        slug = path.rstrip("/").split("/")[-1][:60]
        nad_id = f"nad-{slug}"
        if nad_id in seen_ids:
            continue
        seen_ids.add(nad_id)

        action = RegulatoryAction(
            id=nad_id,
            source=SourceType.NAD_DECISION,
            source_id=nad_id,
            title=f"NAD Decision: {title}"[:200],
            description=title,
            company=company,
            product_categories=categories,
            violation_types=violations,
            severity=Severity.WARNING,
            date=date_str or datetime.now().strftime("%Y-%m-%d"),
            url=f"https://bbbprograms.org{path}",
            status="NAD Decision",
        )
        actions.append(action)

    return actions


async def fetch_nad_decisions(
    date_from: str | None = None,
    max_pages: int = 3,
) -> list[RegulatoryAction]:
    """Fetch NAD decisions from BBB National Programs media center.

    Args:
        date_from: ISO date string for incremental sync
        max_pages: Max pages to scrape

    Returns:
        List of RegulatoryAction records (only product-relevant cases)
    """
    all_actions: list[RegulatoryAction] = []
    seen_ids: set[str] = set()

    async with httpx.AsyncClient(
        timeout=30.0,
        follow_redirects=True,
        headers=BROWSER_HEADERS,
    ) as client:
        for page in range(max_pages):
            url = NAD_URL if page == 0 else f"{NAD_URL}?page={page}"
            try:
                resp = await client.get(url)
                if resp.status_code in (403, 404, 429):
                    logger.warning("BBB Programs returned %d on page %d", resp.status_code, page)
                    break
                resp.raise_for_status()
            except httpx.HTTPError as e:
                logger.error("Failed to fetch NAD page %d: %s", page, e)
                break

            actions = _parse_nad_html(resp.text, date_from)
            if not actions:
                break

            for a in actions:
                if a.source_id not in seen_ids:
                    seen_ids.add(a.source_id)
                    all_actions.append(a)

    logger.info("Fetched %d NAD decisions", len(all_actions))
    return all_actions
