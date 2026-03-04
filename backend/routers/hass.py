import json
from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from collectors.hass import HassAPI, collect_hass_instance, parse_hass_data
from database import HassInstance, HassSnapshot, decrypt_value, encrypt_value, get_db

router = APIRouter(prefix="/hass")
templates = Jinja2Templates(directory="templates")


# ── List ──────────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def hass_list(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(HassInstance).order_by(HassInstance.name))
    instances = result.scalars().all()

    # Attach latest snapshot to each instance
    rows = []
    for inst in instances:
        snap = (await db.execute(
            select(HassSnapshot)
            .where(HassSnapshot.instance_id == inst.id)
            .order_by(HassSnapshot.timestamp.desc())
            .limit(1)
        )).scalar_one_or_none()

        data = None
        if snap and snap.ok and snap.data_json:
            try:
                data = json.loads(snap.data_json)
            except Exception:
                pass

        rows.append({
            "instance": inst,
            "snap": snap,
            "data": data,
        })

    return templates.TemplateResponse("hass_list.html", {
        "request": request,
        "rows": rows,
        "active_page": "hass",
    })


# ── Detail ────────────────────────────────────────────────────────────────────

@router.get("/{instance_id}", response_class=HTMLResponse)
async def hass_detail(instance_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    instance = await db.get(HassInstance, instance_id)
    if not instance:
        return RedirectResponse(url="/hass", status_code=303)

    error = None
    data = None

    # Use latest successful snapshot
    snap = (await db.execute(
        select(HassSnapshot)
        .where(HassSnapshot.instance_id == instance_id, HassSnapshot.ok == True)
        .order_by(HassSnapshot.timestamp.desc())
        .limit(1)
    )).scalar_one_or_none()

    if snap and snap.data_json:
        try:
            data = json.loads(snap.data_json)
        except Exception:
            pass

    # If no snapshot yet, try live fetch
    if data is None:
        try:
            token = decrypt_value(instance.token_enc)
            api = HassAPI(host=instance.host, token=token, verify_ssl=instance.verify_ssl)
            raw = await api.fetch_all()
            data = parse_hass_data(raw["config"], raw["states"])
        except Exception as exc:
            error = str(exc)

    # Fetch last snapshot regardless of ok for error message display
    last_snap = (await db.execute(
        select(HassSnapshot)
        .where(HassSnapshot.instance_id == instance_id)
        .order_by(HassSnapshot.timestamp.desc())
        .limit(1)
    )).scalar_one_or_none()

    if last_snap and not last_snap.ok and error is None:
        error = last_snap.error

    return templates.TemplateResponse("hass_detail.html", {
        "request": request,
        "instance": instance,
        "data": data,
        "error": error,
        "snap": snap or last_snap,
        "active_page": "hass",
    })


# ── CRUD ──────────────────────────────────────────────────────────────────────

@router.post("/add")
async def add_instance(
    name: str = Form(...),
    host: str = Form(...),
    token: str = Form(...),
    verify_ssl: str = Form("off"),
    db: AsyncSession = Depends(get_db),
):
    db.add(HassInstance(
        name=name.strip(),
        host=host.strip().rstrip("/"),
        token_enc=encrypt_value(token.strip()),
        verify_ssl=(verify_ssl == "on"),
    ))
    await db.commit()
    return RedirectResponse(url="/hass", status_code=303)


@router.post("/{instance_id}/edit")
async def edit_instance(
    instance_id: int,
    name: str = Form(...),
    host: str = Form(...),
    token: str = Form(""),
    verify_ssl: str = Form("off"),
    db: AsyncSession = Depends(get_db),
):
    instance = await db.get(HassInstance, instance_id)
    if instance:
        instance.name = name.strip()
        instance.host = host.strip().rstrip("/")
        instance.verify_ssl = (verify_ssl == "on")
        if token.strip():
            instance.token_enc = encrypt_value(token.strip())
        await db.commit()
    return RedirectResponse(url=f"/hass/{instance_id}", status_code=303)


@router.post("/{instance_id}/delete")
async def delete_instance(instance_id: int, db: AsyncSession = Depends(get_db)):
    instance = await db.get(HassInstance, instance_id)
    if instance:
        await db.delete(instance)
        await db.commit()
    return RedirectResponse(url="/hass", status_code=303)


@router.post("/{instance_id}/refresh")
async def refresh_instance(instance_id: int, db: AsyncSession = Depends(get_db)):
    """Manually trigger a data collection for this instance."""
    await collect_hass_instance(instance_id, db)
    return RedirectResponse(url=f"/hass/{instance_id}", status_code=303)
