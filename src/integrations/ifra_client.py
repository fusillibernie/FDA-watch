"""IFRA (International Fragrance Association) amendments client.

Monitors IFRA standards updates relevant to fragrance/cosmetic formulation.
"""

import logging
import re
import uuid
from datetime import datetime, timedelta

import httpx

from src.models.enums import ProductCategory, RegulationStage, SourceType
from src.models.regulation import RegulationChange

logger = logging.getLogger(__name__)

IFRA_URL = "https://ifrafragrance.org/standards-library"

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
}


def _parse_ifra_html(html: str, date_from: str | None = None) -> list[RegulationChange]:
    """Parse IFRA standards/amendment entries from their library page."""
    results: list[RegulationChange] = []

    cutoff = None
    if date_from:
        try:
            cutoff = datetime.strptime(date_from, "%Y-%m-%d")
        except ValueError:
            pass
    else:
        cutoff = datetime.now() - timedelta(days=365)

    # Look for amendment/standard entries
    entry_pattern = re.compile(
        r'<a[^>]+href="([^"]*)"[^>]*>\s*((?:IFRA\s+)?(?:\d+\w*\s+)?(?:Amendment|Standard)[^<]*)</a>',
        re.IGNORECASE,
    )

    date_pattern = re.compile(
        r'(\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})',
        re.IGNORECASE,
    )

    date_positions: list[tuple[int, str]] = []
    for dm in date_pattern.finditer(html):
        try:
            date_str = datetime.strptime(dm.group(1), "%d %B %Y").strftime("%Y-%m-%d")
            date_positions.append((dm.start(), date_str))
        except ValueError:
            continue

    for match in entry_pattern.finditer(html):
        url_path = match.group(1)
        title = match.group(2).strip()
        title = re.sub(r'<[^>]+>', '', title).strip()
        if not title:
            continue

        # Find nearest date
        date_str = ""
        for pos, ds in reversed(date_positions):
            if pos <= match.start():
                date_str = ds
                break

        if cutoff and date_str:
            try:
                if datetime.strptime(date_str, "%Y-%m-%d") < cutoff:
                    continue
            except ValueError:
                pass

        # Extract amendment number if present
        amend_match = re.search(r'(\d+)\w*\s*Amendment', title, re.IGNORECASE)
        amend_num = amend_match.group(1) if amend_match else uuid.uuid4().hex[:8]
        source_id = f"ifra-amendment-{amend_num}"

        full_url = url_path if url_path.startswith("http") else f"https://ifrafragrance.org{url_path}"

        change = RegulationChange(
            id=source_id,
            source=SourceType.IFRA_AMENDMENT,
            source_id=source_id,
            title=title[:300],
            summary=f"IFRA standards update: {title}",
            agency="IFRA",
            stage=RegulationStage.AMENDMENT,
            product_categories=[ProductCategory.COSMETIC],
            date_published=date_str,
            jurisdiction="EU",
            url=full_url,
        )
        results.append(change)

    return results


async def fetch_ifra_amendments(
    date_from: str | None = None,
) -> list[RegulationChange]:
    """Fetch IFRA standards amendments.

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
            resp = await client.get(IFRA_URL)
            if resp.status_code in (403, 404, 429):
                logger.warning("IFRA page returned %d", resp.status_code)
                return []
            resp.raise_for_status()
    except httpx.HTTPError as e:
        logger.error("Failed to fetch IFRA amendments: %s", e)
        return []

    changes = _parse_ifra_html(resp.text, date_from)
    logger.info("Fetched %d IFRA amendments", len(changes))
    return changes
