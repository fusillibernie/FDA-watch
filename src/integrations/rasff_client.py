"""EU RASFF (Rapid Alert System for Food and Feed) client.

Fetches food/feed safety notifications from the RASFF Window portal.
The old backend API (webgate.ec.europa.eu/rasff-window/backend/) is dead.
The official DG SANTE API requires free registration for a subscription key.
This client scrapes the public RASFF Window search page as a no-auth fallback.
"""

import logging
import re
import uuid
from datetime import datetime, timedelta

import httpx

from src.models.enums import ProductCategory, Severity, SourceType, ViolationType
from src.models.enforcement import RegulatoryAction

logger = logging.getLogger(__name__)

RASFF_SEARCH_URL = "https://webgate.ec.europa.eu/rasff-window/screen/list"

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
}

CATEGORY_MAP = {
    "food": ProductCategory.FOOD,
    "feed": ProductCategory.FOOD,
    "food contact material": ProductCategory.FOOD,
    "dietetic food": ProductCategory.DIETARY_SUPPLEMENT,
    "dietary supplement": ProductCategory.DIETARY_SUPPLEMENT,
    "herbs": ProductCategory.DIETARY_SUPPLEMENT,
    "novel food": ProductCategory.DIETARY_SUPPLEMENT,
    "cosmetic": ProductCategory.COSMETIC,
}

HAZARD_KEYWORDS = {
    "pesticide": ViolationType.FOOD_SAFETY_ALERT,
    "mycotoxin": ViolationType.FOOD_SAFETY_ALERT,
    "aflatoxin": ViolationType.FOOD_SAFETY_ALERT,
    "salmonella": ViolationType.FOOD_SAFETY_ALERT,
    "listeria": ViolationType.FOOD_SAFETY_ALERT,
    "e. coli": ViolationType.FOOD_SAFETY_ALERT,
    "heavy metal": ViolationType.FOOD_SAFETY_ALERT,
    "cadmium": ViolationType.FOOD_SAFETY_ALERT,
    "lead": ViolationType.FOOD_SAFETY_ALERT,
    "mercury": ViolationType.FOOD_SAFETY_ALERT,
    "migration": ViolationType.FOOD_SAFETY_ALERT,
    "unauthorized": ViolationType.FOOD_SAFETY_ALERT,
    "allergen": ViolationType.FOOD_SAFETY_ALERT,
    "residue": ViolationType.FOOD_SAFETY_ALERT,
    "fraud": ViolationType.FOOD_SAFETY_ALERT,
    "novel food": ViolationType.FOOD_SAFETY_ALERT,
}

NOTIFICATION_TYPE_SEVERITY = {
    "alert": Severity.CLASS_I,
    "border rejection": Severity.CLASS_II,
    "information": Severity.CLASS_III,
    "news": Severity.ADVISORY,
}


def _classify_categories(product_category: str, subject: str) -> list[ProductCategory]:
    categories: list[ProductCategory] = []
    combined = f"{product_category} {subject}".lower()
    for keyword, cat in CATEGORY_MAP.items():
        if keyword in combined and cat not in categories:
            categories.append(cat)
    return categories or [ProductCategory.FOOD]


def _classify_hazards(subject: str) -> list[ViolationType]:
    violations: list[ViolationType] = []
    lower = subject.lower()
    for keyword, vtype in HAZARD_KEYWORDS.items():
        if keyword in lower and vtype not in violations:
            violations.append(vtype)
    return violations or [ViolationType.FOOD_SAFETY_ALERT]


def _classify_severity(notification_type: str) -> Severity:
    return NOTIFICATION_TYPE_SEVERITY.get(notification_type.lower().strip(), Severity.WARNING)


def _parse_rasff_notifications(notifications: list[dict]) -> list[RegulatoryAction]:
    actions: list[RegulatoryAction] = []

    for notif in notifications:
        reference = notif.get("reference", "") or notif.get("id", "")
        subject = notif.get("subject", "") or ""
        product_category = notif.get("productCategory", "") or notif.get("category", "") or ""
        notification_type = notif.get("notificationType", "") or notif.get("type", "") or ""
        origin_country = notif.get("countryOrigin", "") or notif.get("originCountry", "") or ""

        date_str = ""
        for date_field in ("notificationDate", "date", "ecValidationDate"):
            raw_date = notif.get(date_field, "")
            if raw_date:
                try:
                    date_str = datetime.strptime(
                        str(raw_date)[:10], "%Y-%m-%d"
                    ).strftime("%Y-%m-%d")
                    break
                except ValueError:
                    continue

        company = notif.get("operatorName", "") or notif.get("company", "") or origin_country or "Unknown"
        description = subject
        if notification_type:
            description = f"[{notification_type}] {subject}"
        if origin_country:
            description += f" (Origin: {origin_country})"

        categories = _classify_categories(product_category, subject)
        violations = _classify_hazards(subject)
        severity = _classify_severity(notification_type)

        source_id = f"rasff-{reference}" if reference else f"rasff-{uuid.uuid4().hex[:12]}"

        action = RegulatoryAction(
            id=source_id,
            source=SourceType.EU_RASFF,
            source_id=source_id,
            title=subject[:200] or f"RASFF Notification {reference}",
            description=description[:2000],
            company=company[:200],
            product_categories=categories,
            violation_types=violations,
            severity=severity,
            date=date_str,
            jurisdiction="EU",
            url=f"https://webgate.ec.europa.eu/rasff-window/screen/notification/{reference}" if reference else None,
            status=notification_type or None,
        )
        actions.append(action)

    return actions


def _parse_rasff_html(html: str, date_from: str | None = None) -> list[RegulatoryAction]:
    """Parse RASFF notifications from the RASFF Window HTML page."""
    results: list[RegulatoryAction] = []

    cutoff = None
    if date_from:
        try:
            cutoff = datetime.strptime(date_from, "%Y-%m-%d")
        except ValueError:
            pass
    else:
        cutoff = datetime.now() - timedelta(days=1825)

    # Look for notification rows — the SPA renders a table with notification data
    # Match reference numbers like "2026.1234" and associated text
    ref_pattern = re.compile(
        r'(\d{4}\.\d{3,6})',
    )
    # Date pattern DD/MM/YYYY or YYYY-MM-DD
    date_pattern = re.compile(r'(\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4})')

    # Try to find structured data in JSON embedded in the page (SPA often embeds initial state)
    json_pattern = re.compile(r'"reference"\s*:\s*"(\d{4}\.\d{3,6})"')
    subject_pattern = re.compile(r'"subject"\s*:\s*"([^"]+)"')

    refs = json_pattern.findall(html)
    subjects = subject_pattern.findall(html)

    if refs:
        # Found embedded JSON data
        for i, ref in enumerate(refs):
            subject = subjects[i] if i < len(subjects) else f"RASFF Notification {ref}"
            notif = {
                "reference": ref,
                "subject": subject,
            }
            results.extend(_parse_rasff_notifications([notif]))
    else:
        # Fallback: try to extract from HTML table rows
        row_pattern = re.compile(
            r'<tr[^>]*>.*?</tr>',
            re.DOTALL,
        )
        for row_match in row_pattern.finditer(html):
            row_html = row_match.group(0)
            ref_match = ref_pattern.search(row_html)
            if not ref_match:
                continue

            reference = ref_match.group(1)
            # Extract text content from cells
            cell_texts = re.findall(r'<td[^>]*>(.*?)</td>', row_html, re.DOTALL)
            cell_texts = [re.sub(r'<[^>]+>', '', t).strip() for t in cell_texts]
            subject = next((t for t in cell_texts if len(t) > 20), f"RASFF {reference}")

            date_str = ""
            for text in cell_texts:
                dm = date_pattern.search(text)
                if dm:
                    try:
                        raw = dm.group(1)
                        if "/" in raw:
                            date_str = datetime.strptime(raw, "%d/%m/%Y").strftime("%Y-%m-%d")
                        else:
                            date_str = raw
                        break
                    except ValueError:
                        continue

            if cutoff and date_str:
                try:
                    if datetime.strptime(date_str, "%Y-%m-%d") < cutoff:
                        continue
                except ValueError:
                    pass

            notif = {
                "reference": reference,
                "subject": subject,
                "date": date_str,
            }
            results.extend(_parse_rasff_notifications([notif]))

    return results


async def fetch_rasff_notifications(
    date_from: str | None = None,
    max_records: int = 200,
) -> list[RegulatoryAction]:
    """Fetch RASFF food/feed safety notifications.

    Scrapes the RASFF Window portal HTML since the old backend API is dead
    and the DG SANTE API requires registration.

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
            resp = await client.get(RASFF_SEARCH_URL)
            if resp.status_code in (403, 404, 429):
                logger.warning("RASFF portal returned %d", resp.status_code)
                return []
            resp.raise_for_status()
    except httpx.HTTPError as e:
        logger.error("Failed to fetch RASFF notifications: %s", e)
        return []
    except Exception as e:
        logger.error("RASFF response parse error: %s", e)
        return []

    actions = _parse_rasff_html(resp.text, date_from)
    logger.info("Fetched %d RASFF notifications", len(actions))
    return actions[:max_records]
