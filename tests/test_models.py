"""Tests for data models."""

from src.models.enums import ProductCategory, Severity, SourceType, ViolationType
from src.models.enforcement import RegulatoryAction, WarningLetterMeta, NADCase
from src.models.alerts import AlertRule, AlertMatch


def test_regulatory_action_creation():
    action = RegulatoryAction(
        id="test-001",
        source=SourceType.OPENFDA_ENFORCEMENT,
        source_id="F-1234-2025",
        title="Undeclared peanuts in snack product",
        description="Product contains undeclared peanuts. People with allergies may have serious reactions.",
        company="Test Foods Inc.",
        product_categories=[ProductCategory.FOOD],
        violation_types=[ViolationType.UNDECLARED_ALLERGEN],
        severity=Severity.CLASS_I,
        date="2025-01-15",
    )
    assert action.id == "test-001"
    assert action.source == SourceType.OPENFDA_ENFORCEMENT
    assert ProductCategory.FOOD in action.product_categories
    assert action.severity == Severity.CLASS_I


def test_regulatory_action_optional_fields():
    action = RegulatoryAction(
        id="test-002",
        source=SourceType.FDA_WARNING_LETTER,
        source_id="wl-abc123",
        title="Warning Letter",
        description="GMP violations found",
        company="Test Corp",
        product_categories=[ProductCategory.DIETARY_SUPPLEMENT],
        violation_types=[ViolationType.GMP_VIOLATION],
        severity=Severity.WARNING,
        date="2025-03-01",
    )
    assert action.url is None
    assert action.status is None
    assert action.distribution is None
    assert action.raw_data is None


def test_warning_letter_meta():
    letter = WarningLetterMeta(
        letter_id="wl-001",
        company="Supplement Co",
        subject="CGMP violations",
        issue_date="2025-02-01",
        product_type="Dietary Supplements",
    )
    assert letter.close_out_date is None
    assert letter.company == "Supplement Co"


def test_nad_case():
    case = NADCase(
        case_id="nad-001",
        advertiser="Brand X",
        product="Skin Cream",
        claims_at_issue="Anti-aging claims",
        decision="Modified",
        date="2025-01-20",
        product_categories=[ProductCategory.COSMETIC],
        violation_types=[ViolationType.UNSUBSTANTIATED_CLAIM],
    )
    assert case.challenger is None
    assert case.decision == "Modified"


def test_alert_rule():
    rule = AlertRule(
        id="rule-001",
        name="Allergen alerts",
        keywords=["undeclared", "allergen", "peanut"],
        product_categories=[ProductCategory.FOOD],
        created_at="2025-01-01T00:00:00Z",
    )
    assert rule.active is True
    assert len(rule.keywords) == 3


def test_alert_match():
    match = AlertMatch(
        id="match-001",
        alert_rule_id="rule-001",
        action_id="test-001",
        matched_keywords=["undeclared", "peanut"],
        matched_at="2025-01-15T12:00:00Z",
    )
    assert match.read is False
    assert len(match.matched_keywords) == 2


def test_enum_values():
    assert ProductCategory.FOOD.value == "food"
    assert ViolationType.ADULTERATION.value == "adulteration"
    assert Severity.CLASS_I.value == "class_i"
    assert SourceType.OPENFDA_ENFORCEMENT.value == "openfda_enforcement"
