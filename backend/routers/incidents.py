"""Incidents UI – list, detail, acknowledge, resolve."""
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from templating import templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db
from models.incident import Incident, IncidentEvent
from models.log_template import LogTemplate, PrecursorPattern

router = APIRouter(prefix="/incidents")


@router.get("")
async def incidents_list(status: str = None):
    """Redirect to unified alerts page with incidents tab."""
    url = "/alerts?tab=incidents"
    if status:
        url += f"&status={status}"
    return RedirectResponse(url, status_code=302)


@router.get("/{incident_id}", response_class=HTMLResponse)
async def incident_detail(
    incident_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    incident = (await db.execute(
        select(Incident)
        .options(selectinload(Incident.events))
        .where(Incident.id == incident_id)
    )).scalar_one_or_none()

    if not incident:
        return RedirectResponse("/alerts?tab=incidents", status_code=302)

    # Fetch related syslog entries around the incident timeframe
    related_logs = []
    try:
        from services.clickhouse_client import query as ch_query
        start = incident.created_at - timedelta(minutes=5)
        end = (incident.resolved_at or datetime.utcnow()) + timedelta(minutes=5)
        related_logs = await ch_query(
            "SELECT timestamp, hostname, severity, app_name, message "
            "FROM syslog_messages "
            "WHERE timestamp >= {t0:DateTime64(3)} AND timestamp <= {t1:DateTime64(3)} "
            "AND severity <= 3 "
            "ORDER BY timestamp DESC LIMIT 50",
            {"t0": start, "t1": end},
        )
    except Exception:
        logging.getLogger(__name__).debug("Could not fetch syslog context", exc_info=True)

    # Fetch root-cause suggestions from learned precursor patterns
    precursors = []
    try:
        _EVENT_MAP = {
            "host_down_syslog": "host_down",
            "multi_host_down": "host_down",
            "integration_host": "integration_fail",
        }
        event_type = _EVENT_MAP.get(incident.rule, "incident")
        rows = (await db.execute(
            select(PrecursorPattern, LogTemplate)
            .join(LogTemplate, PrecursorPattern.template_id == LogTemplate.id)
            .where(
                PrecursorPattern.precedes_event == event_type,
                PrecursorPattern.confidence >= 0.3,
                PrecursorPattern.occurrence_count >= 2,
            )
            .order_by(PrecursorPattern.confidence.desc())
            .limit(8)
        )).all()
        for pp, lt in rows:
            precursors.append({
                "template": lt.template,
                "example": lt.example,
                "confidence": round(pp.confidence * 100),
                "lead_time_min": round(pp.avg_lead_time_sec / 60, 1) if pp.avg_lead_time_sec else None,
                "occurrences": pp.occurrence_count,
            })
    except Exception:
        logging.getLogger(__name__).debug("Could not fetch precursors", exc_info=True)

    return templates.TemplateResponse("incident_detail.html", {
        "request": request,
        "incident": incident,
        "related_logs": related_logs,
        "precursors": precursors,
        "active_page": "alerts",
    })


@router.post("/{incident_id}/acknowledge")
async def acknowledge_incident(
    incident_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    incident = (await db.execute(
        select(Incident).where(Incident.id == incident_id)
    )).scalar_one_or_none()

    if incident and incident.status == "open":
        user = getattr(request.state, "current_user", None)
        username = user.username if user else "unknown"
        incident.status = "acknowledged"
        incident.acknowledged_by = username
        incident.updated_at = datetime.utcnow()
        db.add(IncidentEvent(
            incident_id=incident.id,
            event_type="acknowledged",
            summary=f"Acknowledged by {username}",
        ))
        await db.commit()

    return RedirectResponse(f"/incidents/{incident_id}", status_code=302)


@router.post("/{incident_id}/resolve")
async def resolve_incident(
    incident_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    incident = (await db.execute(
        select(Incident).where(Incident.id == incident_id)
    )).scalar_one_or_none()

    if incident and incident.status in ("open", "acknowledged"):
        user = getattr(request.state, "current_user", None)
        username = user.username if user else "unknown"
        incident.status = "resolved"
        incident.resolved_at = datetime.utcnow()
        incident.updated_at = datetime.utcnow()
        db.add(IncidentEvent(
            incident_id=incident.id,
            event_type="resolved",
            summary=f"Manually resolved by {username}",
        ))
        await db.commit()

    return RedirectResponse(f"/incidents/{incident_id}", status_code=302)
