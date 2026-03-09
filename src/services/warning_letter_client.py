"""Client for FDA Warning Letters XML dataset."""

import logging
import uuid
import xml.etree.ElementTree as StdET
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import httpx
from defusedxml import ElementTree as ET

if TYPE_CHECKING:
    pass

from src.models.enums import ProductCategory, Severity, SourceType
from src.models.enforcement import RegulatoryAction, WarningLetterMeta

logger = logging.getLogger(__name__)

WARNING_LETTERS_URL = "https://www.fda.gov/media/97981/download"

# Product type keywords for filtering to relevant categories
PRODUCT_TYPE_MAP: dict[str, list[ProductCategory]] = {
    "food": [ProductCategory.FOOD],
    "foods": [ProductCategory.FOOD],
    "dietary supplement": [ProductCategory.DIETARY_SUPPLEMENT],
    "dietary supplements": [ProductCategory.DIETARY_SUPPLEMENT],
    "cosmetic": [ProductCategory.COSMETIC],
    "cosmetics": [ProductCategory.COSMETIC],
    "drug": [ProductCategory.OTC_DRUG],
    "drugs": [ProductCategory.OTC_DRUG],
    "otc": [ProductCategory.OTC_DRUG],
    "device": [ProductCategory.DEVICE],
    "devices": [ProductCategory.DEVICE],
}

RELEVANT_CATEGORIES = {
    ProductCategory.FOOD,
    ProductCategory.DIETARY_SUPPLEMENT,
    ProductCategory.COSMETIC,
    ProductCategory.OTC_DRUG,
}


def _classify_product_type(product_type: str) -> list[ProductCategory]:
    """Map FDA product type string to our ProductCategory enum values."""
    categories: list[ProductCategory] = []
    lower = product_type.lower()
    for keyword, cats in PRODUCT_TYPE_MAP.items():
        if keyword in lower:
            categories.extend(c for c in cats if c not in categories)
    return categories


def _is_relevant(categories: list[ProductCategory]) -> bool:
    """Check if the letter's product categories overlap with our interests."""
    return bool(set(categories) & RELEVANT_CATEGORIES)


def _parse_date(date_str: str | None) -> str:
    """Parse various date formats to ISO format."""
    if not date_str:
        return ""
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%B %d, %Y"):
        try:
            return datetime.strptime(date_str.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return date_str.strip()


def _parse_xml(xml_bytes: bytes, date_from: str | None = None) -> tuple[
    list[WarningLetterMeta], list[RegulatoryAction]
]:
    """Parse the FDA warning letters XML dataset.

    Returns both metadata records and unified RegulatoryAction records.
    """
    root = ET.fromstring(xml_bytes)
    letters: list[WarningLetterMeta] = []
    actions: list[RegulatoryAction] = []

    cutoff = None
    if date_from:
        try:
            cutoff = datetime.strptime(date_from, "%Y-%m-%d")
        except ValueError:
            pass
    else:
        cutoff = datetime.now() - timedelta(days=730)

    for item in root.iter():
        # The XML structure varies; try common tag names
        if item.tag not in ("WarningLetter", "item", "row"):
            continue

        company = _get_text(item, "CompanyName", "FirmName", "company_name")
        subject = _get_text(item, "Subject", "subject", "LetterType")
        issue_date_raw = _get_text(item, "IssuedDate", "PostedDate", "issue_date")
        product_type = _get_text(item, "ProductType", "product_type", "Type")
        close_out = _get_text(item, "CloseOutDate", "close_out_date")
        letter_url = _get_text(item, "URL", "url", "WarningLetterURL")

        if not company:
            continue

        issue_date = _parse_date(issue_date_raw)
        categories = _classify_product_type(product_type)

        if not _is_relevant(categories):
            continue

        if cutoff and issue_date:
            try:
                letter_date = datetime.strptime(issue_date, "%Y-%m-%d")
                if letter_date < cutoff:
                    continue
            except ValueError:
                pass

        letter_id = f"wl-{uuid.uuid4().hex[:12]}"

        meta = WarningLetterMeta(
            letter_id=letter_id,
            company=company,
            subject=subject or "Warning Letter",
            issue_date=issue_date,
            product_type=product_type,
            close_out_date=_parse_date(close_out) if close_out else None,
            url=letter_url,
        )
        letters.append(meta)

        action = RegulatoryAction(
            id=letter_id,
            source=SourceType.FDA_WARNING_LETTER,
            source_id=letter_id,
            title=f"Warning Letter: {company}" + (f" - {subject}" if subject else ""),
            description=subject or "FDA Warning Letter",
            company=company,
            product_categories=categories,
            violation_types=[],  # Filled by classifier
            severity=Severity.WARNING,
            date=issue_date,
            url=letter_url,
            status="Closed" if close_out else "Open",
        )
        actions.append(action)

    logger.info("Parsed %d relevant warning letters from XML", len(letters))
    return letters, actions


def _get_text(element: StdET.Element, *tag_names: str) -> str:
    """Try multiple tag names and return the first non-empty text found."""
    for tag in tag_names:
        child = element.find(tag)
        if child is not None and child.text:
            return child.text.strip()
        # Also check attributes
        val = element.get(tag, "")
        if val:
            return val.strip()
    return ""


async def fetch_warning_letters(
    date_from: str | None = None,
) -> tuple[list[WarningLetterMeta], list[RegulatoryAction]]:
    """Download and parse FDA warning letters XML.

    Args:
        date_from: ISO date string (YYYY-MM-DD) for incremental sync

    Returns:
        Tuple of (letter metadata list, regulatory action list)
    """
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        try:
            resp = await client.get(WARNING_LETTERS_URL)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.error("Failed to download warning letters XML: %s", e)
            return [], []

    return _parse_xml(resp.content, date_from)
