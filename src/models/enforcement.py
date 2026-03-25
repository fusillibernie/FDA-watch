from pydantic import BaseModel

from .enums import ProductCategory, ViolationType, Severity, SourceType


class RegulatoryAction(BaseModel):
    """Unified model for all regulatory actions regardless of source."""

    id: str
    source: SourceType
    source_id: str
    title: str
    description: str
    company: str
    product_categories: list[ProductCategory]
    violation_types: list[ViolationType]
    severity: Severity
    date: str
    jurisdiction: str = "US"
    url: str | None = None
    status: str | None = None
    distribution: str | None = None
    raw_data: dict | None = None
    soi_metadata: dict | None = None  # {food_subcategory, cfr_references, matched_products}


class WarningLetterMeta(BaseModel):
    """FDA warning letter metadata."""

    letter_id: str
    company: str
    subject: str
    issue_date: str
    product_type: str
    close_out_date: str | None = None
    url: str | None = None


