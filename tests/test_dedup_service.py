"""Tests for duplicate detection service."""

from src.models.enums import ProductCategory, Severity, SourceType, ViolationType
from src.models.enforcement import RegulatoryAction
from src.services.dedup_service import (
    find_duplicates,
    _normalize_company,
    _token_overlap,
    _date_within_days,
)


def _make_action(action_id, company, title, date="2026-01-15"):
    return RegulatoryAction(
        id=action_id,
        source=SourceType.OPENFDA_ENFORCEMENT,
        source_id=action_id,
        title=title,
        description="desc",
        company=company,
        product_categories=[ProductCategory.FOOD],
        violation_types=[ViolationType.ADULTERATION],
        severity=Severity.CLASS_I,
        date=date,
    )


def test_normalize_company():
    assert _normalize_company("Acme Foods, Inc.") == "acme foods"
    assert _normalize_company("Test LLC") == "test"
    assert _normalize_company("FoodCo, Ltd.") == "foodco"


def test_date_within_days():
    assert _date_within_days("2026-01-15", "2026-01-20", 30)
    assert not _date_within_days("2026-01-15", "2026-03-15", 30)


def test_token_overlap():
    a = {"recall", "food", "product", "salmonella"}
    b = {"recall", "food", "contamination", "salmonella"}
    assert _token_overlap(a, b) > 0.5


def test_find_duplicates_match():
    actions = [
        _make_action("a1", "Acme Inc.", "Recall of contaminated food product", "2026-01-15"),
        _make_action("a2", "Acme, Inc.", "Recall of contaminated food product batch 2", "2026-01-20"),
    ]
    groups = find_duplicates(actions)
    assert len(groups) == 1
    assert set(groups[0]) == {"a1", "a2"}


def test_find_duplicates_no_match_different_company():
    actions = [
        _make_action("a1", "Acme Inc.", "Recall of food product", "2026-01-15"),
        _make_action("a2", "Other Corp", "Recall of food product", "2026-01-15"),
    ]
    groups = find_duplicates(actions)
    assert len(groups) == 0


def test_find_duplicates_no_match_date_too_far():
    actions = [
        _make_action("a1", "Acme Inc.", "Recall of food product", "2026-01-15"),
        _make_action("a2", "Acme Inc.", "Recall of food product", "2026-06-15"),
    ]
    groups = find_duplicates(actions)
    assert len(groups) == 0


def test_find_duplicates_empty():
    assert find_duplicates([]) == []
