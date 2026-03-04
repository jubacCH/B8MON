"""NUT / UPS router – manage UPS instances monitored via Network UPS Tools."""
from __future__ import annotations
import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from collectors.nut import NutClient
from database import NutInstance, NutSnapshot, decrypt_value, encrypt_value, get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ups")
templates = Jinja2Templates(directory="templates")


# ── List ──────────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def nut_list(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(NutInstance).order_by(NutInstance.name))
    instances = result.scalars().all()

    # Attach latest snapshot data to each instance
    summaries = []
    for inst in instances:
        snap_result = await db.execute(
            select(NutSnapshot)
            .where(NutSnapshot.instance_id == inst.id)
            .order_by(NutSnapshot.timestamp.desc())
            .limit(1)
        )
        snap = snap_result.scalar_one_or_none()
        data = None
        if snap and snap.ok and snap.data_json:
            try:
                data = json.loads(snap.data_json)
            except Exception:
                pass
        summaries.append({
            "instance": inst,
            "snap":     snap,
            "data":     data,
        })

    return templates.TemplateResponse("nut_list.html", {
        "request":    request,
        "summaries":  summaries,
        "active_page": "ups",
    })


# ── Detail ────────────────────────────────────────────────────────────────────

@router.get("/{instance_id}", response_class=HTMLResponse)
async def nut_detail(instance_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    instance = await db.get(NutInstance, instance_id)
    if not instance:
        return RedirectResponse(url="/ups", status_code=303)

    snap_result = await db.execute(
        select(NutSnapshot)
        .where(NutSnapshot.instance_id == instance_id)
        .order_by(NutSnapshot.timestamp.desc())
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

    return templates.TemplateResponse("nut_detail.html", {
        "request":     request,
        "instance":    instance,
        "snap":        snap,
        "data":        data,
        "error":       error,
        "active_page": "ups",
        "saved":       request.query_params.get("saved"),
    })


# ── Add ───────────────────────────────────────────────────────────────────────

@router.post("/add")
async def nut_add(
    name:     str = Form(...),
    host:     str = Form(...),
    port:     str = Form("3493"),
    ups_name: str = Form("ups"),
    username: str = Form(""),
    password: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    try:
        port_int = int(port)
    except ValueError:
        port_int = 3493

    inst = NutInstance(
        name         = name.strip(),
        host         = host.strip(),
        port         = port_int,
        ups_name     = ups_name.strip() or "ups",
        username     = username.strip() or None,
        password_enc = encrypt_value(password) if password.strip() else None,
    )
    db.add(inst)
    await db.commit()
    await db.refresh(inst)

    # Try an immediate poll
    await _poll_instance(inst, db)

    return RedirectResponse(url=f"/ups/{inst.id}", status_code=303)


# ── Edit ──────────────────────────────────────────────────────────────────────

@router.post("/{instance_id}/edit")
async def nut_edit(
    instance_id: int,
    name:     str = Form(...),
    host:     str = Form(...),
    port:     str = Form("3493"),
    ups_name: str = Form("ups"),
    username: str = Form(""),
    password: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    inst = await db.get(NutInstance, instance_id)
    if not inst:
        return RedirectResponse(url="/ups", status_code=303)

    try:
        port_int = int(port)
    except ValueError:
        port_int = 3493

    inst.name     = name.strip()
    inst.host     = host.strip()
    inst.port     = port_int
    inst.ups_name = ups_name.strip() or "ups"
    inst.username = username.strip() or None
    if password.strip():
        inst.password_enc = encrypt_value(password.strip())
    await db.commit()

    return RedirectResponse(url=f"/ups/{instance_id}?saved=1", status_code=303)


# ── Delete ────────────────────────────────────────────────────────────────────

@router.post("/{instance_id}/delete")
async def nut_delete(instance_id: int, db: AsyncSession = Depends(get_db)):
    inst = await db.get(NutInstance, instance_id)
    if inst:
        await db.delete(inst)
        await db.commit()
    return RedirectResponse(url="/ups", status_code=303)


# ── Manual refresh ────────────────────────────────────────────────────────────

@router.post("/{instance_id}/refresh")
async def nut_refresh(instance_id: int, db: AsyncSession = Depends(get_db)):
    inst = await db.get(NutInstance, instance_id)
    if inst:
        await _poll_instance(inst, db)
    return RedirectResponse(url=f"/ups/{instance_id}", status_code=303)


# ── Internal helpers ──────────────────────────────────────────────────────────

async def _poll_instance(inst: NutInstance, db: AsyncSession) -> None:
    """Fetch NUT data for a single instance and store a snapshot."""
    try:
        password = decrypt_value(inst.password_enc) if inst.password_enc else None
        client = NutClient(
            host     = inst.host,
            port     = inst.port,
            ups_name = inst.ups_name,
            username = inst.username,
            password = password,
        )
        data = await client.fetch_all()
        db.add(NutSnapshot(
            instance_id = inst.id,
            timestamp   = datetime.utcnow(),
            ok          = True,
            data_json   = json.dumps(data),
            error       = None,
        ))
    except Exception as exc:
        logger.warning("NUT poll failed for %s: %s", inst.name, exc)
        db.add(NutSnapshot(
            instance_id = inst.id,
            timestamp   = datetime.utcnow(),
            ok          = False,
            data_json   = None,
            error       = str(exc),
        ))
    await db.commit()
