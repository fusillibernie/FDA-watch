"""Tests for rule-based violation classifier."""

from src.models.enums import ProductCategory, Severity, SourceType, ViolationType
from src.models.enforcement import RegulatoryAction
from src.services.classifier import ViolationClassifier


def _make_action(title: str, description: str) -> RegulatoryAction:
    return RegulatoryAction(
        id="test",
        source=SourceType.OPENFDA_ENFORCEMENT,
        source_id="test",
        title=title,
        description=description,
        company="Test Co",
        product_categories=[ProductCategory.FOOD],
        violation_types=[],
        severity=Severity.CLASS_I,
        date="2025-01-01",
    )


def test_classify_undeclared_allergen():
    clf = ViolationClassifier()
    action = _make_action(
        "Recall of snack bars",
        "Product contains undeclared peanuts. People with peanut allergies may have serious reactions.",
    )
    types = clf.classify(action)
    assert ViolationType.UNDECLARED_ALLERGEN in types


def test_classify_adulteration():
    clf = ViolationClassifier()
    action = _make_action(
        "Recall of spice product",
        "Product is adulterated because it was manufactured under insanitary conditions.",
    )
    types = clf.classify(action)
    assert ViolationType.ADULTERATION in types


def test_classify_contamination():
    clf = ViolationClassifier()
    action = _make_action(
        "Recall of romaine lettuce",
        "Potential salmonella contamination detected in routine testing.",
    )
    types = clf.classify(action)
    assert ViolationType.CONTAMINATION in types


def test_classify_drug_claim():
    clf = ViolationClassifier()
    action = _make_action(
        "Warning letter to supplement company",
        "Products are intended to treat diabetes and cure cancer, making them unapproved new drugs.",
    )
    types = clf.classify(action)
    assert ViolationType.UNAPPROVED_DRUG_CLAIM in types


def test_classify_gmp():
    clf = ViolationClassifier()
    action = _make_action(
        "Warning letter",
        "Violations of 21 CFR 111 current good manufacturing practice for dietary supplements.",
    )
    types = clf.classify(action)
    assert ViolationType.CGMP_DIETARY_SUPPLEMENT in types or ViolationType.GMP_VIOLATION in types


def test_classify_unknown_falls_back_to_other():
    clf = ViolationClassifier()
    action = _make_action(
        "Generic notice",
        "Something happened with a product but no specific violation keywords match.",
    )
    types = clf.classify(action)
    assert types == [ViolationType.OTHER]


def test_classify_multiple_types():
    clf = ViolationClassifier()
    action = _make_action(
        "Recall of dietary supplement",
        "Product is adulterated and misbranded. Contains undeclared milk allergen and salmonella contamination.",
    )
    types = clf.classify(action)
    assert len(types) > 1


def test_classify_batch():
    clf = ViolationClassifier()
    actions = [
        _make_action("Allergen recall", "Undeclared peanut allergen"),
        _make_action("Contamination recall", "Salmonella found in product"),
    ]
    result = clf.classify_batch(actions)
    assert all(len(a.violation_types) > 0 for a in result)
