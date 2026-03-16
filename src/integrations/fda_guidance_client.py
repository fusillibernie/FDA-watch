"""FDA Guidance Documents client.

Fetches draft and final guidance documents from FDA's guidance search page.
"""

import logging
import re
import uuid
from datetime import datetime, timedelta

import httpx

from src.models.enums import ProductCategory, RegulationStage, SourceType
from src.models.regulation import RegulationChange

logger = logging.getLogger(__name__)

FDA_GUIDANCE_URL = "https://www.fda.gov/regulatory-information/search-fda-guidance-documents"

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
}

CATEGORY_KEYWORDS = {
    "food": ProductCategory.FOOD,
    "dietary supplement": ProductCategory.DIETARY_SUPPLEMENT,
    "CGMP": ProductCategory.DIETARY_SUPPLEMENT,
    "nutrition": ProductCategory.FOOD,
    "allergen": ProductCategory.FOOD,
    "cosmetic": ProductCategory.COSMETIC,
    "color additive": ProductCategory.COSMETIC,
    "sunscreen": ProductCategory.COSMETIC,
    "over-the-counter": ProductCategory.OTC_DRUG,
    "OTC": ProductCategory.OTC_DRUG,
    "drug": ProductCategory.OTC_DRUG,
    "device": ProductCategory.DEVICE,
    "medical device": ProductCategory.DEVICE,
    "diagnostic": ProductCategory.DEVICE,
}


def _classify_categories(text: str) -> list[ProductCategory]:
    categories: list[ProductCategory] = []
    lower = text.lower()
    for keyword, cat in CATEGORY_KEYWORDS.items():
        if keyword.lower() in lower and cat not in categories:
            categories.append(cat)
    return categories or [ProductCategory.FOOD]


def _classify_stage(text: str) -> RegulationStage:
    lower = text.lower()
    if "draft" in lower:
        return RegulationStage.GUIDANCE_DRAFT
    if "final" in lower or "revised" in lower:
        return RegulationStage.GUIDANCE_FINAL
    return RegulationStage.GUIDANCE_FINAL


def _parse_fda_guidance_html(html: str, date_from: str | None = None) -> list[RegulationChange]:
    """Parse FDA guidance document listings from HTML."""
    results: list[RegulationChange] = []

    cutoff = None
    if date_from:
        try:
            cutoff = datetime.strptime(date_from, "%Y-%m-%d")
        except ValueError:
            pass
    else:
        cutoff = datetime.now() - timedelta(days=365)

    # Pattern for guidance entries
    # FDA pages have links with guidance titles and nearby dates
    entry_pattern = re.compile(
        r'<a[^>]+href="(/regulatory-information/[^"]*guidance[^"]*)"[^>]*>\s*([^<]+)</a>',
        re.IGNORECASE,
    )

    date_pattern = re.compile(
        r'(\d{1,2}/\d{1,2}/\d{4})',
    )

    # Find dates with positions
    date_positions: list[tuple[int, str]] = []
    for dm in date_pattern.finditer(html):
        try:
            date_str = datetime.strptime(dm.group(1), "%m/%d/%Y").strftime("%Y-%m-%d")
            date_positions.append((dm.start(), date_str))
        except ValueError:
            continue

    for match in entry_pattern.finditer(html):
        url_path = match.group(1)
        title = match.group(2).strip()
        if not title or len(title) < 10:
            continue

        # Find nearest date before this entry
        date_str = ""
        match_pos = match.start()
        for pos, ds in reversed(date_positions):
            if pos <= match_pos:
                date_str = ds
                break

        if cutoff and date_str:
            try:
                if datetime.strptime(date_str, "%Y-%m-%d") < cutoff:
                    continue
            except ValueError:
                pass

        stage = _classify_stage(title)
        categories = _classify_categories(title)

        slug = url_path.rstrip("/").split("/")[-1] if url_path else uuid.uuid4().hex[:12]
        source_id = f"fda-guidance-{slug}"

        change = RegulationChange(
            id=source_id,
            source=SourceType.FDA_GUIDANCE,
            source_id=source_id,
            title=title[:300],
            summary=title,
            agency="FDA",
            stage=stage,
            product_categories=categories,
            date_published=date_str,
            jurisdiction="US",
            url=f"https://www.fda.gov{url_path}" if not url_path.startswith("http") else url_path,
        )
        results.append(change)

    return results


async def fetch_fda_guidance(
    date_from: str | None = None,
) -> list[RegulationChange]:
    """Fetch FDA guidance documents.

    Args:
        date_from: ISO date string for incremental sync

    Returns:
        List of RegulationChange records
    """
    try:
        async with httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers=BROWSER_HEADERS,
        ) as client:
            resp = await client.get(FDA_GUIDANCE_URL)
            if resp.status_code in (403, 404, 429):
                logger.warning("FDA guidance page returned %d", resp.status_code)
                return []
            resp.raise_for_status()
    except httpx.HTTPError as e:
        logger.error("Failed to fetch FDA guidance: %s", e)
        return []

    changes = _parse_fda_guidance_html(resp.text, date_from)
    logger.info("Fetched %d FDA guidance documents", len(changes))
    return changes
