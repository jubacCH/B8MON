"""Redfish / iDRAC router – manage server hardware monitors."""
from __future__ import annotations
import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from collectors.redfish import RedfishAPI
from database import RedfishServer, RedfishSnapshot, decrypt_value, encrypt_value, get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/redfish")
templates = Jinja2Templates(directory="templates")


# ── List ──────────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def redfish_list(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(RedfishServer).order_by(RedfishServer.name))
    servers = result.scalars().all()

    summaries = []
    for srv in servers:
        snap_result = await db.execute(
            select(RedfishSnapshot)
            .where(RedfishSnapshot.server_id == srv.id)
            .order_by(RedfishSnapshot.timestamp.desc())
            .limit(1)
        )
        snap = snap_result.scalar_one_or_none()
        data = None
        if snap and snap.ok and snap.data_json:
            try:
                data = json.loads(snap.data_json)
            except Exception:
                pass
        # Find hottest temperature reading
        hottest = None
        if data and data.get("temperatures"):
            temps = [t["reading_c"] for t in data["temperatures"] if t.get("reading_c") is not None]
            hottest = max(temps) if temps else None
        summaries.append({
            "server":  srv,
            "snap":    snap,
            "data":    data,
            "hottest": hottest,
        })

    return templates.TemplateResponse("redfish_list.html", {
        "request":    request,
        "summaries":  summaries,
        "active_page": "redfish",
    })


# ── Detail ────────────────────────────────────────────────────────────────────

@router.get("/{server_id}", response_class=HTMLResponse)
async def redfish_detail(server_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    server = await db.get(RedfishServer, server_id)
    if not server:
        return RedirectResponse(url="/redfish", status_code=303)

    snap_result = await db.execute(
        select(RedfishSnapshot)
        .where(RedfishSnapshot.server_id == server_id)
        .order_by(RedfishSnapshot.timestamp.desc())
        .limit(1)
    )
    snap = snap_result.scalar_one_or_none()

    data  = None
    error = None
    if snap:
        if snap.ok and snap.data_json:
            try:
                data = json.loads(snap.data_json)
            except Exception:
                error = "Failed to parse snapshot data"
        else:
            error = snap.error

    return templates.TemplateResponse("redfish_detail.html", {
        "request":     request,
        "server":      server,
        "snap":        snap,
        "data":        data,
        "error":       error,
        "active_page": "redfish",
        "saved":       request.query_params.get("saved"),
    })


# ── Add ───────────────────────────────────────────────────────────────────────

@router.post("/add")
async def redfish_add(
    name:       str = Form(...),
    host:       str = Form(...),
    username:   str = Form(...),
    password:   str = Form(...),
    verify_ssl: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    srv = RedfishServer(
        name         = name.strip(),
        host         = host.strip().rstrip("/"),
        username     = username.strip(),
        password_enc = encrypt_value(password),
        verify_ssl   = verify_ssl == "on",
    )
    db.add(srv)
    await db.commit()
    await db.refresh(srv)

    # Try an immediate poll
    await _poll_server(srv, db)

    return RedirectResponse(url=f"/redfish/{srv.id}", status_code=303)


# ── Edit ──────────────────────────────────────────────────────────────────────

@router.post("/{server_id}/edit")
async def redfish_edit(
    server_id:  int,
    name:       str = Form(...),
    host:       str = Form(...),
    username:   str = Form(...),
    password:   str = Form(""),
    verify_ssl: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    srv = await db.get(RedfishServer, server_id)
    if not srv:
        return RedirectResponse(url="/redfish", status_code=303)

    srv.name       = name.strip()
    srv.host       = host.strip().rstrip("/")
    srv.username   = username.strip()
    srv.verify_ssl = verify_ssl == "on"
    if password.strip():
        srv.password_enc = encrypt_value(password.strip())
    await db.commit()

    return RedirectResponse(url=f"/redfish/{server_id}?saved=1", status_code=303)


# ── Delete ────────────────────────────────────────────────────────────────────

@router.post("/{server_id}/delete")
async def redfish_delete(server_id: int, db: AsyncSession = Depends(get_db)):
    srv = await db.get(RedfishServer, server_id)
    if srv:
        await db.delete(srv)
        await db.commit()
    return RedirectResponse(url="/redfish", status_code=303)


# ── Manual refresh ────────────────────────────────────────────────────────────

@router.post("/{server_id}/refresh")
async def redfish_refresh(server_id: int, db: AsyncSession = Depends(get_db)):
    srv = await db.get(RedfishServer, server_id)
    if srv:
        await _poll_server(srv, db)
    return RedirectResponse(url=f"/redfish/{server_id}", status_code=303)


# ── Internal helpers ──────────────────────────────────────────────────────────

async def _poll_server(srv: RedfishServer, db: AsyncSession) -> None:
    """Fetch Redfish data for a single server and store a snapshot."""
    try:
        api = RedfishAPI(
            host       = srv.host,
            username   = srv.username,
            password   = decrypt_value(srv.password_enc),
            verify_ssl = srv.verify_ssl,
        )
        data = await api.fetch_all()
        db.add(RedfishSnapshot(
            server_id = srv.id,
            timestamp = datetime.utcnow(),
            ok        = True,
            data_json = json.dumps(data),
            error     = None,
        ))
    except Exception as exc:
        logger.warning("Redfish poll failed for %s: %s", srv.name, exc)
        db.add(RedfishSnapshot(
            server_id = srv.id,
            timestamp = datetime.utcnow(),
            ok        = False,
            data_json = None,
            error     = str(exc),
        ))
    await db.commit()
