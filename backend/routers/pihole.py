import json

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from collectors.pihole import PiholeAPI
from database import PiholeInstance, PiholeSnapshot, decrypt_value, encrypt_value, get_db

router = APIRouter(prefix="/pihole")
templates = Jinja2Templates(directory="templates")


# ── List ──────────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def pihole_list(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PiholeInstance).order_by(PiholeInstance.name))
    instances = result.scalars().all()

    instance_data = []
    for inst in instances:
        snap = (await db.execute(
            select(PiholeSnapshot)
            .where(PiholeSnapshot.instance_id == inst.id)
            .order_by(PiholeSnapshot.timestamp.desc())
            .limit(1)
        )).scalar_one_or_none()

        data = json.loads(snap.data_json) if snap and snap.data_json else None
        instance_data.append({"instance": inst, "snap": snap, "data": data})

    return templates.TemplateResponse("pihole_list.html", {
        "request":       request,
        "instance_data": instance_data,
        "active_page":   "pihole",
    })


# ── Detail ────────────────────────────────────────────────────────────────────

@router.get("/{instance_id}", response_class=HTMLResponse)
async def pihole_detail(instance_id: int, request: Request,
                        db: AsyncSession = Depends(get_db)):
    instance = await db.get(PiholeInstance, instance_id)
    if not instance:
        return RedirectResponse(url="/pihole")

    error = None
    data  = None

    snap = (await db.execute(
        select(PiholeSnapshot)
        .where(PiholeSnapshot.instance_id == instance_id, PiholeSnapshot.ok == True)
        .order_by(PiholeSnapshot.timestamp.desc())
        .limit(1)
    )).scalar_one_or_none()

    if snap and snap.data_json:
        data = json.loads(snap.data_json)
    else:
        try:
            api_key = decrypt_value(instance.api_key_enc) if instance.api_key_enc else None
            data = await PiholeAPI(
                host       = instance.host,
                api_key    = api_key,
                verify_ssl = instance.verify_ssl,
            ).fetch_all()
        except Exception as e:
            error = str(e)

    return templates.TemplateResponse("pihole_detail.html", {
        "request":    request,
        "instance":   instance,
        "data":       data,
        "error":      error,
        "snap":       snap,
        "active_page": "pihole",
        "active_tab":  request.query_params.get("tab", "overview"),
        "saved":       request.query_params.get("saved"),
    })


# ── CRUD ──────────────────────────────────────────────────────────────────────

@router.post("/add")
async def add_instance(
    name:       str  = Form(...),
    host:       str  = Form(...),
    api_key:    str  = Form(""),
    verify_ssl: str  = Form("off"),
    db: AsyncSession = Depends(get_db),
):
    inst = PiholeInstance(
        name        = name.strip(),
        host        = host.strip().rstrip("/"),
        api_key_enc = encrypt_value(api_key.strip()) if api_key.strip() else None,
        verify_ssl  = (verify_ssl == "on"),
    )
    db.add(inst)
    await db.commit()
    return RedirectResponse(url="/pihole", status_code=303)


@router.post("/{instance_id}/edit")
async def edit_instance(
    instance_id: int,
    name:        str  = Form(...),
    host:        str  = Form(...),
    api_key:     str  = Form(""),
    verify_ssl:  str  = Form("off"),
    db: AsyncSession  = Depends(get_db),
):
    instance = await db.get(PiholeInstance, instance_id)
    if instance:
        instance.name       = name.strip()
        instance.host       = host.strip().rstrip("/")
        instance.verify_ssl = (verify_ssl == "on")
        if api_key.strip():
            instance.api_key_enc = encrypt_value(api_key.strip())
        await db.commit()
    return RedirectResponse(url=f"/pihole/{instance_id}?saved=1", status_code=303)


@router.post("/{instance_id}/delete")
async def delete_instance(instance_id: int, db: AsyncSession = Depends(get_db)):
    instance = await db.get(PiholeInstance, instance_id)
    if instance:
        await db.delete(instance)
        await db.commit()
    return RedirectResponse(url="/pihole", status_code=303)
