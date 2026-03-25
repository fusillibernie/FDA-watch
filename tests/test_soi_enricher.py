"""Tests for the SOI enrichment service."""

import json
import tempfile
from pathlib import Path

import pytest

from src.models.enforcement import RegulatoryAction
from src.models.enums import (
    FoodSubcategory,
    ProductCategory,
    Severity,
    SourceType,
    ViolationType,
)
from src.services.soi_enricher import SOIEnricher


def _make_action(
    title: str,
    description: str,
    violation_types: list[ViolationType] | None = None,
    action_id: str = "test-001",
) -> RegulatoryAction:
    return RegulatoryAction(
        id=action_id,
        source=SourceType.OPENFDA_ENFORCEMENT,
        source_id=f"src-{action_id}",
        title=title,
        description=description,
        company="Test Co",
        product_categories=[ProductCategory.FOOD],
        violation_types=violation_types or [ViolationType.STANDARDS_OF_IDENTITY],
        severity=Severity.WARNING,
        date="2026-03-20",
    )


@pytest.fixture
def enricher():
    return SOIEnricher()


class TestSOIEnricherIceCream:
    def test_ice_cream_subcategory(self, enricher):
        action = _make_action(
            "Recall: Frozen Dessert Products",
            "The ice cream product does not meet 21 CFR 135 standards for ice cream. "
            "Product lacks required milkfat content.",
        )
        result = enricher.enrich(action)
        assert result["food_subcategory"] == "frozen_desserts"
        assert "21 CFR 135" in result["cfr_references"]
        assert "ice cream" in result["matched_products"]

    def test_frozen_custard(self, enricher):
        action = _make_action(
            "Warning: Frozen Custard Labeling",
            "Product labeled as frozen custard does not meet standard of identity.",
        )
        result = enricher.enrich(action)
        assert result["food_subcategory"] == "frozen_desserts"


class TestSOIEnricherHoney:
    def test_honey_subcategory(self, enricher):
        action = _make_action(
            "Recall: Adulterated Honey Products",
            "Product labeled as pure honey contained corn syrup. "
            "Violates 21 CFR 168 standard of identity for honey.",
        )
        result = enricher.enrich(action)
        assert result["food_subcategory"] == "sweeteners"
        assert "21 CFR 168" in result["cfr_references"]
        assert "honey" in result["matched_products"]


class TestSOIEnricherCheese:
    def test_cheese_subcategory(self, enricher):
        action = _make_action(
            "Warning Letter: Cheese Product Misbranding",
            "Product labeled as parmesan cheese does not meet the standard of identity "
            "for cheese under 21 CFR 133.",
        )
        result = enricher.enrich(action)
        assert result["food_subcategory"] == "cheese"
        assert "21 CFR 133" in result["cfr_references"]

    def test_mozzarella(self, enricher):
        action = _make_action(
            "Enforcement: Mozzarella Standards",
            "Product labeled as mozzarella cheese does not conform to standard.",
        )
        result = enricher.enrich(action)
        assert result["food_subcategory"] == "cheese"


class TestSOIEnricherVanilla:
    def test_vanilla_condiments(self, enricher):
        action = _make_action(
            "Warning: Vanilla Extract Labeling",
            "Product labeled as vanilla extract does not meet 21 CFR 169 standard "
            "for vanilla extract. Contains artificial vanillin.",
        )
        result = enricher.enrich(action)
        assert result["food_subcategory"] == "condiments"
        assert "21 CFR 169" in result["cfr_references"]
        assert "vanilla extract" in result["matched_products"]


class TestSOIEnricherOliveOil:
    def test_olive_oil_subcategory(self, enricher):
        action = _make_action(
            "Warning: Extra Virgin Olive Oil Fraud",
            "Product labeled as extra virgin olive oil does not meet standard. "
            "Testing shows it is refined olive oil.",
        )
        result = enricher.enrich(action)
        assert result["food_subcategory"] == "oils_fats"
        assert "extra virgin olive oil" in result["matched_products"]


class TestSOIEnricherNonSOI:
    def test_non_soi_returns_empty(self, enricher):
        action = _make_action(
            "Recall: Contaminated Product",
            "Product recalled due to contamination with listeria.",
            violation_types=[ViolationType.CONTAMINATION],
        )
        result = enricher.enrich(action)
        assert result["food_subcategory"] is None
        assert result["cfr_references"] == []
        assert result["matched_products"] == []


class TestSOIEnricherCFRPatterns:
    def test_cfr_pattern_extraction(self, enricher):
        action = _make_action(
            "Warning: Multiple CFR Violations",
            "Product fails to meet requirements under 21 CFR 131 for milk "
            "and 21 CFR Part 133 for cheese products.",
        )
        result = enricher.enrich(action)
        assert len(result["cfr_references"]) >= 2
        # Should find both CFR references
        refs_lower = [r.lower().replace(" ", "") for r in result["cfr_references"]]
        assert any("131" in r for r in refs_lower)
        assert any("133" in r for r in refs_lower)

    def test_cfr_pattern_not_in_standards(self, enricher):
        """CFR references not in our list should still be captured by regex."""
        action = _make_action(
            "Warning: SOI Violation",
            "Product violates 21 CFR 170 food additive requirements and honey standards.",
        )
        result = enricher.enrich(action)
        assert any("170" in ref for ref in result["cfr_references"])


class TestSOIEnricherBatch:
    def test_batch_enrichment(self, enricher):
        actions = [
            _make_action(
                "Recall: Ice Cream",
                "Ice cream does not meet 21 CFR 135 standard.",
                action_id="batch-001",
            ),
            _make_action(
                "Recall: Honey",
                "Honey product adulterated per 21 CFR 168.",
                action_id="batch-002",
            ),
            _make_action(
                "Recall: Contaminated",
                "Product contaminated with salmonella.",
                violation_types=[ViolationType.CONTAMINATION],
                action_id="batch-003",
            ),
        ]
        results = enricher.enrich_batch(actions)
        assert len(results) == 3
        assert results["batch-001"]["food_subcategory"] == "frozen_desserts"
        assert results["batch-002"]["food_subcategory"] == "sweeteners"
        assert results["batch-003"]["food_subcategory"] is None


class TestSOIEnricherEdgeCases:
    def test_missing_standards_file(self):
        enricher = SOIEnricher(standards_file=Path("/nonexistent/path.json"))
        action = _make_action("Test", "Test ice cream product")
        result = enricher.enrich(action)
        # Still returns valid structure, just no matches from standards
        assert result["food_subcategory"] is None
        assert isinstance(result["cfr_references"], list)
        assert isinstance(result["matched_products"], list)

    def test_custom_standards_file(self, tmp_path):
        custom = tmp_path / "custom_standards.json"
        custom.write_text(json.dumps({
            "standards": [
                {
                    "cfr_part": "21 CFR 999",
                    "title": "Custom Standard",
                    "subcategory": "dairy",
                    "products": ["test milk"],
                    "keywords": ["test milk"],
                }
            ]
        }))
        enricher = SOIEnricher(standards_file=custom)
        action = _make_action("Custom", "This is test milk product")
        result = enricher.enrich(action)
        assert result["food_subcategory"] == "dairy"
        assert "test milk" in result["matched_products"]

    def test_soi_action_no_food_keywords(self, enricher):
        """SOI action with no recognizable food terms."""
        action = _make_action(
            "Warning: Generic Product",
            "This product does not conform to the applicable standard of identity.",
        )
        result = enricher.enrich(action)
        # May or may not match; should not crash
        assert isinstance(result["cfr_references"], list)
        assert isinstance(result["matched_products"], list)
