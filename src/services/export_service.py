"""CSV export service for regulatory actions."""

import csv
import io

from src.models.enforcement import RegulatoryAction


def export_csv(actions: list[RegulatoryAction]) -> str:
    """Export actions to CSV string.

    Columns: date, company, title, source, severity, violation_types,
             categories, url, status
    """
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "date", "company", "title", "source", "severity",
        "violation_types", "categories", "url", "status",
    ])
    for a in actions:
        writer.writerow([
            a.date,
            a.company,
            a.title,
            a.source.value,
            a.severity.value,
            ", ".join(v.value for v in a.violation_types),
            ", ".join(c.value for c in a.product_categories),
            a.url or "",
            a.status or "",
        ])
    return output.getvalue()
