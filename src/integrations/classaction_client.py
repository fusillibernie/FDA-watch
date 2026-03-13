"""Class action lawsuit tracker.

Fetches class action lawsuit filings related to food, supplements,
cosmetics, and OTC drugs from public court records and legal databases.

Primary approach: scrape legal news aggregators for consumer-product lawsuits.
"""

import logging
import re
import uuid
from datetime import datetime, timedelta

import httpx

from src.models.enums import ProductCategory, Severity, SourceType, ViolationType
from src.models.enforcement import RegulatoryAction

logger = logging.getLogger(__name__)

# Legal news sources for class action tracking
CLASSACTION_URL = "https://www.classaction.org/news"
TOPCLASSACTIONS_URL = "https://topclassactions.com/category/lawsuit-settlements/open-lawsuit-settlements/"

# Product category keywords
PRODUCT_KEYWORDS = {
    "food": ProductCategory.FOOD,
    "grocery": ProductCategory.FOOD,
    "cereal": ProductCategory.FOOD,
    "snack": ProductCategory.FOOD,
    "beverage": ProductCategory.FOOD,
    "juice": ProductCategory.FOOD,
    "organic": ProductCategory.FOOD,
    "natural": ProductCategory.FOOD,
    "supplement": ProductCategory.DIETARY_SUPPLEMENT,
    "vitamin": ProductCategory.DIETARY_SUPPLEMENT,
    "herbal": ProductCategory.DIETARY_SUPPLEMENT,
    "probiotic": ProductCategory.DIETARY_SUPPLEMENT,
    "protein powder": ProductCategory.DIETARY_SUPPLEMENT,
    "cosmetic": ProductCategory.COSMETIC,
    "skincare": ProductCategory.COSMETIC,
    "shampoo": ProductCategory.COSMETIC,
    "lotion": ProductCategory.COSMETIC,
    "sunscreen": ProductCategory.COSMETIC,
    "makeup": ProductCategory.COSMETIC,
    "beauty": ProductCategory.COSMETIC,
    "drug": ProductCategory.OTC_DRUG,
    "medicine": ProductCategory.OTC_DRUG,
    "pain reliever": ProductCategory.OTC_DRUG,
    "antacid": ProductCategory.OTC_DRUG,
}

# Violation type keywords
VIOLATION_KEYWORDS = {
    "mislabel": ViolationType.LABELING_VIOLATION,
    "labeling": ViolationType.LABELING_VIOLATION,
    "label": ViolationType.LABELING_VIOLATION,
    "false advertising": ViolationType.DECEPTIVE_ADVERTISING,
    "misleading": ViolationType.DECEPTIVE_ADVERTISING,
    "deceptive": ViolationType.DECEPTIVE_ADVERTISING,
    "fraud": ViolationType.DECEPTIVE_ADVERTISING,
    "misrepresent": ViolationType.DECEPTIVE_ADVERTISING,
    "contaminat": ViolationType.CONTAMINATION,
    "heavy metal": ViolationType.CONTAMINATION,
    "lead": ViolationType.CONTAMINATION,
    "pfas": ViolationType.CONTAMINATION,
    "pesticide": ViolationType.CONTAMINATION,
    "undeclared": ViolationType.UNDECLARED_INGREDIENT,
    "allergen": ViolationType.UNDECLARED_ALLERGEN,
    "health claim": ViolationType.UNSUBSTANTIATED_CLAIM,
    "unproven": ViolationType.UNSUBSTANTIATED_CLAIM,
}


def _classify_categories(text: str) -> list[ProductCategory]:
    """Extract product categories from lawsuit text."""
    categories: list[ProductCategory] = []
    lower = text.lower()
    for keyword, cat in PRODUCT_KEYWORDS.items():
        if keyword in lower and cat not in categories:
            categories.append(cat)
    return categories or [ProductCategory.FOOD]


def _classify_violations(text: str) -> list[ViolationType]:
    """Extract violation types from lawsuit text."""
    violations: list[ViolationType] = []
    lower = text.lower()
    for keyword, vtype in VIOLATION_KEYWORDS.items():
        if keyword in lower and vtype not in violations:
            violations.append(vtype)
    return violations or [ViolationType.DECEPTIVE_ADVERTISING]


def _extract_company(title: str) -> str:
    """Try to extract defendant/company from lawsuit title."""
    # Common patterns: "X Sued Over Y", "X Class Action", "X Lawsuit"
    # Often the company is the first entity mentioned
    # Remove common prefixes
    clean = re.sub(r'^(class action|lawsuit|suit):?\s*', '', title, flags=re.IGNORECASE)
    # Take first segment before common verbs
    for split_word in [" sued ", " faces ", " hit with ", " class action", " lawsuit"]:
        if split_word in clean.lower():
            return clean[:clean.lower().index(split_word)].strip()[:200]
    return clean.split(",")[0].strip()[:200]


def _parse_classaction_page(
    html: str, date_from: str | None = None
) -> list[RegulatoryAction]:
    """Parse class action listings from HTML."""
    actions: list[RegulatoryAction] = []

    cutoff = None
    if date_from:
        try:
            cutoff = datetime.strptime(date_from, "%Y-%m-%d")
        except ValueError:
            pass
    else:
        cutoff = datetime.now() - timedelta(days=730)

    # Find article/entry blocks
    # Common patterns in legal news sites
    article_pattern = re.compile(
        r'<article[^>]*>(.*?)</article>',
        re.DOTALL | re.IGNORECASE,
    )
    # Fallback: heading + paragraph blocks
    heading_pattern = re.compile(
        r'<h[23][^>]*>\s*<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>\s*</h[23]>',
        re.DOTALL | re.IGNORECASE,
    )

    articles = article_pattern.findall(html)
    if not articles:
        # Fallback to heading pattern
        headings = heading_pattern.findall(html)
        for url, title in headings:
            title_clean = re.sub(r'<[^>]+>', '', title).strip()
            if len(title_clean) < 10:
                continue

            # Only keep relevant product lawsuits
            combined = title_clean.lower()
            is_relevant = any(kw in combined for kw in PRODUCT_KEYWORDS)
            if not is_relevant:
                continue

            case_id = f"ca-{uuid.uuid4().hex[:12]}"
            full_url = url if url.startswith("http") else f"https://www.classaction.org{url}"

            action = RegulatoryAction(
                id=case_id,
                source=SourceType.CLASS_ACTION,
                source_id=case_id,
                title=f"Class Action: {title_clean}"[:200],
                description=title_clean,
                company=_extract_company(title_clean),
                product_categories=_classify_categories(title_clean),
                violation_types=_classify_violations(title_clean),
                severity=Severity.ADVISORY,
                date=datetime.now().strftime("%Y-%m-%d"),
                url=full_url,
                status="Filed",
            )
            actions.append(action)
        return actions

    for article_html in articles:
        # Extract title
        title_match = re.search(
            r'<(?:h[2-4]|a)[^>]*>(.*?)</(?:h[2-4]|a)>', article_html, re.DOTALL
        )
        if not title_match:
            continue
        title = re.sub(r'<[^>]+>', '', title_match.group(1)).strip()
        if len(title) < 10:
            continue

        # Only keep product-related lawsuits
        desc_text = re.sub(r'<[^>]+>', ' ', article_html)
        desc_text = re.sub(r'\s+', ' ', desc_text).strip()
        combined = f"{title} {desc_text}".lower()

        is_relevant = any(kw in combined for kw in PRODUCT_KEYWORDS)
        if not is_relevant:
            continue

        # Extract date
        date_match = re.search(
            r'(\d{1,2}/\d{1,2}/\d{4}|\w+ \d{1,2},? \d{4}|\d{4}-\d{2}-\d{2})',
            article_html,
        )
        date_str = datetime.now().strftime("%Y-%m-%d")
        if date_match:
            raw = date_match.group(1)
            for fmt in ("%m/%d/%Y", "%B %d, %Y", "%B %d %Y", "%Y-%m-%d"):
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

        # Extract URL
        url_match = re.search(r'href="([^"]*)"', article_html)
        url = ""
        if url_match:
            url = url_match.group(1)
            if not url.startswith("http"):
                url = f"https://www.classaction.org{url}"

        case_id = f"ca-{uuid.uuid4().hex[:12]}"

        action = RegulatoryAction(
            id=case_id,
            source=SourceType.CLASS_ACTION,
            source_id=case_id,
            title=f"Class Action: {title}"[:200],
            description=desc_text[:2000],
            company=_extract_company(title),
            product_categories=_classify_categories(combined),
            violation_types=_classify_violations(combined),
            severity=Severity.ADVISORY,
            date=date_str,
            url=url,
            status="Filed",
        )
        actions.append(action)

    return actions


async def fetch_classaction_lawsuits(
    date_from: str | None = None,
) -> list[RegulatoryAction]:
    """Fetch class action lawsuits related to consumer products.

    Args:
        date_from: ISO date string for incremental sync

    Returns:
        List of RegulatoryAction records
    """
    all_actions: list[RegulatoryAction] = []

    urls = [CLASSACTION_URL, TOPCLASSACTIONS_URL]

    async with httpx.AsyncClient(
        timeout=30.0,
        follow_redirects=True,
        headers={
            "User-Agent": "Mozilla/5.0 (FDA-watch compliance monitor)",
        },
    ) as client:
        for url in urls:
            try:
                resp = await client.get(url)
                if resp.status_code in (403, 404, 429):
                    logger.warning("Class action source %s returned %d", url, resp.status_code)
                    continue
                resp.raise_for_status()
            except httpx.HTTPError as e:
                logger.error("Failed to fetch class actions from %s: %s", url, e)
                continue

            actions = _parse_classaction_page(resp.text, date_from)
            all_actions.extend(actions)

    logger.info("Fetched %d class action lawsuits", len(all_actions))
    return all_actions
