"""EUR-Lex regulation change client.

Fetches EU regulation amendments via the Publications Office SPARQL endpoint.
This is the recommended public API (no auth required, JSON output).
The old HTML search endpoint was unreliable for programmatic access.
"""

import logging
import re
import uuid
from datetime import datetime, timedelta
from urllib.parse import quote

import httpx

from src.models.enums import ProductCategory, RegulationStage, SourceType
from src.models.regulation import RegulationChange

logger = logging.getLogger(__name__)

SPARQL_ENDPOINT = "https://publications.europa.eu/webapi/rdf/sparql"

# Key EU regulations to track
TRACKED_REGULATIONS = {
    "1223/2009": ("Cosmetics Regulation", ProductCategory.COSMETIC),
    "1907/2006": ("REACH Regulation", ProductCategory.COSMETIC),
    "2015/2283": ("Novel Foods Regulation", ProductCategory.FOOD),
    "178/2002": ("General Food Law", ProductCategory.FOOD),
    "1935/2004": ("Food Contact Materials", ProductCategory.FOOD),
    "2017/745": ("Medical Devices Regulation", ProductCategory.DEVICE),
}

# Keywords for filtering relevant results
RELEVANCE_KEYWORDS = [
    "cosmetic", "food", "device", "REACH", "novel food",
    "1223/2009", "1907/2006", "2015/2283", "178/2002", "1935/2004", "2017/745",
    "fragrance", "allergen", "additive", "supplement", "medical device",
]

STAGE_KEYWORDS = {
    "amending": RegulationStage.AMENDMENT,
    "corrigendum": RegulationStage.AMENDMENT,
    "implementing": RegulationStage.FINAL_RULE,
    "delegated": RegulationStage.FINAL_RULE,
    "repealing": RegulationStage.FINAL_RULE,
    "proposal": RegulationStage.PROPOSED_RULE,
}


def _classify_categories(title: str) -> list[ProductCategory]:
    categories: list[ProductCategory] = []
    lower = title.lower()
    for reg_num, (_, cat) in TRACKED_REGULATIONS.items():
        if reg_num in title and cat not in categories:
            categories.append(cat)
    if "cosmetic" in lower and ProductCategory.COSMETIC not in categories:
        categories.append(ProductCategory.COSMETIC)
    if "food" in lower and ProductCategory.FOOD not in categories:
        categories.append(ProductCategory.FOOD)
    if "device" in lower and ProductCategory.DEVICE not in categories:
        categories.append(ProductCategory.DEVICE)
    return categories or [ProductCategory.COSMETIC]


def _classify_stage(title: str) -> RegulationStage:
    lower = title.lower()
    for keyword, stage in STAGE_KEYWORDS.items():
        if keyword in lower:
            return stage
    return RegulationStage.AMENDMENT


def _build_sparql_query(start_date: str, limit: int = 500, offset: int = 0) -> str:
    """Build a SPARQL query to find relevant EU regulations."""
    # Build a FILTER with relevance keywords
    keyword_filters = " || ".join(
        f'CONTAINS(LCASE(?title), "{kw.lower()}")'
        for kw in RELEVANCE_KEYWORDS
    )

    return f"""
PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>

SELECT DISTINCT ?work ?title ?date ?celex WHERE {{
  ?work cdm:work_date_document ?date .
  ?work cdm:resource_legal_id_celex ?celex .
  ?exp cdm:expression_belongs_to_work ?work .
  ?exp cdm:expression_title ?title .

  FILTER(?date >= "{start_date}"^^xsd:date)
  FILTER(LANG(?title) = "en" || LANG(?title) = "")
  FILTER({keyword_filters})
}}
ORDER BY DESC(?date)
LIMIT {limit}
OFFSET {offset}
"""


def _parse_sparql_results(data: dict) -> list[RegulationChange]:
    """Parse SPARQL JSON results into RegulationChange records."""
    results: list[RegulationChange] = []
    bindings = data.get("results", {}).get("bindings", [])

    for binding in bindings:
        title = binding.get("title", {}).get("value", "")
        date_str = binding.get("date", {}).get("value", "")
        celex = binding.get("celex", {}).get("value", "")
        work_uri = binding.get("work", {}).get("value", "")

        if not title:
            continue

        # Clean up date
        if date_str:
            date_str = date_str[:10]  # YYYY-MM-DD

        stage = _classify_stage(title)
        categories = _classify_categories(title)

        # Build source ID from CELEX number
        celex_clean = re.sub(r'[^a-zA-Z0-9]', '-', celex).strip('-') if celex else uuid.uuid4().hex[:12]
        source_id = f"eurlex-{celex_clean}"

        # Build URL from CELEX number
        url = f"https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:{celex}" if celex else None

        change = RegulationChange(
            id=source_id,
            source=SourceType.EU_OFFICIAL_JOURNAL,
            source_id=source_id,
            title=title[:300],
            summary=title,
            agency="EU Commission",
            stage=stage,
            product_categories=categories,
            date_published=date_str,
            jurisdiction="EU",
            url=url,
        )
        results.append(change)

    return results


async def fetch_eurlex_changes(
    date_from: str | None = None,
    max_records: int = 500,
) -> list[RegulationChange]:
    """Fetch EU regulation changes from EUR-Lex via SPARQL.

    Uses the Publications Office SPARQL endpoint which is public,
    requires no authentication, and returns JSON.

    Args:
        date_from: ISO date string for incremental sync
        max_records: Maximum records to return

    Returns:
        List of RegulationChange records
    """
    start_date = date_from or (datetime.now() - timedelta(days=1825)).strftime("%Y-%m-%d")

    page_size = 100
    all_changes: list[RegulationChange] = []

    try:
        async with httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
        ) as client:
            offset = 0
            while offset < max_records:
                query = _build_sparql_query(start_date, limit=page_size, offset=offset)
                params = {
                    "query": query,
                    "format": "application/json",
                }
                resp = await client.get(SPARQL_ENDPOINT, params=params)
                if resp.status_code in (403, 404, 429):
                    logger.warning("EUR-Lex SPARQL returned %d", resp.status_code)
                    break
                resp.raise_for_status()
                data = resp.json()

                changes = _parse_sparql_results(data)
                if not changes:
                    break
                all_changes.extend(changes)
                if len(changes) < page_size:
                    break
                offset += page_size
    except httpx.HTTPError as e:
        logger.error("Failed to fetch EUR-Lex: %s", e)
    except Exception as e:
        logger.error("EUR-Lex SPARQL parse error: %s", e)

    logger.info("Fetched %d EUR-Lex regulation changes", len(all_changes))
    return all_changes
