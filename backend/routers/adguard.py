import json

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from collectors.adguard import AdguardAPI
from database import AdguardInstance, AdguardSnapshot, decrypt_value, encrypt_value, get_db

router = APIRouter(prefix="/adguard")
templates = Jinja2Templates(directory="templates")


# ── List ──────────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def adguard_list(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AdguardInstance).order_by(AdguardInstance.name))
    instances = result.scalars().all()

    instance_data = []
    for inst in instances:
        snap = (await db.execute(
            select(AdguardSnapshot)
            .where(AdguardSnapshot.instance_id == inst.id)
            .order_by(AdguardSnapshot.timestamp.desc())
            .limit(1)
        )).scalar_one_or_none()

        data = json.loads(snap.data_json) if snap and snap.data_json else None
        instance_data.append({"instance": inst, "snap": snap, "data": data})

    return templates.TemplateResponse("adguard_list.html", {
        "request":       request,
        "instance_data": instance_data,
        "active_page":   "adguard",
    })


# ── Detail ────────────────────────────────────────────────────────────────────

@router.get("/{instance_id}", response_class=HTMLResponse)
async def adguard_detail(instance_id: int, request: Request,
                         db: AsyncSession = Depends(get_db)):
    instance = await db.get(AdguardInstance, instance_id)
    if not instance:
        return RedirectResponse(url="/adguard")

    error = None
    data  = None

    snap = (await db.execute(
        select(AdguardSnapshot)
        .where(AdguardSnapshot.instance_id == instance_id, AdguardSnapshot.ok == True)
        .order_by(AdguardSnapshot.timestamp.desc())
        .limit(1)
    )).scalar_one_or_none()

    if snap and snap.data_json:
        data = json.loads(snap.data_json)
    else:
        try:
            password = (
                decrypt_value(instance.password_enc)
                if instance.password_enc else None
            )
            data = await AdguardAPI(
                host       = instance.host,
                username   = instance.username,
                password   = password,
                verify_ssl = instance.verify_ssl,
            ).fetch_all()
        except Exception as e:
            error = str(e)

    return templates.TemplateResponse("adguard_detail.html", {
        "request":    request,
        "instance":   instance,
        "data":       data,
        "error":      error,
        "snap":       snap,
        "active_page": "adguard",
        "active_tab":  request.query_params.get("tab", "overview"),
        "saved":       request.query_params.get("saved"),
    })


# ── CRUD ──────────────────────────────────────────────────────────────────────

@router.post("/add")
async def add_instance(
    name:       str  = Form(...),
    host:       str  = Form(...),
    username:   str  = Form(""),
    password:   str  = Form(""),
    verify_ssl: str  = Form("off"),
    db: AsyncSession = Depends(get_db),
):
    inst = AdguardInstance(
        name         = name.strip(),
        host         = host.strip().rstrip("/"),
        username     = username.strip() or None,
        password_enc = encrypt_value(password.strip()) if password.strip() else None,
        verify_ssl   = (verify_ssl == "on"),
    )
    db.add(inst)
    await db.commit()
    return RedirectResponse(url="/adguard", status_code=303)


@router.post("/{instance_id}/edit")
async def edit_instance(
    instance_id: int,
    name:        str  = Form(...),
    host:        str  = Form(...),
    username:    str  = Form(""),
    password:    str  = Form(""),
    verify_ssl:  str  = Form("off"),
    db: AsyncSession  = Depends(get_db),
):
    instance = await db.get(AdguardInstance, instance_id)
    if instance:
        instance.name       = name.strip()
        instance.host       = host.strip().rstrip("/")
        instance.username   = username.strip() or None
        instance.verify_ssl = (verify_ssl == "on")
        if password.strip():
            instance.password_enc = encrypt_value(password.strip())
        await db.commit()
    return RedirectResponse(url=f"/adguard/{instance_id}?saved=1", status_code=303)


@router.post("/{instance_id}/delete")
async def delete_instance(instance_id: int, db: AsyncSession = Depends(get_db)):
    instance = await db.get(AdguardInstance, instance_id)
    if instance:
        await db.delete(instance)
        await db.commit()
    return RedirectResponse(url="/adguard", status_code=303)
