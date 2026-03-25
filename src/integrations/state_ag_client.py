"""State Attorney General enforcement action client.

Scrapes press releases from CA and NY AG offices for consumer product
enforcement actions. Heavy category filtering to food, supplements,
cosmetics, and OTC drugs only.
"""

import logging
import re
import uuid
from datetime import datetime, timedelta

import httpx

from src.models.enums import ProductCategory, Severity, SourceType, ViolationType
from src.models.enforcement import RegulatoryAction

logger = logging.getLogger(__name__)

CA_AG_URL = "https://oag.ca.gov/news"
NY_AG_URL = "https://ag.ny.gov/press-releases"

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
}

PRODUCT_KEYWORDS = {
    "food": ProductCategory.FOOD,
    "grocery": ProductCategory.FOOD,
    "beverage": ProductCategory.FOOD,
    "restaurant": ProductCategory.FOOD,
    "organic": ProductCategory.FOOD,
    "supplement": ProductCategory.DIETARY_SUPPLEMENT,
    "vitamin": ProductCategory.DIETARY_SUPPLEMENT,
    "herbal": ProductCategory.DIETARY_SUPPLEMENT,
    "diet pill": ProductCategory.DIETARY_SUPPLEMENT,
    "weight loss": ProductCategory.DIETARY_SUPPLEMENT,
    "cosmetic": ProductCategory.COSMETIC,
    "skincare": ProductCategory.COSMETIC,
    "beauty": ProductCategory.COSMETIC,
    "shampoo": ProductCategory.COSMETIC,
    "lotion": ProductCategory.COSMETIC,
    "sunscreen": ProductCategory.COSMETIC,
    "drug": ProductCategory.OTC_DRUG,
    "pharmaceutical": ProductCategory.OTC_DRUG,
    "medicine": ProductCategory.OTC_DRUG,
    "health": ProductCategory.DIETARY_SUPPLEMENT,
}

VIOLATION_KEYWORDS = {
    "deceptive": ViolationType.DECEPTIVE_ADVERTISING,
    "misleading": ViolationType.DECEPTIVE_ADVERTISING,
    "false advertising": ViolationType.DECEPTIVE_ADVERTISING,
    "fraud": ViolationType.DECEPTIVE_ADVERTISING,
    "consumer protection": ViolationType.DECEPTIVE_ADVERTISING,
    "unsubstantiated": ViolationType.UNSUBSTANTIATED_CLAIM,
    "mislabel": ViolationType.LABELING_VIOLATION,
    "labeling": ViolationType.LABELING_VIOLATION,
    "contaminat": ViolationType.CONTAMINATION,
    "unsafe": ViolationType.CONTAMINATION,
    "adulterat": ViolationType.ADULTERATION,
    "standard of identity": ViolationType.STANDARDS_OF_IDENTITY,
    "imitation": ViolationType.STANDARDS_OF_IDENTITY,
    "not real": ViolationType.STANDARDS_OF_IDENTITY,
    "not genuine": ViolationType.STANDARDS_OF_IDENTITY,
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
    """Extract company/entity from AG press release title."""
    # "AG Sues X for..."
    match = re.search(
        r'(?:Attorney General|AG)\s+(?:Sues?|Files?|Charges?|Announces?|Settles?|Orders?)\s+(.+?)(?:\s+for\s|\s+over\s|\s+to\s|\s+in\s|\s*$)',
        title, re.IGNORECASE,
    )
    if match:
        return match.group(1).strip()[:200]

    # "X Settles..." or "X Agrees..."
    match = re.search(
        r'^(.+?)\s+(?:Settles?|Agrees?|Ordered|Fined|Pays?)\s',
        title, re.IGNORECASE,
    )
    if match:
        name = match.group(1).strip()
        if len(name) > 3:
            return name[:200]

    return title.split(",")[0].split(" — ")[0].strip()[:200]


def _parse_ca_ag_html(html: str, date_from: str | None = None) -> list[RegulatoryAction]:
    """Parse CA AG press release listings."""
    actions: list[RegulatoryAction] = []
    seen_ids: set[str] = set()

    cutoff = None
    if date_from:
        try:
            cutoff = datetime.strptime(date_from, "%Y-%m-%d")
        except ValueError:
            pass
    else:
        cutoff = datetime.now() - timedelta(days=1825)

    # Look for press release links
    link_pattern = re.compile(
        r'<a\s+href="(/news/[^"]+)"[^>]*>(.*?)</a>',
        re.DOTALL | re.IGNORECASE,
    )

    date_pattern = re.compile(
        r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}'
    )

    for m in link_pattern.finditer(html):
        path = m.group(1)
        title = re.sub(r'<[^>]+>', '', m.group(2)).strip()
        if not title or len(title) < 15:
            continue

        # Find nearby date
        nearby = html[max(0, m.start() - 200):m.start() + 500]
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

        categories = _classify_categories(title)
        if not categories:
            continue

        violations = _classify_violations(title)
        company = _extract_company(title)

        slug = path.rstrip("/").split("/")[-1][:50]
        ag_id = f"ca-ag-{slug}"
        if ag_id in seen_ids:
            continue
        seen_ids.add(ag_id)

        action = RegulatoryAction(
            id=ag_id,
            source=SourceType.STATE_AG,
            source_id=ag_id,
            title=f"CA AG: {title}"[:200],
            description=title,
            company=company,
            product_categories=categories,
            violation_types=violations,
            severity=Severity.WARNING,
            date=date_str or datetime.now().strftime("%Y-%m-%d"),
            url=f"https://oag.ca.gov{path}",
            status="CA Attorney General",
        )
        actions.append(action)

    return actions


def _parse_ny_ag_html(html: str, date_from: str | None = None) -> list[RegulatoryAction]:
    """Parse NY AG press release listings."""
    actions: list[RegulatoryAction] = []
    seen_ids: set[str] = set()

    cutoff = None
    if date_from:
        try:
            cutoff = datetime.strptime(date_from, "%Y-%m-%d")
        except ValueError:
            pass
    else:
        cutoff = datetime.now() - timedelta(days=1825)

    link_pattern = re.compile(
        r'<a\s+href="(/press-release/[^"]+)"[^>]*>(.*?)</a>',
        re.DOTALL | re.IGNORECASE,
    )

    date_pattern = re.compile(
        r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}'
    )

    for m in link_pattern.finditer(html):
        path = m.group(1)
        title = re.sub(r'<[^>]+>', '', m.group(2)).strip()
        if not title or len(title) < 15:
            continue

        nearby = html[max(0, m.start() - 200):m.start() + 500]
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

        categories = _classify_categories(title)
        if not categories:
            continue

        violations = _classify_violations(title)
        company = _extract_company(title)

        slug = path.rstrip("/").split("/")[-1][:50]
        ag_id = f"ny-ag-{slug}"
        if ag_id in seen_ids:
            continue
        seen_ids.add(ag_id)

        action = RegulatoryAction(
            id=ag_id,
            source=SourceType.STATE_AG,
            source_id=ag_id,
            title=f"NY AG: {title}"[:200],
            description=title,
            company=company,
            product_categories=categories,
            violation_types=violations,
            severity=Severity.WARNING,
            date=date_str or datetime.now().strftime("%Y-%m-%d"),
            url=f"https://ag.ny.gov{path}",
            status="NY Attorney General",
        )
        actions.append(action)

    return actions


async def fetch_ca_ag_actions(date_from: str | None = None) -> list[RegulatoryAction]:
    """Fetch CA AG press releases."""
    async with httpx.AsyncClient(
        timeout=30.0, follow_redirects=True, headers=BROWSER_HEADERS
    ) as client:
        try:
            resp = await client.get(CA_AG_URL)
            if resp.status_code in (403, 404, 429):
                logger.warning("CA AG returned %d", resp.status_code)
                return []
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.error("Failed to fetch CA AG: %s", e)
            return []
    return _parse_ca_ag_html(resp.text, date_from)


async def fetch_ny_ag_actions(date_from: str | None = None) -> list[RegulatoryAction]:
    """Fetch NY AG press releases."""
    async with httpx.AsyncClient(
        timeout=30.0, follow_redirects=True, headers=BROWSER_HEADERS
    ) as client:
        try:
            resp = await client.get(NY_AG_URL)
            if resp.status_code in (403, 404, 429):
                logger.warning("NY AG returned %d", resp.status_code)
                return []
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.error("Failed to fetch NY AG: %s", e)
            return []
    return _parse_ny_ag_html(resp.text, date_from)


async def fetch_state_ag_actions(
    date_from: str | None = None,
) -> list[RegulatoryAction]:
    """Fetch and combine state AG actions from CA and NY.

    Deduplicates by source_id.
    """
    ca = await fetch_ca_ag_actions(date_from)
    ny = await fetch_ny_ag_actions(date_from)

    all_actions = ca + ny
    seen: set[str] = set()
    deduped: list[RegulatoryAction] = []
    for a in all_actions:
        if a.source_id not in seen:
            seen.add(a.source_id)
            deduped.append(a)

    logger.info("Fetched %d state AG actions (CA: %d, NY: %d)", len(deduped), len(ca), len(ny))
    return deduped
