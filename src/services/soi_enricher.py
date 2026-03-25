"""Standards of Identity enrichment service.

Extracts food subcategory and CFR references from SOI-classified actions.
"""

import json
import logging
import re
from pathlib import Path

from src.models.enums import FoodSubcategory, ViolationType
from src.models.enforcement import RegulatoryAction

logger = logging.getLogger(__name__)

SOI_STANDARDS_FILE = Path(__file__).parent.parent.parent / "data" / "reference" / "soi_standards.json"


class SOIEnricher:
    """Enriches standards-of-identity actions with food subcategory and CFR references."""

    def __init__(self, standards_file: Path | None = None):
        self._standards: list[dict] = []
        self._load(standards_file or SOI_STANDARDS_FILE)

    def _load(self, path: Path) -> None:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._standards = data.get("standards", [])
            logger.info("Loaded %d SOI standards", len(self._standards))
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.warning("Could not load SOI standards: %s", e)

    def enrich(self, action: RegulatoryAction) -> dict:
        """Extract SOI metadata from an action.

        Returns dict with:
            food_subcategory: FoodSubcategory value or None
            cfr_references: list of matched CFR parts
            matched_products: list of product names found in text
        """
        if ViolationType.STANDARDS_OF_IDENTITY not in action.violation_types:
            return {"food_subcategory": None, "cfr_references": [], "matched_products": []}

        text = f"{action.title} {action.description}".lower()

        best_score = 0
        best_standard = None
        all_cfr_refs: list[str] = []
        all_products: list[str] = []

        for std in self._standards:
            score = 0
            matched_products = []

            # Check keywords
            for kw in std.get("keywords", []):
                if kw.lower() in text:
                    score += 1

            # Check product names
            for prod in std.get("products", []):
                if prod.lower() in text:
                    score += 2  # Product match is stronger
                    matched_products.append(prod)

            # Check CFR reference in text
            cfr = std.get("cfr_part", "")
            if cfr and cfr.lower().replace(" ", "") in text.replace(" ", ""):
                score += 3  # Explicit CFR citation is strongest
                if cfr not in all_cfr_refs:
                    all_cfr_refs.append(cfr)

            if score > best_score:
                best_score = score
                best_standard = std
                all_products = matched_products

            if matched_products and cfr and cfr not in all_cfr_refs:
                all_cfr_refs.append(cfr)

        # Also scan for CFR patterns not in our standards list
        cfr_pattern = re.compile(r"21\s*CFR\s*(Part\s*)?\d{2,3}", re.IGNORECASE)
        for m in cfr_pattern.finditer(f"{action.title} {action.description}"):
            ref = m.group(0).strip()
            if ref not in all_cfr_refs:
                all_cfr_refs.append(ref)

        subcategory = None
        if best_standard and best_score > 0:
            try:
                subcategory = FoodSubcategory(best_standard["subcategory"])
            except (ValueError, KeyError):
                pass

        return {
            "food_subcategory": subcategory.value if subcategory else None,
            "cfr_references": all_cfr_refs,
            "matched_products": all_products,
        }

    def enrich_batch(self, actions: list[RegulatoryAction]) -> dict[str, dict]:
        """Enrich a batch of actions. Returns {action_id: enrichment_dict}."""
        results = {}
        for action in actions:
            results[action.id] = self.enrich(action)
        return results
