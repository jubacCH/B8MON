"""Incidents UI – list, detail, acknowledge, resolve."""
from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from templating import templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db
from models.incident import Incident, IncidentEvent

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

    return templates.TemplateResponse("incident_detail.html", {
        "request": request,
        "incident": incident,
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
