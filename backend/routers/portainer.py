import json

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from collectors.portainer import PortainerAPI
from database import PortainerInstance, PortainerSnapshot, decrypt_value, encrypt_value, get_db

router = APIRouter(prefix="/portainer")
templates = Jinja2Templates(directory="templates")


# ── List ──────────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def portainer_list(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PortainerInstance).order_by(PortainerInstance.name))
    instances = result.scalars().all()

    instance_data = []
    for inst in instances:
        snap = (await db.execute(
            select(PortainerSnapshot)
            .where(PortainerSnapshot.instance_id == inst.id, PortainerSnapshot.ok == True)
            .order_by(PortainerSnapshot.timestamp.desc())
            .limit(1)
        )).scalar_one_or_none()

        # Also check most recent snapshot (may be an error)
        last_snap = (await db.execute(
            select(PortainerSnapshot)
            .where(PortainerSnapshot.instance_id == inst.id)
            .order_by(PortainerSnapshot.timestamp.desc())
            .limit(1)
        )).scalar_one_or_none()

        data = json.loads(snap.data_json) if snap else None
        instance_data.append({
            "instance": inst,
            "snap": last_snap,
            "data": data,
        })

    return templates.TemplateResponse("portainer_list.html", {
        "request": request,
        "instance_data": instance_data,
        "active_page": "portainer",
    })


# ── Detail ────────────────────────────────────────────────────────────────────

@router.get("/{instance_id}", response_class=HTMLResponse)
async def portainer_detail(instance_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    instance = await db.get(PortainerInstance, instance_id)
    if not instance:
        return RedirectResponse(url="/portainer")

    error = None
    data = None

    snap = (await db.execute(
        select(PortainerSnapshot)
        .where(PortainerSnapshot.instance_id == instance_id, PortainerSnapshot.ok == True)
        .order_by(PortainerSnapshot.timestamp.desc())
        .limit(1)
    )).scalar_one_or_none()

    if snap:
        data = json.loads(snap.data_json)
    else:
        try:
            api_key = decrypt_value(instance.api_key_enc) if instance.api_key_enc else None
            data = await PortainerAPI(
                host=instance.host,
                api_key=api_key,
                verify_ssl=instance.verify_ssl,
            ).fetch_all()
        except Exception as e:
            error = str(e)

    return templates.TemplateResponse("portainer_detail.html", {
        "request": request,
        "instance": instance,
        "data": data,
        "error": error,
        "snap": snap,
        "active_page": "portainer",
        "active_tab": request.query_params.get("tab", "overview"),
        "saved": request.query_params.get("saved"),
    })


# ── CRUD ──────────────────────────────────────────────────────────────────────

@router.post("/add")
async def add_instance(
    name:       str  = Form(...),
    host:       str  = Form(...),
    api_key:    str  = Form(""),
    verify_ssl: str  = Form(""),
    db: AsyncSession = Depends(get_db),
):
    verify_ssl_bool = verify_ssl == "on"
    inst = PortainerInstance(
        name=name.strip(),
        host=host.strip().rstrip("/"),
        api_key_enc=encrypt_value(api_key) if api_key.strip() else None,
        verify_ssl=verify_ssl_bool,
    )
    db.add(inst)
    await db.commit()
    return RedirectResponse(url="/portainer", status_code=303)


@router.post("/{instance_id}/edit")
async def edit_instance(
    instance_id: int,
    name:        str  = Form(...),
    host:        str  = Form(...),
    api_key:     str  = Form(""),
    verify_ssl:  str  = Form(""),
    db: AsyncSession  = Depends(get_db),
):
    instance = await db.get(PortainerInstance, instance_id)
    if instance:
        instance.name = name.strip()
        instance.host = host.strip().rstrip("/")
        instance.verify_ssl = verify_ssl == "on"
        if api_key.strip():
            instance.api_key_enc = encrypt_value(api_key.strip())
        await db.commit()
    return RedirectResponse(url=f"/portainer/{instance_id}?saved=1", status_code=303)


@router.post("/{instance_id}/delete")
async def delete_instance(instance_id: int, db: AsyncSession = Depends(get_db)):
    instance = await db.get(PortainerInstance, instance_id)
    if instance:
        await db.delete(instance)
        await db.commit()
    return RedirectResponse(url="/portainer", status_code=303)


# ── JSON API ──────────────────────────────────────────────────────────────────

@router.get("/api/{instance_id}/status")
async def api_instance_status(instance_id: int, db: AsyncSession = Depends(get_db)):
    instance = await db.get(PortainerInstance, instance_id)
    if not instance:
        return JSONResponse({"error": "Instance not found"}, status_code=404)

    snap = (await db.execute(
        select(PortainerSnapshot)
        .where(PortainerSnapshot.instance_id == instance_id)
        .order_by(PortainerSnapshot.timestamp.desc())
        .limit(1)
    )).scalar_one_or_none()

    base = {"instance_id": instance_id, "name": instance.name}
    if snap:
        base["cached_at"] = snap.timestamp.isoformat()
        if snap.ok:
            return {"ok": True, **base, **json.loads(snap.data_json)}
        return {"ok": False, **base, "error": snap.error}

    try:
        api_key = decrypt_value(instance.api_key_enc) if instance.api_key_enc else None
        data = await PortainerAPI(
            host=instance.host,
            api_key=api_key,
            verify_ssl=instance.verify_ssl,
        ).fetch_all()
        return {"ok": True, **base, **data}
    except Exception as exc:
        return {"ok": False, **base, "error": str(exc)}
