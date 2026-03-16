from pydantic import BaseModel

from .enums import ProductCategory, RegulationStage, SourceType


class RegulationChange(BaseModel):
    """A regulatory change: new rule, amendment, or guidance document."""

    id: str
    source: SourceType
    source_id: str
    title: str
    summary: str
    agency: str
    stage: RegulationStage
    product_categories: list[ProductCategory]
    date_published: str
    date_effective: str | None = None
    date_comments_close: str | None = None
    jurisdiction: str = "US"
    url: str | None = None
    cfr_references: list[str] | None = None
    raw_data: dict | None = None
