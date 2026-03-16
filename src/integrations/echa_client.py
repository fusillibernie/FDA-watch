"""ECHA/REACH substances client.

Fetches SVHC Candidate List substances from the ECHA website.
ECHA has no public REST API for substance data (returns 403).
This client scrapes the Candidate List HTML table, which contains ~253 SVHCs
and updates only a few times per year.
"""

import logging
import re
import uuid
from datetime import datetime, timedelta

import httpx

from src.models.enums import ProductCategory, Severity, SourceType, ViolationType
from src.models.enforcement import RegulatoryAction

logger = logging.getLogger(__name__)

# ECHA Candidate List page (SVHC substances)
ECHA_CANDIDATE_URL = "https://echa.europa.eu/candidate-list-table"

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
}

CATEGORY_KEYWORDS = {
    "cosmetic": ProductCategory.COSMETIC,
    "food contact": ProductCategory.FOOD,
    "food": ProductCategory.FOOD,
    "toy": ProductCategory.DEVICE,
    "childcare": ProductCategory.DEVICE,
    "textile": ProductCategory.DEVICE,
    "skin": ProductCategory.COSMETIC,
}

ACTION_KEYWORDS = {
    "restriction": ViolationType.RESTRICTED_SUBSTANCE,
    "authorisation": ViolationType.RESTRICTED_SUBSTANCE,
    "authorization": ViolationType.RESTRICTED_SUBSTANCE,
    "candidate list": ViolationType.RESTRICTED_SUBSTANCE,
    "svhc": ViolationType.RESTRICTED_SUBSTANCE,
    "cmr": ViolationType.RESTRICTED_SUBSTANCE,
    "carcinogenic": ViolationType.RESTRICTED_SUBSTANCE,
    "mutagenic": ViolationType.RESTRICTED_SUBSTANCE,
    "toxic to reproduction": ViolationType.RESTRICTED_SUBSTANCE,
    "endocrine": ViolationType.COSMETIC_SAFETY_CONCERN,
    "sensitiser": ViolationType.COSMETIC_SAFETY_CONCERN,
    "sensitizer": ViolationType.COSMETIC_SAFETY_CONCERN,
    "pbt": ViolationType.RESTRICTED_SUBSTANCE,
    "vpvb": ViolationType.RESTRICTED_SUBSTANCE,
}


def _classify_categories(text: str) -> list[ProductCategory]:
    categories: list[ProductCategory] = []
    lower = text.lower()
    for keyword, cat in CATEGORY_KEYWORDS.items():
        if keyword in lower and cat not in categories:
            categories.append(cat)
    return categories or [ProductCategory.COSMETIC]


def _classify_actions(text: str) -> list[ViolationType]:
    violations: list[ViolationType] = []
    lower = text.lower()
    for keyword, vtype in ACTION_KEYWORDS.items():
        if keyword in lower and vtype not in violations:
            violations.append(vtype)
    return violations or [ViolationType.RESTRICTED_SUBSTANCE]


def _parse_echa_substances(substances: list[dict]) -> list[RegulatoryAction]:
    actions: list[RegulatoryAction] = []

    for substance in substances:
        ec_number = substance.get("ecNumber", "") or substance.get("ec_number", "")
        cas_number = substance.get("casNumber", "") or substance.get("cas_number", "")
        name = substance.get("substanceName", "") or substance.get("name", "") or ""
        reason = substance.get("reasonForInclusion", "") or substance.get("reason", "") or ""
        action_type = substance.get("actionType", "") or substance.get("type", "") or ""

        date_str = ""
        for date_field in ("inclusionDate", "date", "decisionDate"):
            raw_date = substance.get(date_field, "")
            if raw_date:
                try:
                    date_str = datetime.strptime(
                        str(raw_date)[:10], "%Y-%m-%d"
                    ).strftime("%Y-%m-%d")
                    break
                except ValueError:
                    continue

        combined_text = f"{name} {reason} {action_type}"
        categories = _classify_categories(combined_text)
        violations = _classify_actions(combined_text)

        id_part = ec_number or cas_number or uuid.uuid4().hex[:12]
        action_suffix = action_type.lower().replace(" ", "_")[:20] if action_type else "svhc"
        source_id = f"echa-{id_part}-{action_suffix}"

        description = f"{name}"
        if reason:
            description += f". Reason: {reason}"
        if cas_number:
            description += f" (CAS: {cas_number})"

        action = RegulatoryAction(
            id=source_id,
            source=SourceType.EU_ECHA_REACH,
            source_id=source_id,
            title=f"{name[:150]} - {action_type}" if action_type else name[:200],
            description=description[:2000],
            company=name[:200],
            product_categories=categories,
            violation_types=violations,
            severity=Severity.WARNING,
            date=date_str,
            jurisdiction="EU",
            url=None,
            status=action_type or "Candidate List",
        )
        actions.append(action)

    return actions


def _parse_echa_html(html: str, date_from: str | None = None) -> list[RegulatoryAction]:
    """Parse ECHA Candidate List table from HTML."""
    substances: list[dict] = []

    cutoff = None
    if date_from:
        try:
            cutoff = datetime.strptime(date_from, "%Y-%m-%d")
        except ValueError:
            pass

    # The candidate list table has columns:
    # Substance name | EC No. | CAS No. | Date of inclusion | Reason for inclusion
    row_pattern = re.compile(r'<tr[^>]*>(.*?)</tr>', re.DOTALL)
    cell_pattern = re.compile(r'<td[^>]*>(.*?)</td>', re.DOTALL)

    for row_match in row_pattern.finditer(html):
        row_html = row_match.group(1)
        cells = cell_pattern.findall(row_html)
        if len(cells) < 4:
            continue

        # Strip HTML tags from cell content
        clean_cells = [re.sub(r'<[^>]+>', '', c).strip() for c in cells]

        name = clean_cells[0]
        if not name or len(name) < 3:
            continue

        ec_number = clean_cells[1] if len(clean_cells) > 1 else ""
        cas_number = clean_cells[2] if len(clean_cells) > 2 else ""
        date_raw = clean_cells[3] if len(clean_cells) > 3 else ""
        reason = clean_cells[4] if len(clean_cells) > 4 else ""

        # Parse date (various formats: DD/MM/YYYY, YYYY-MM-DD, DD Month YYYY)
        date_str = ""
        if date_raw:
            for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d %B %Y"):
                try:
                    date_str = datetime.strptime(date_raw.strip(), fmt).strftime("%Y-%m-%d")
                    break
                except ValueError:
                    continue

        if cutoff and date_str:
            try:
                if datetime.strptime(date_str, "%Y-%m-%d") < cutoff:
                    continue
            except ValueError:
                pass

        substances.append({
            "name": name,
            "ec_number": ec_number,
            "cas_number": cas_number,
            "date": date_str,
            "reason": reason,
            "type": "SVHC Candidate List",
        })

    return _parse_echa_substances(substances)


async def fetch_echa_substances(
    date_from: str | None = None,
    max_records: int = 200,
) -> list[RegulatoryAction]:
    """Fetch ECHA/REACH SVHC Candidate List substances.

    Scrapes the ECHA Candidate List HTML table since ECHA has no public
    REST API. The list contains ~253 substances and updates a few times
    per year.

    Args:
        date_from: ISO date string (YYYY-MM-DD) for incremental sync
        max_records: Maximum records to return

    Returns:
        List of RegulatoryAction records
    """
    try:
        async with httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers=BROWSER_HEADERS,
        ) as client:
            resp = await client.get(ECHA_CANDIDATE_URL)
            if resp.status_code in (403, 404, 429):
                logger.warning("ECHA page returned %d", resp.status_code)
                return []
            resp.raise_for_status()
    except httpx.HTTPError as e:
        logger.error("Failed to fetch ECHA substances: %s", e)
        return []
    except Exception as e:
        logger.error("ECHA response parse error: %s", e)
        return []

    actions = _parse_echa_html(resp.text, date_from)
    logger.info("Fetched %d ECHA/REACH substances", len(actions))
    return actions[:max_records]
