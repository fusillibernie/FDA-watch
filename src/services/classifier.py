"""Rule-based violation classifier using keyword/pattern matching."""

import json
import logging
import re
from pathlib import Path

from src.models.enums import ViolationType
from src.models.enforcement import RegulatoryAction

logger = logging.getLogger(__name__)

TAXONOMY_PATH = Path(__file__).parent.parent.parent / "data" / "reference" / "violation_taxonomy.json"


class ViolationClassifier:
    """Classifies regulatory actions by violation type using keyword and regex rules."""

    def __init__(self, taxonomy_path: Path | None = None):
        self.taxonomy: dict[str, dict] = {}
        path = taxonomy_path or TAXONOMY_PATH
        self._load_taxonomy(path)

    def _load_taxonomy(self, path: Path) -> None:
        """Load the violation taxonomy from JSON."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                self.taxonomy = json.load(f)
            logger.info("Loaded violation taxonomy with %d types", len(self.taxonomy))
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.warning("Could not load taxonomy from %s: %s", path, e)
            self.taxonomy = {}

    def classify(self, action: RegulatoryAction) -> list[ViolationType]:
        """Classify a regulatory action and return matching violation types.

        Scans title + description against taxonomy keywords and patterns.
        Returns a list of ViolationType values sorted by confidence (hit count).
        """
        text = f"{action.title} {action.description}".lower()
        scores: dict[ViolationType, int] = {}

        for type_key, rules in self.taxonomy.items():
            try:
                vtype = ViolationType(type_key)
            except ValueError:
                continue

            score = 0
            keywords = rules.get("keywords", [])
            patterns = rules.get("patterns", [])

            for kw in keywords:
                if kw.lower() in text:
                    score += 1

            for pattern in patterns:
                try:
                    if re.search(pattern, text, re.IGNORECASE):
                        score += 2  # Pattern matches are stronger signals
                except re.error:
                    logger.warning("Invalid regex pattern in taxonomy: %s", pattern)

            if score > 0:
                scores[vtype] = score

        if not scores:
            return [ViolationType.OTHER]

        # Sort by score descending, return all matching types
        return [vt for vt, _ in sorted(scores.items(), key=lambda x: x[1], reverse=True)]

    def classify_batch(self, actions: list[RegulatoryAction]) -> list[RegulatoryAction]:
        """Classify a batch of actions in place, setting violation_types on each."""
        for action in actions:
            if not action.violation_types:
                action.violation_types = self.classify(action)
        return actions
