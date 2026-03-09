from pydantic import BaseModel

from .enums import ProductCategory, SourceType


class AlertRule(BaseModel):
    """User-defined keyword alert."""

    id: str
    name: str
    keywords: list[str]
    product_categories: list[ProductCategory] | None = None
    sources: list[SourceType] | None = None
    active: bool = True
    created_at: str
    webhook_url: str | None = None


class AlertMatch(BaseModel):
    """A match between an alert rule and a regulatory action."""

    id: str
    alert_rule_id: str
    action_id: str
    matched_keywords: list[str]
    matched_at: str
    read: bool = False
