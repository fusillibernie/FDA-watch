"""EU SCCS (Scientific Committee on Consumer Safety) client.

Scrapes SCCS opinions on cosmetic ingredients from the EC website.
"""

import logging
import re
import uuid
from datetime import datetime, timedelta

import httpx

from src.models.enums import ProductCategory, Severity, SourceType, ViolationType
from src.models.enforcement import RegulatoryAction

logger = logging.getLogger(__name__)

SCCS_URL = "https://health.ec.europa.eu/scientific-committees/scientific-committee-consumer-safety-sccs/sccs-opinions_en"

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
}

SAFETY_KEYWORDS = {
    "not safe": ViolationType.COSMETIC_SAFETY_CONCERN,
    "unsafe": ViolationType.COSMETIC_SAFETY_CONCERN,
    "concern": ViolationType.COSMETIC_SAFETY_CONCERN,
    "endocrine": ViolationType.COSMETIC_SAFETY_CONCERN,
    "sensitisation": ViolationType.COSMETIC_SAFETY_CONCERN,
    "sensitization": ViolationType.COSMETIC_SAFETY_CONCERN,
    "carcinogen": ViolationType.COSMETIC_SAFETY_CONCERN,
    "mutagenic": ViolationType.COSMETIC_SAFETY_CONCERN,
    "genotoxic": ViolationType.COSMETIC_SAFETY_CONCERN,
    "restricted": ViolationType.RESTRICTED_SUBSTANCE,
    "banned": ViolationType.RESTRICTED_SUBSTANCE,
    "prohibition": ViolationType.RESTRICTED_SUBSTANCE,
}


def _classify_violations(text: str) -> list[ViolationType]:
    violations: list[ViolationType] = []
    lower = text.lower()
    for keyword, vtype in SAFETY_KEYWORDS.items():
        if keyword in lower and vtype not in violations:
            violations.append(vtype)
    return violations or [ViolationType.COSMETIC_SAFETY_CONCERN]


def _parse_sccs_opinions(html: str, date_from: str | None = None) -> list[RegulatoryAction]:
    """Parse SCCS opinion entries from the HTML page."""
    results: list[RegulatoryAction] = []

    cutoff = None
    if date_from:
        try:
            cutoff = datetime.strptime(date_from, "%Y-%m-%d")
        except ValueError:
            pass
    else:
        cutoff = datetime.now() - timedelta(days=365)

    # Look for opinion entries — typical pattern:
    # <a href="/...">SCCS/1234/56 - Opinion on SubstanceName</a>
    # with nearby date information
    opinion_pattern = re.compile(
        r'<a[^>]+href="([^"]*)"[^>]*>\s*(SCCS/\d+/\d+[^<]*)</a>',
        re.IGNORECASE,
    )

    # Also find dates near opinions
    date_pattern = re.compile(r'(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})', re.IGNORECASE)

    # Build a list of all dates found in the page with their positions
    date_positions: list[tuple[int, str]] = []
    for dm in date_pattern.finditer(html):
        try:
            date_str = datetime.strptime(dm.group(0), "%d %B %Y").strftime("%Y-%m-%d")
            date_positions.append((dm.start(), date_str))
        except ValueError:
            continue

    for match in opinion_pattern.finditer(html):
        url_path = match.group(1)
        title = match.group(2).strip()
        title = re.sub(r'<[^>]+>', '', title).strip()
        if not title:
            continue

        # Extract opinion number
        opinion_match = re.search(r'SCCS/(\d+/\d+)', title)
        opinion_number = opinion_match.group(1).replace("/", "-") if opinion_match else uuid.uuid4().hex[:12]

        # Find nearest date that precedes this match position
        date_str = ""
        match_pos = match.start()
        for pos, ds in reversed(date_positions):
            if pos <= match_pos:
                date_str = ds
                break

        # Date filtering
        if cutoff and date_str:
            try:
                if datetime.strptime(date_str, "%Y-%m-%d") < cutoff:
                    continue
            except ValueError:
                pass

        # Extract substance from title (after the opinion number)
        substance = re.sub(r'SCCS/\d+/\d+\s*[-–]\s*', '', title).strip()
        substance = re.sub(r'^Opinion\s+on\s+', '', substance, flags=re.IGNORECASE).strip()

        violations = _classify_violations(title)

        source_id = f"sccs-{opinion_number}"
        full_url = url_path if url_path.startswith("http") else f"https://health.ec.europa.eu{url_path}"

        action = RegulatoryAction(
            id=source_id,
            source=SourceType.EU_SCCS,
            source_id=source_id,
            title=title[:200],
            description=f"SCCS opinion on {substance}" if substance else title,
            company=substance[:200] if substance else "Unknown",
            product_categories=[ProductCategory.COSMETIC],
            violation_types=violations,
            severity=Severity.ADVISORY,
            date=date_str,
            jurisdiction="EU",
            url=full_url,
            status="Opinion",
        )
        results.append(action)

    return results


async def fetch_sccs_opinions(
    date_from: str | None = None,
) -> list[RegulatoryAction]:
    """Fetch SCCS opinions from the EC website.

    Args:
        date_from: ISO date string for incremental sync

    Returns:
        List of RegulatoryAction records
    """
    try:
        async with httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers=BROWSER_HEADERS,
        ) as client:
            resp = await client.get(SCCS_URL)
            if resp.status_code in (403, 404, 429):
                logger.warning("SCCS page returned %d", resp.status_code)
                return []
            resp.raise_for_status()
    except httpx.HTTPError as e:
        logger.error("Failed to fetch SCCS opinions: %s", e)
        return []

    actions = _parse_sccs_opinions(resp.text, date_from)
    logger.info("Fetched %d SCCS opinions", len(actions))
    return actions
