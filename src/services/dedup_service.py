"""Duplicate detection service for regulatory actions."""

import re
from datetime import datetime

from src.models.enforcement import RegulatoryAction


def _normalize_company(name: str) -> str:
    """Normalize company name for matching."""
    lower = name.lower().strip()
    # Strip common suffixes
    for suffix in [
        ", inc.", " inc.", " inc", ", llc", " llc",
        ", corp.", " corp.", " corp", ", ltd.", " ltd.",
        " ltd", ", co.", " co.", " company", ", l.p.", " l.p.",
    ]:
        if lower.endswith(suffix):
            lower = lower[: -len(suffix)]
    return lower.strip()


def _tokenize(text: str) -> set[str]:
    """Split text into lowercase word tokens."""
    return set(re.findall(r'\b[a-z]{3,}\b', text.lower()))


def _token_overlap(a: set[str], b: set[str]) -> float:
    """Jaccard-like overlap ratio."""
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    smaller = min(len(a), len(b))
    return intersection / smaller if smaller > 0 else 0.0


def _date_within_days(d1: str, d2: str, days: int = 30) -> bool:
    """Check if two ISO date strings are within N days."""
    try:
        dt1 = datetime.strptime(d1[:10], "%Y-%m-%d")
        dt2 = datetime.strptime(d2[:10], "%Y-%m-%d")
        return abs((dt1 - dt2).days) <= days
    except (ValueError, TypeError):
        return False


def find_duplicates(actions: list[RegulatoryAction]) -> list[list[str]]:
    """Find groups of duplicate/related actions.

    Match criteria:
    - Normalized company names match
    - Dates within 30 days
    - Title token overlap > 0.5

    Returns list of groups, where each group is a list of action IDs.
    """
    groups: list[list[str]] = []
    assigned: set[str] = set()

    for i, a in enumerate(actions):
        if a.id in assigned:
            continue

        a_company = _normalize_company(a.company)
        a_tokens = _tokenize(a.title)
        group = [a.id]

        for j in range(i + 1, len(actions)):
            b = actions[j]
            if b.id in assigned:
                continue

            b_company = _normalize_company(b.company)
            if a_company != b_company:
                continue

            if not _date_within_days(a.date, b.date):
                continue

            b_tokens = _tokenize(b.title)
            if _token_overlap(a_tokens, b_tokens) > 0.5:
                group.append(b.id)

        if len(group) > 1:
            for gid in group:
                assigned.add(gid)
            groups.append(group)

    return groups
