"""Tests for CSV export service."""

import csv
import io

from src.models.enums import ProductCategory, Severity, SourceType, ViolationType
from src.models.enforcement import RegulatoryAction
from src.services.export_service import export_csv


def _make_action():
    return RegulatoryAction(
        id="test-1",
        source=SourceType.OPENFDA_ENFORCEMENT,
        source_id="R-001",
        title="Test Recall",
        description="Test description",
        company="Test Corp",
        product_categories=[ProductCategory.FOOD],
        violation_types=[ViolationType.ADULTERATION],
        severity=Severity.CLASS_I,
        date="2026-01-15",
        url="https://example.com",
        status="Ongoing",
    )


def test_export_csv_headers():
    result = export_csv([_make_action()])
    reader = csv.reader(io.StringIO(result))
    headers = next(reader)
    assert "date" in headers
    assert "company" in headers
    assert "title" in headers
    assert "source" in headers
    assert "severity" in headers


def test_export_csv_data():
    result = export_csv([_make_action()])
    reader = csv.reader(io.StringIO(result))
    next(reader)  # skip headers
    row = next(reader)
    assert "2026-01-15" in row
    assert "Test Corp" in row
    assert "Test Recall" in row


def test_export_csv_empty():
    result = export_csv([])
    lines = result.strip().split("\n")
    assert len(lines) == 1  # Just headers


def test_export_csv_multiple():
    actions = [_make_action(), _make_action()]
    actions[1].id = "test-2"
    actions[1].company = "Other Corp"
    result = export_csv(actions)
    lines = result.strip().split("\n")
    assert len(lines) == 3  # Headers + 2 rows
