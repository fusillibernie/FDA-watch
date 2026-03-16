"""Tests for data models."""

from src.models.enums import ProductCategory, RegulationStage, Severity, SourceType, ViolationType
from src.models.enforcement import RegulatoryAction, WarningLetterMeta
from src.models.alerts import AlertRule, AlertMatch
from src.models.regulation import RegulationChange


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


def test_eu_source_types():
    assert SourceType.EU_RAPEX.value == "eu_rapex"
    assert SourceType.EU_RASFF.value == "eu_rasff"
    assert SourceType.EU_SCCS.value == "eu_sccs"
    assert SourceType.EU_ECHA_REACH.value == "eu_echa_reach"


def test_eu_violation_types():
    assert ViolationType.PRODUCT_SAFETY_RISK.value == "product_safety_risk"
    assert ViolationType.FOOD_SAFETY_ALERT.value == "food_safety_alert"
    assert ViolationType.RESTRICTED_SUBSTANCE.value == "restricted_substance"
    assert ViolationType.COSMETIC_SAFETY_CONCERN.value == "cosmetic_safety_concern"


def test_regulatory_action_jurisdiction():
    action = RegulatoryAction(
        id="eu-test-001",
        source=SourceType.EU_RAPEX,
        source_id="rapex-12345",
        title="EU Safety Gate Alert",
        description="Product safety risk detected.",
        company="EU Test Corp",
        product_categories=[ProductCategory.COSMETIC],
        violation_types=[ViolationType.PRODUCT_SAFETY_RISK],
        severity=Severity.WARNING,
        date="2025-06-01",
        jurisdiction="EU",
    )
    assert action.jurisdiction == "EU"
    assert action.source == SourceType.EU_RAPEX


def test_regulatory_action_default_jurisdiction():
    action = RegulatoryAction(
        id="us-test-001",
        source=SourceType.OPENFDA_ENFORCEMENT,
        source_id="F-1234-2025",
        title="US enforcement action",
        description="Test",
        company="Test Corp",
        product_categories=[ProductCategory.FOOD],
        violation_types=[ViolationType.ADULTERATION],
        severity=Severity.CLASS_I,
        date="2025-01-15",
    )
    assert action.jurisdiction == "US"


def test_regulation_change_model():
    change = RegulationChange(
        id="fr-test-001",
        source=SourceType.FEDERAL_REGISTER,
        source_id="fr-2025-12345",
        title="Final Rule: Food Labeling Requirements",
        summary="FDA issues final rule updating food labeling.",
        agency="FDA",
        stage=RegulationStage.FINAL_RULE,
        product_categories=[ProductCategory.FOOD],
        date_published="2025-06-15",
        date_effective="2025-12-15",
        date_comments_close=None,
    )
    assert change.jurisdiction == "US"
    assert change.stage == RegulationStage.FINAL_RULE
    assert change.date_effective == "2025-12-15"


def test_regulation_stage_enum():
    assert RegulationStage.PROPOSED_RULE.value == "proposed_rule"
    assert RegulationStage.FINAL_RULE.value == "final_rule"
    assert RegulationStage.AMENDMENT.value == "amendment"
    assert RegulationStage.GUIDANCE_DRAFT.value == "guidance_draft"


def test_regulation_source_types():
    assert SourceType.FEDERAL_REGISTER.value == "federal_register"
    assert SourceType.FDA_GUIDANCE.value == "fda_guidance"
    assert SourceType.EU_OFFICIAL_JOURNAL.value == "eu_official_journal"
    assert SourceType.IFRA_AMENDMENT.value == "ifra_amendment"


def test_alert_match_regulation_change():
    match = AlertMatch(
        id="match-reg-001",
        alert_rule_id="rule-001",
        regulation_change_id="fr-test-001",
        matched_keywords=["food labeling"],
        matched_at="2025-06-15T12:00:00Z",
    )
    assert match.action_id is None
    assert match.regulation_change_id == "fr-test-001"
