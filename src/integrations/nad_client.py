"""NAD (National Advertising Division) decisions client.

Parses the BBB Programs sitemap at https://bbbprograms.org/sitemap.xml
to extract NAD decision URLs and dates. The NAD site is a React SPA with
no public API, but the sitemap contains structured data: URL slugs with
decision titles and lastmod dates.

Decision URLs follow the pattern:
  /Education-and-Resources/newsroom/Descisions/<Slug-With-Title>
"""

import hashlib
import logging
import re
from datetime import datetime, timedelta

import httpx
from defusedxml import ElementTree

from src.models.enums import ProductCategory, Severity, SourceType, ViolationType
from src.models.enforcement import RegulatoryAction

logger = logging.getLogger(__name__)

SITEMAP_URL = "https://bbbprograms.org/sitemap.xml"
DECISION_PATH_PREFIX = "/Education-and-Resources/newsroom/Descisions/"
SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/xml,application/xml",
}

# Keywords in slugs that indicate product categories
CATEGORY_KEYWORDS = {
    "food": ProductCategory.FOOD,
    "supplement": ProductCategory.DIETARY_SUPPLEMENT,
    "vitamin": ProductCategory.DIETARY_SUPPLEMENT,
    "probiotic": ProductCategory.DIETARY_SUPPLEMENT,
    "protein": ProductCategory.DIETARY_SUPPLEMENT,
    "nutrition": ProductCategory.DIETARY_SUPPLEMENT,
    "diet": ProductCategory.DIETARY_SUPPLEMENT,
    "cosmetic": ProductCategory.COSMETIC,
    "skincare": ProductCategory.COSMETIC,
    "beauty": ProductCategory.COSMETIC,
    "shampoo": ProductCategory.COSMETIC,
    "hair": ProductCategory.COSMETIC,
    "sunscreen": ProductCategory.COSMETIC,
    "drug": ProductCategory.OTC_DRUG,
    "medicine": ProductCategory.OTC_DRUG,
    "pet": ProductCategory.FOOD,
    "dog": ProductCategory.FOOD,
    "cat": ProductCategory.FOOD,
    "beverage": ProductCategory.FOOD,
    "snack": ProductCategory.FOOD,
    "cereal": ProductCategory.FOOD,
    "organic": ProductCategory.FOOD,
}

VIOLATION_KEYWORDS = {
    "misleading": ViolationType.DECEPTIVE_ADVERTISING,
    "deceptive": ViolationType.DECEPTIVE_ADVERTISING,
    "false": ViolationType.DECEPTIVE_ADVERTISING,
    "unsubstantiated": ViolationType.UNSUBSTANTIATED_CLAIM,
    "claims": ViolationType.UNSUBSTANTIATED_CLAIM,
    "labeling": ViolationType.LABELING_VIOLATION,
    "label": ViolationType.LABELING_VIOLATION,
    "refers": ViolationType.DECEPTIVE_ADVERTISING,  # "Refers X to FTC" = serious
    "modify": ViolationType.DECEPTIVE_ADVERTISING,
    "discontinue": ViolationType.DECEPTIVE_ADVERTISING,
    "standard of identity": ViolationType.STANDARDS_OF_IDENTITY,
    "imitation": ViolationType.STANDARDS_OF_IDENTITY,
    "not real": ViolationType.STANDARDS_OF_IDENTITY,
}


def _slug_to_title(slug: str) -> str:
    """Convert URL slug to readable title."""
    # Replace hyphens, clean up numbering suffixes like "(2)"
    title = slug.replace("-", " ").strip()
    # Remove trailing parenthesized numbers like " (2)"
    title = re.sub(r"\s*\(\d+\)\s*$", "", title)
    # Title-case but preserve acronyms like NAD, FTC, CARU
    words = title.split()
    result = []
    acronyms = {"nad", "ftc", "caru", "dssrc", "daap", "bbb", "otc", "fda"}
    for w in words:
        if w.lower() in acronyms:
            result.append(w.upper())
        else:
            result.append(w.capitalize())
    return " ".join(result)


def _extract_company(title: str) -> str:
    """Extract company name from NAD decision title.

    NAD sitemap slugs are often truncated (~50 chars), so company names
    may be incomplete. We extract the best guess and mark truncated names.
    """
    # "National Advertising Division Finds [Company] ..."
    # "National Advertising Division Recommends [Company] Modify..."
    # "National Advertising Division Refers [Company] To..."
    # "National Advertising Division Will Refer [Company] To..."
    for pattern in [
        r"National Advertising Division (?:Finds|Recommends|Refers|Determines|Will Refer) (.+?)(?:'s | To | Should | Has | Is | Are | Claims | Modify | Discontinue | Not | Quantif| Pric)",
        r"National Advertising Review Board Recommends (.+?)(?:'s | To | Should | Modify | Discontinue )",
    ]:
        match = re.search(pattern, title)
        if match:
            return match.group(1).strip()[:200]

    # If pattern matched but no stop word (truncated slug), take what's after the verb
    for prefix_verb in ["Finds ", "Recommends ", "Refers ", "Determines ", "Will Refer ", "Review Board Recommends "]:
        full_prefix = "National Advertising Division " + prefix_verb
        if full_prefix in title:
            remainder = title.split(full_prefix, 1)[1].strip()
            # Return the remainder (likely truncated but still useful)
            return remainder[:200] if remainder else title[:200]

    # CARU/DSSRC patterns
    for org in ["CARU ", "DSSRC "]:
        if org in title:
            idx = title.index(org)
            remainder = title[idx + len(org):]
            for verb in ["Recommends ", "Refers ", "Finds "]:
                if verb in remainder:
                    after = remainder.split(verb, 1)[1]
                    return after.split(",")[0].split(" To ")[0].split(" Modify ")[0].strip()[:200]

    return title[:200]


def _classify_categories(text: str) -> list[ProductCategory]:
    categories: list[ProductCategory] = []
    lower = text.lower()
    for keyword, cat in CATEGORY_KEYWORDS.items():
        if keyword in lower and cat not in categories:
            categories.append(cat)
    return categories or [ProductCategory.FOOD]  # Default: NAD covers consumer products


def _classify_violations(text: str) -> list[ViolationType]:
    violations: list[ViolationType] = []
    lower = text.lower()
    for keyword, vtype in VIOLATION_KEYWORDS.items():
        if keyword in lower and vtype not in violations:
            violations.append(vtype)
    return violations or [ViolationType.DECEPTIVE_ADVERTISING]


async def fetch_nad_decisions(
    date_from: str | None = None,
) -> list[RegulatoryAction]:
    """Fetch NAD decisions from BBB Programs sitemap.

    Parses sitemap XML for decision URLs and dates.
    Since the actual pages are React-rendered, we extract
    structured data from URL slugs and lastmod dates.

    Args:
        date_from: ISO date string for incremental sync

    Returns:
        List of RegulatoryAction records
    """
    cutoff = None
    if date_from:
        try:
            cutoff = datetime.strptime(date_from, "%Y-%m-%d")
        except ValueError:
            pass
    else:
        cutoff = datetime.now() - timedelta(days=1825)

    actions: list[RegulatoryAction] = []

    async with httpx.AsyncClient(
        timeout=60.0,
        follow_redirects=True,
        headers=BROWSER_HEADERS,
    ) as client:
        try:
            resp = await client.get(SITEMAP_URL)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.error("Failed to fetch NAD sitemap: %s", e)
            return []

    try:
        root = ElementTree.fromstring(resp.content)
    except Exception as e:
        logger.error("Failed to parse NAD sitemap XML: %s", e)
        return []

    for url_elem in root.findall("sm:url", SITEMAP_NS):
        loc = url_elem.findtext("sm:loc", default="", namespaces=SITEMAP_NS)
        lastmod = url_elem.findtext("sm:lastmod", default="", namespaces=SITEMAP_NS)

        if not loc or DECISION_PATH_PREFIX not in loc:
            continue

        # Extract slug
        slug = loc.split(DECISION_PATH_PREFIX, 1)[1].strip("/")
        if not slug or len(slug) < 5:
            continue

        # Parse date from lastmod
        date_str = ""
        if lastmod:
            try:
                date_str = datetime.fromisoformat(lastmod.replace("Z", "+00:00")).strftime("%Y-%m-%d")
            except ValueError:
                date_str = lastmod[:10]

        # Date filtering
        if cutoff and date_str:
            try:
                if datetime.strptime(date_str, "%Y-%m-%d") < cutoff:
                    continue
            except ValueError:
                pass

        title = _slug_to_title(slug)
        company = _extract_company(title)
        categories = _classify_categories(title)
        violations = _classify_violations(title)

        # Stable ID from slug hash
        slug_hash = hashlib.md5(slug.encode()).hexdigest()[:12]
        decision_id = f"nad-{slug_hash}"

        # Determine severity: referrals to FTC are more serious
        severity = Severity.WARNING if "refer" in title.lower() else Severity.ADVISORY

        action = RegulatoryAction(
            id=decision_id,
            source=SourceType.NAD_DECISION,
            source_id=decision_id,
            title=f"NAD: {title}"[:200],
            description=title,
            company=company,
            product_categories=categories,
            violation_types=violations,
            severity=severity,
            date=date_str or datetime.now().strftime("%Y-%m-%d"),
            url=loc,
            status="Decision",
        )
        actions.append(action)

    logger.info("Fetched %d NAD decisions from sitemap", len(actions))
    return actions
