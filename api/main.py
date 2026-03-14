"""FDA-watch API — FDA & Advertising Compliance Monitor."""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from src.models.enums import ProductCategory, Severity, SourceType, ViolationType
from src.services.alert_service import AlertService
from src.services.classifier import ViolationClassifier
from src.services.ingestion_service import IngestionService
from src.services.scheduler_service import SchedulerService
from src.services.auth import require_auth
from src.services.export_service import export_csv
from src.services.search_service import SearchService

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

limiter = Limiter(key_func=get_remote_address)

app = FastAPI(
    title="FDA-watch",
    description="FDA & advertising compliance monitor",
    version="0.1.0",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get(
        "CORS_ORIGINS", "http://localhost:8000,http://localhost:8001,http://localhost:8002,http://localhost:8003,http://localhost:8004,http://127.0.0.1:8000,http://127.0.0.1:8004"
    ).split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Services (singletons)
# ---------------------------------------------------------------------------

search_service = SearchService()
alert_service = AlertService()
classifier = ViolationClassifier()
ingestion_service = IngestionService(
    search_service=search_service,
    alert_service=alert_service,
    classifier=classifier,
    api_key=os.environ.get("OPENFDA_API_KEY"),
)
scheduler_service = SchedulerService(
    ingest_callback=lambda: ingestion_service.ingest_all(),
)


# ---------------------------------------------------------------------------
# Lifespan (scheduler start/stop)
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler_service.start()
    yield
    scheduler_service.stop()


app.router.lifespan_context = lifespan

# ---------------------------------------------------------------------------
# Static files / UI
# ---------------------------------------------------------------------------

UI_DIR = Path(__file__).parent.parent / "ui"
if UI_DIR.exists():
    app.mount("/ui", StaticFiles(directory=str(UI_DIR), html=True), name="ui")


@app.get("/", response_class=HTMLResponse)
async def root():
    index = UI_DIR / "index.html"
    if index.exists():
        return HTMLResponse(index.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>FDA-watch</h1><p>UI not found. Visit /docs for API.</p>")


# ---------------------------------------------------------------------------
# Ingestion endpoints
# ---------------------------------------------------------------------------


@app.post("/api/ingest", dependencies=[Depends(require_auth)])
@limiter.limit("5/minute")
async def trigger_ingest(
    request: Request,
    source: str | None = Query(None, description="Specific source: openfda, warning_letters, ftc, classaction, cpsc, prop65, nad, state_ag"),
):
    """Trigger data ingestion from FDA sources."""
    summary = await ingestion_service.ingest_all(source=source)
    return summary


@app.get("/api/ingest/status")
async def ingest_status():
    """Get last sync timestamps and record counts."""
    return ingestion_service.get_status()


# ---------------------------------------------------------------------------
# Browse & Search
# ---------------------------------------------------------------------------


@app.get("/api/actions/stats")
async def action_stats():
    """Aggregated statistics for the dashboard."""
    return search_service.stats()


@app.get("/api/actions")
async def list_actions(
    q: str | None = Query(None),
    category: ProductCategory | None = Query(None),
    violation_type: ViolationType | None = Query(None),
    severity: Severity | None = Query(None),
    source: SourceType | None = Query(None),
    company: str | None = Query(None),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """List regulatory actions with filtering and search."""
    results, total = search_service.search(
        q=q,
        category=category,
        violation_type=violation_type,
        severity=severity,
        source=source,
        company=company,
        date_from=date_from,
        date_to=date_to,
        offset=offset,
        limit=limit,
    )
    return {
        "results": [a.model_dump() for a in results],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


@app.get("/api/actions/export")
async def export_actions(
    q: str | None = Query(None),
    category: ProductCategory | None = Query(None),
    violation_type: ViolationType | None = Query(None),
    severity: Severity | None = Query(None),
    source: SourceType | None = Query(None),
    company: str | None = Query(None),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
):
    """Export filtered actions as CSV."""
    results, _ = search_service.search(
        q=q, category=category, violation_type=violation_type,
        severity=severity, source=source, company=company,
        date_from=date_from, date_to=date_to,
        offset=0, limit=10000,
    )
    csv_content = export_csv(results)
    return StreamingResponse(
        iter([csv_content]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=fda_watch_export.csv"},
    )


@app.get("/api/actions/trends")
async def action_trends(months: int = Query(6, ge=1, le=24)):
    """Trend analysis with month-over-month changes."""
    return search_service.trends(months=months)


@app.get("/api/actions/{action_id}")
async def get_action(action_id: str):
    """Get a single regulatory action by ID."""
    action = search_service.get_action(action_id)
    if not action:
        raise HTTPException(404, "Action not found")
    return action.model_dump()


@app.get("/api/actions/{action_id}/related")
async def get_related_actions(action_id: str):
    """Find duplicate/related actions."""
    related = search_service.get_related(action_id)
    return [a.model_dump() for a in related]


# ---------------------------------------------------------------------------
# Companies
# ---------------------------------------------------------------------------


@app.get("/api/companies")
async def list_companies(
    q: str | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """List companies with action counts."""
    companies, total = search_service.list_companies(q=q, offset=offset, limit=limit)
    return {"results": companies, "total": total, "offset": offset, "limit": limit}


@app.get("/api/companies/{name}/profile")
async def company_profile(name: str):
    """Get full company profile."""
    profile = search_service.company_profile(name)
    if profile["total_actions"] == 0:
        raise HTTPException(404, "Company not found")
    return profile


# ---------------------------------------------------------------------------
# Warning Letters
# ---------------------------------------------------------------------------


@app.get("/api/warning-letters")
async def list_warning_letters(
    q: str | None = Query(None),
    category: ProductCategory | None = Query(None),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """Browse warning letters (subset of actions with source=fda_warning_letter)."""
    results, total = search_service.search(
        q=q,
        category=category,
        source=SourceType.FDA_WARNING_LETTER,
        date_from=date_from,
        date_to=date_to,
        offset=offset,
        limit=limit,
    )
    return {
        "results": [a.model_dump() for a in results],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


@app.get("/api/warning-letters/{letter_id}")
async def get_warning_letter(letter_id: str):
    action = search_service.get_action(letter_id)
    if not action or action.source != SourceType.FDA_WARNING_LETTER:
        raise HTTPException(404, "Warning letter not found")
    return action.model_dump()


# ---------------------------------------------------------------------------
# Alert Rules
# ---------------------------------------------------------------------------


class AlertRuleCreate(BaseModel):
    name: str
    keywords: list[str]
    product_categories: list[ProductCategory] | None = None
    sources: list[SourceType] | None = None
    webhook_url: str | None = None


class AlertRuleUpdate(BaseModel):
    name: str | None = None
    keywords: list[str] | None = None
    product_categories: list[ProductCategory] | None = None
    sources: list[SourceType] | None = None
    active: bool | None = None
    webhook_url: str | None = None


@app.get("/api/alerts/rules")
async def list_alert_rules():
    return [r.model_dump() for r in alert_service.list_rules()]


@app.post("/api/alerts/rules", status_code=201, dependencies=[Depends(require_auth)])
async def create_alert_rule(body: AlertRuleCreate):
    rule = alert_service.create_rule(
        name=body.name,
        keywords=body.keywords,
        product_categories=body.product_categories,
        sources=body.sources,
        webhook_url=body.webhook_url,
    )
    return rule.model_dump()


@app.put("/api/alerts/rules/{rule_id}", dependencies=[Depends(require_auth)])
async def update_alert_rule(rule_id: str, body: AlertRuleUpdate):
    updates = body.model_dump(exclude_none=True)
    rule = alert_service.update_rule(rule_id, updates)
    if not rule:
        raise HTTPException(404, "Alert rule not found")
    return rule.model_dump()


@app.delete("/api/alerts/rules/{rule_id}", dependencies=[Depends(require_auth)])
async def delete_alert_rule(rule_id: str):
    if not alert_service.delete_rule(rule_id):
        raise HTTPException(404, "Alert rule not found")
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Alert Matches
# ---------------------------------------------------------------------------


@app.get("/api/alerts/matches")
async def list_alert_matches(unread_only: bool = Query(False)):
    return [m.model_dump() for m in alert_service.list_matches(unread_only=unread_only)]


@app.put("/api/alerts/matches/{match_id}/read")
async def mark_match_read(match_id: str):
    if not alert_service.mark_read(match_id):
        raise HTTPException(404, "Match not found")
    return {"read": True}


@app.get("/api/alerts/matches/unread-count")
async def unread_match_count():
    return {"unread": alert_service.unread_count()}


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


class SchedulerConfig(BaseModel):
    interval_hours: int | None = None
    enabled: bool | None = None


@app.get("/api/scheduler/status")
async def scheduler_status():
    return scheduler_service.get_status()


@app.put("/api/scheduler/config", dependencies=[Depends(require_auth)])
async def update_scheduler(body: SchedulerConfig):
    if body.interval_hours is not None:
        scheduler_service.set_interval(body.interval_hours)
    if body.enabled is not None:
        scheduler_service.set_enabled(body.enabled)
    return scheduler_service.get_status()


# ---------------------------------------------------------------------------
# Reference
# ---------------------------------------------------------------------------


@app.get("/api/reference/violation-types")
async def list_violation_types():
    return [{"value": v.value, "label": v.name.replace("_", " ").title()} for v in ViolationType]


@app.get("/api/reference/product-categories")
async def list_product_categories():
    return [{"value": c.value, "label": c.name.replace("_", " ").title()} for c in ProductCategory]


# ---------------------------------------------------------------------------
# Litigation (Phase 2 — NAD, FTC, Class Action)
# ---------------------------------------------------------------------------

LITIGATION_SOURCES = {
    SourceType.FTC_ACTION,
    SourceType.CLASS_ACTION,
    SourceType.NAD_DECISION,
    SourceType.STATE_AG,
}


@app.get("/api/litigation")
async def list_litigation(
    q: str | None = Query(None),
    source: SourceType | None = Query(None),
    category: ProductCategory | None = Query(None),
    violation_type: ViolationType | None = Query(None),
    company: str | None = Query(None),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """List litigation actions (NAD, FTC, Class Action) with filtering."""
    # If a specific litigation source is given, use it; otherwise search all litigation sources
    if source and source in LITIGATION_SOURCES:
        results, total = search_service.search(
            q=q, source=source, category=category, violation_type=violation_type,
            company=company, date_from=date_from, date_to=date_to,
            offset=offset, limit=limit,
        )
    else:
        # Combine results from all litigation sources
        all_results = []
        for src in LITIGATION_SOURCES:
            src_results, _ = search_service.search(
                q=q, source=src, category=category, violation_type=violation_type,
                company=company, date_from=date_from, date_to=date_to,
                offset=0, limit=10000,
            )
            all_results.extend(src_results)
        # Sort by date desc
        all_results.sort(key=lambda a: a.date, reverse=True)
        total = len(all_results)
        results = all_results[offset : offset + limit]

    return {
        "results": [a.model_dump() for a in results],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


@app.get("/api/litigation/{case_id}")
async def get_litigation_case(case_id: str):
    """Get a single litigation case by ID."""
    action = search_service.get_action(case_id)
    if not action or action.source not in LITIGATION_SOURCES:
        raise HTTPException(404, "Litigation case not found")
    return action.model_dump()


@app.get("/api/reference/litigation-sources")
async def list_litigation_sources():
    """Return available litigation source types."""
    return [
        {"value": s.value, "label": s.name.replace("_", " ").title()}
        for s in LITIGATION_SOURCES
    ]
