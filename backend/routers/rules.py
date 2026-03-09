"""Router for custom alert rules – CRUD + field discovery API."""
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from models.alert_rule import AlertRule
from models.base import get_db
from services import rules as rules_svc

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Page ─────────────────────────────────────────────────────────────────────

@router.get("/rules")
async def rules_page(request: Request, db: AsyncSession = Depends(get_db)):
    from main import templates
    all_rules = await rules_svc.get_all_rules(db)
    sources = await rules_svc.get_source_options(db)
    operators = [
        {"key": k, "label": v[0]}
        for k, v in rules_svc.OPERATORS.items()
    ]
    return templates.TemplateResponse("rules.html", {
        "request": request,
        "rules": all_rules,
        "sources": sources,
        "operators": operators,
        "active_page": "rules",
    })


# ── CRUD ─────────────────────────────────────────────────────────────────────

@router.post("/rules/add")
async def add_rule(request: Request, db: AsyncSession = Depends(get_db)):
    form = await request.form()
    rule = AlertRule(
        name=str(form.get("name", "")).strip() or "Unnamed Rule",
        source_type=str(form.get("source_type", "")),
        source_id=int(form["source_id"]) if form.get("source_id") else None,
        field_path=str(form.get("field_path", "")),
        operator=str(form.get("operator", "gt")),
        threshold=str(form.get("threshold", "")) or None,
        severity=str(form.get("severity", "warning")),
        message_template=str(form.get("message_template", "")).strip() or None,
        cooldown_minutes=int(form.get("cooldown_minutes", 5)),
        enabled=True,
    )
    db.add(rule)
    await db.commit()
    return RedirectResponse(url="/rules?saved=1", status_code=303)


@router.post("/rules/{rule_id}/toggle")
async def toggle_rule(rule_id: int, db: AsyncSession = Depends(get_db)):
    rule = await rules_svc.get_rule(db, rule_id)
    if not rule:
        return JSONResponse({"error": "Not found"}, status_code=404)
    rule.enabled = not rule.enabled
    await db.commit()
    return RedirectResponse(url="/rules", status_code=303)


@router.post("/rules/{rule_id}/delete")
async def delete_rule(rule_id: int, db: AsyncSession = Depends(get_db)):
    await rules_svc.delete_rule(db, rule_id)
    return RedirectResponse(url="/rules", status_code=303)


@router.post("/rules/{rule_id}/edit")
async def edit_rule(request: Request, rule_id: int, db: AsyncSession = Depends(get_db)):
    rule = await rules_svc.get_rule(db, rule_id)
    if not rule:
        return RedirectResponse(url="/rules", status_code=303)

    form = await request.form()
    rule.name = str(form.get("name", "")).strip() or rule.name
    rule.source_type = str(form.get("source_type", "")) or rule.source_type
    rule.source_id = int(form["source_id"]) if form.get("source_id") else None
    rule.field_path = str(form.get("field_path", "")) or rule.field_path
    rule.operator = str(form.get("operator", "")) or rule.operator
    rule.threshold = str(form.get("threshold", "")) or None
    rule.severity = str(form.get("severity", "")) or rule.severity
    rule.message_template = str(form.get("message_template", "")).strip() or None
    rule.cooldown_minutes = int(form.get("cooldown_minutes", 5))
    await db.commit()
    return RedirectResponse(url="/rules?saved=1", status_code=303)


# ── Field Discovery API ─────────────────────────────────────────────────────

@router.get("/api/rules/fields")
async def get_fields(
    source_type: str,
    source_id: int | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Return available fields for a given source type/instance."""
    fields = await rules_svc.get_fields_for_source(db, source_type, source_id)
    return JSONResponse(fields)


@router.get("/api/rules/sources")
async def get_sources(db: AsyncSession = Depends(get_db)):
    """Return available source types and instances."""
    sources = await rules_svc.get_source_options(db)
    return JSONResponse(sources)
