"""Class action lawsuit tracker.

Scrapes class action lawsuit filings from classaction.org/news.

HTML structure on classaction.org:
  <article>
    <header><h3><a href="/news/slug">Title</a></h3></header>
    <p><span>March 12, 2026</span> Summary text...</p>
  </article>

Filters to lawsuits relevant to food, supplements, cosmetics, and OTC drugs.
"""

import logging
import re
import uuid
from datetime import datetime, timedelta

import httpx

from src.models.enums import ProductCategory, Severity, SourceType, ViolationType
from src.models.enforcement import RegulatoryAction

logger = logging.getLogger(__name__)

CLASSACTION_URL = "https://www.classaction.org/news"

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
}

# Product category keywords — only cases matching these are kept
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
    "supplement": ProductCategory.DIETARY_SUPPLEMENT,
    "vitamin": ProductCategory.DIETARY_SUPPLEMENT,
    "herbal": ProductCategory.DIETARY_SUPPLEMENT,
    "probiotic": ProductCategory.DIETARY_SUPPLEMENT,
    "protein powder": ProductCategory.DIETARY_SUPPLEMENT,
    "cosmetic": ProductCategory.COSMETIC,
    "skincare": ProductCategory.COSMETIC,
    "shampoo": ProductCategory.COSMETIC,
    "conditioner": ProductCategory.COSMETIC,
    "lotion": ProductCategory.COSMETIC,
    "sunscreen": ProductCategory.COSMETIC,
    "makeup": ProductCategory.COSMETIC,
    "beauty": ProductCategory.COSMETIC,
    "hair": ProductCategory.COSMETIC,
    "drug": ProductCategory.OTC_DRUG,
    "medicine": ProductCategory.OTC_DRUG,
    "pain reliever": ProductCategory.OTC_DRUG,
    "antacid": ProductCategory.OTC_DRUG,
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
    "lead": ViolationType.CONTAMINATION,
    "pfas": ViolationType.CONTAMINATION,
    "benzene": ViolationType.CONTAMINATION,
    "pesticide": ViolationType.CONTAMINATION,
    "undeclared": ViolationType.UNDECLARED_INGREDIENT,
    "allergen": ViolationType.UNDECLARED_ALLERGEN,
    "health claim": ViolationType.UNSUBSTANTIATED_CLAIM,
    "unproven": ViolationType.UNSUBSTANTIATED_CLAIM,
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
    """Extract defendant company from lawsuit title."""
    # "X Sued Over Y", "X Faces Class Action", "X Hit with Class Action"
    for split_word in [" sued ", " faces ", " hit with ", " facing "]:
        if split_word in title.lower():
            idx = title.lower().index(split_word)
            return title[:idx].strip()[:200]

    # "Class Action Claims X's ...", "Lawsuit Claims X ..."
    match = re.search(
        r'(?:Class Action|Lawsuit)\s+(?:Claims?|Alleges?|Says?)\s+(.+?)(?:\'s?\s|\s+(?:Is|Are|Has|Have|Fail|Operat|Disclos|Contain))',
        title, re.IGNORECASE,
    )
    if match:
        return match.group(1).strip()[:200]

    # "$XM Company Settlement..."
    match = re.search(r'\$[\d.]+[MBK]?\s+(.+?)\s+Settlement', title, re.IGNORECASE)
    if match:
        return match.group(1).strip()[:200]

    # Fallback: first segment
    return title.split(",")[0].split(" — ")[0].strip()[:200]


def _parse_classaction_page(
    html: str, date_from: str | None = None
) -> list[RegulatoryAction]:
    """Parse class action listings from classaction.org HTML.

    Matches the actual site structure:
      <h3 ...><a href="/news/slug" ...>Title</a></h3>
    with dates in <span> elements like "March 12, 2026".
    """
    actions: list[RegulatoryAction] = []
    seen_slugs: set[str] = set()

    cutoff = None
    if date_from:
        try:
            cutoff = datetime.strptime(date_from, "%Y-%m-%d")
        except ValueError:
            pass
    else:
        cutoff = datetime.now() - timedelta(days=730)

    # Match: <h3 ...><a href="/news/slug" ...>Title</a></h3>
    # Also match: <a href="/news/slug" ...><h3>Title</h3></a>
    patterns = [
        re.compile(
            r'<h3[^>]*>\s*<a\s+href="(/news/[^"]+)"[^>]*>(.*?)</a>\s*</h3>',
            re.DOTALL | re.IGNORECASE,
        ),
        re.compile(
            r'<a\s+href="(/news/[^"]+)"[^>]*>\s*<h3[^>]*>(.*?)</h3>\s*</a>',
            re.DOTALL | re.IGNORECASE,
        ),
    ]

    # Also grab dates: <span ...>Month DD, YYYY</span>
    date_pattern = re.compile(
        r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}'
    )

    # Collect all headline matches with their positions
    entries: list[tuple[int, str, str]] = []  # (position, slug, title)
    for pattern in patterns:
        for m in pattern.finditer(html):
            slug = m.group(1)
            title = re.sub(r'<[^>]+>', '', m.group(2)).strip()
            if slug not in seen_slugs and len(title) > 10:
                seen_slugs.add(slug)
                entries.append((m.start(), slug, title))

    # Sort by position in document
    entries.sort(key=lambda x: x[0])

    for pos, slug, title in entries:
        # Look for a date near this entry (within next 500 chars)
        nearby = html[pos:pos + 500]
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

        # Only keep product-relevant lawsuits
        categories = _classify_categories(title)
        if not categories:
            continue

        violations = _classify_violations(title)
        company = _extract_company(title)
        url = f"https://www.classaction.org{slug}"

        # Use slug for stable ID
        slug_id = slug.replace("/news/", "").replace("/", "-")[:60]
        case_id = f"ca-{slug_id}"

        action = RegulatoryAction(
            id=case_id,
            source=SourceType.CLASS_ACTION,
            source_id=case_id,
            title=f"Class Action: {title}"[:200],
            description=title,
            company=company,
            product_categories=categories,
            violation_types=violations,
            severity=Severity.ADVISORY,
            date=date_str or datetime.now().strftime("%Y-%m-%d"),
            url=url,
            status="Filed",
        )
        actions.append(action)

    return actions


async def fetch_classaction_lawsuits(
    date_from: str | None = None,
    max_pages: int = 3,
) -> list[RegulatoryAction]:
    """Fetch class action lawsuits related to consumer products.

    Scrapes classaction.org/news with pagination.

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
        for page in range(1, max_pages + 1):
            url = CLASSACTION_URL if page == 1 else f"{CLASSACTION_URL}?page={page}"
            try:
                resp = await client.get(url)
                if resp.status_code in (403, 404, 429):
                    logger.warning("classaction.org returned %d on page %d", resp.status_code, page)
                    break
                resp.raise_for_status()
            except httpx.HTTPError as e:
                logger.error("Failed to fetch class actions page %d: %s", page, e)
                break

            actions = _parse_classaction_page(resp.text, date_from)
            if not actions:
                break

            for a in actions:
                if a.source_id not in seen_ids:
                    seen_ids.add(a.source_id)
                    all_actions.append(a)

    logger.info("Fetched %d class action lawsuits", len(all_actions))
    return all_actions
