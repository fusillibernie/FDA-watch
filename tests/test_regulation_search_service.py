"""Tests for regulation search service."""

from src.models.enums import ProductCategory, RegulationStage, SourceType
from src.models.regulation import RegulationChange
from src.services.regulation_search_service import RegulationSearchService


def _make_change(**overrides) -> RegulationChange:
    defaults = {
        "id": "fr-test-001",
        "source": SourceType.FEDERAL_REGISTER,
        "source_id": "fr-test-001",
        "title": "Test Final Rule on Food Labeling",
        "summary": "Test summary about food labeling requirements.",
        "agency": "FDA",
        "stage": RegulationStage.FINAL_RULE,
        "product_categories": [ProductCategory.FOOD],
        "date_published": "2025-06-15",
    }
    defaults.update(overrides)
    return RegulationChange(**defaults)


def test_add_and_search(tmp_path):
    svc = RegulationSearchService(changes_file=tmp_path / "changes.json")
    c1 = _make_change()
    c2 = _make_change(id="fr-test-002", source_id="fr-test-002", title="Cosmetic Safety Rule", stage=RegulationStage.PROPOSED_RULE, product_categories=[ProductCategory.COSMETIC])

    added = svc.add_changes([c1, c2])
    assert added == 2

    results, total = svc.search()
    assert total == 2


def test_dedup(tmp_path):
    svc = RegulationSearchService(changes_file=tmp_path / "changes.json")
    c1 = _make_change()
    svc.add_changes([c1])
    added = svc.add_changes([c1])  # duplicate
    assert added == 0

    results, total = svc.search()
    assert total == 1


def test_search_by_stage(tmp_path):
    svc = RegulationSearchService(changes_file=tmp_path / "changes.json")
    svc.add_changes([
        _make_change(id="1", source_id="1", stage=RegulationStage.FINAL_RULE),
        _make_change(id="2", source_id="2", stage=RegulationStage.PROPOSED_RULE),
    ])

    results, total = svc.search(stage=RegulationStage.PROPOSED_RULE)
    assert total == 1
    assert results[0].stage == RegulationStage.PROPOSED_RULE


def test_search_by_agency(tmp_path):
    svc = RegulationSearchService(changes_file=tmp_path / "changes.json")
    svc.add_changes([
        _make_change(id="1", source_id="1", agency="FDA"),
        _make_change(id="2", source_id="2", agency="FTC"),
    ])

    results, total = svc.search(agency="FTC")
    assert total == 1
    assert results[0].agency == "FTC"


def test_search_by_category(tmp_path):
    svc = RegulationSearchService(changes_file=tmp_path / "changes.json")
    svc.add_changes([
        _make_change(id="1", source_id="1", product_categories=[ProductCategory.FOOD]),
        _make_change(id="2", source_id="2", product_categories=[ProductCategory.COSMETIC]),
    ])

    results, total = svc.search(category=ProductCategory.COSMETIC)
    assert total == 1


def test_search_text(tmp_path):
    svc = RegulationSearchService(changes_file=tmp_path / "changes.json")
    svc.add_changes([
        _make_change(id="1", source_id="1", title="OTC Monograph Reform"),
        _make_change(id="2", source_id="2", title="Food Contact Material Safety"),
    ])

    results, total = svc.search(q="monograph")
    assert total == 1
    assert "Monograph" in results[0].title


def test_get_change(tmp_path):
    svc = RegulationSearchService(changes_file=tmp_path / "changes.json")
    c = _make_change()
    svc.add_changes([c])

    found = svc.get_change("fr-test-001")
    assert found is not None
    assert found.title == c.title

    assert svc.get_change("nonexistent") is None


def test_stats(tmp_path):
    svc = RegulationSearchService(changes_file=tmp_path / "changes.json")
    svc.add_changes([
        _make_change(id="1", source_id="1", agency="FDA", stage=RegulationStage.FINAL_RULE, date_published="2025-06-15"),
        _make_change(id="2", source_id="2", agency="FTC", stage=RegulationStage.PROPOSED_RULE, date_published="2025-06-10", date_comments_close="2099-12-31"),
    ])

    stats = svc.stats()
    assert stats["total_changes"] == 2
    assert stats["open_comment_periods"] == 1
    assert "FDA" in stats["by_agency"]
    assert "FTC" in stats["by_agency"]


def test_persistence(tmp_path):
    file = tmp_path / "changes.json"
    svc = RegulationSearchService(changes_file=file)
    svc.add_changes([_make_change()])

    svc2 = RegulationSearchService(changes_file=file)
    results, total = svc2.search()
    assert total == 1
