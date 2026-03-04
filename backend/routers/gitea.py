import json

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from collectors.gitea import GiteaAPI, collect_gitea_instance, parse_gitea_data
from database import GiteaInstance, GiteaSnapshot, decrypt_value, encrypt_value, get_db

router = APIRouter(prefix="/gitea")
templates = Jinja2Templates(directory="templates")


# ── List ──────────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def gitea_list(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(GiteaInstance).order_by(GiteaInstance.name))
    instances = result.scalars().all()

    rows = []
    for inst in instances:
        snap = (await db.execute(
            select(GiteaSnapshot)
            .where(GiteaSnapshot.instance_id == inst.id)
            .order_by(GiteaSnapshot.timestamp.desc())
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

    return templates.TemplateResponse("gitea_list.html", {
        "request": request,
        "rows": rows,
        "active_page": "gitea",
    })


# ── Detail ────────────────────────────────────────────────────────────────────

@router.get("/{instance_id}", response_class=HTMLResponse)
async def gitea_detail(instance_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    instance = await db.get(GiteaInstance, instance_id)
    if not instance:
        return RedirectResponse(url="/gitea", status_code=303)

    error = None
    data = None

    snap = (await db.execute(
        select(GiteaSnapshot)
        .where(GiteaSnapshot.instance_id == instance_id, GiteaSnapshot.ok == True)
        .order_by(GiteaSnapshot.timestamp.desc())
        .limit(1)
    )).scalar_one_or_none()

    if snap and snap.data_json:
        try:
            data = json.loads(snap.data_json)
        except Exception:
            pass

    if data is None:
        try:
            token = decrypt_value(instance.token_enc) if instance.token_enc else None
            api = GiteaAPI(host=instance.host, token=token, verify_ssl=instance.verify_ssl)
            raw = await api.fetch_all()
            data = parse_gitea_data(raw["version_info"], raw["repos"], raw["users"], raw["orgs"])
        except Exception as exc:
            error = str(exc)

    last_snap = (await db.execute(
        select(GiteaSnapshot)
        .where(GiteaSnapshot.instance_id == instance_id)
        .order_by(GiteaSnapshot.timestamp.desc())
        .limit(1)
    )).scalar_one_or_none()

    if last_snap and not last_snap.ok and error is None:
        error = last_snap.error

    return templates.TemplateResponse("gitea_detail.html", {
        "request": request,
        "instance": instance,
        "data": data,
        "error": error,
        "snap": snap or last_snap,
        "active_page": "gitea",
    })


# ── CRUD ──────────────────────────────────────────────────────────────────────

@router.post("/add")
async def add_instance(
    name: str = Form(...),
    host: str = Form(...),
    token: str = Form(""),
    verify_ssl: str = Form("off"),
    db: AsyncSession = Depends(get_db),
):
    db.add(GiteaInstance(
        name=name.strip(),
        host=host.strip().rstrip("/"),
        token_enc=encrypt_value(token.strip()) if token.strip() else None,
        verify_ssl=(verify_ssl == "on"),
    ))
    await db.commit()
    return RedirectResponse(url="/gitea", status_code=303)


@router.post("/{instance_id}/edit")
async def edit_instance(
    instance_id: int,
    name: str = Form(...),
    host: str = Form(...),
    token: str = Form(""),
    verify_ssl: str = Form("off"),
    db: AsyncSession = Depends(get_db),
):
    instance = await db.get(GiteaInstance, instance_id)
    if instance:
        instance.name = name.strip()
        instance.host = host.strip().rstrip("/")
        instance.verify_ssl = (verify_ssl == "on")
        if token.strip():
            instance.token_enc = encrypt_value(token.strip())
        await db.commit()
    return RedirectResponse(url=f"/gitea/{instance_id}", status_code=303)


@router.post("/{instance_id}/delete")
async def delete_instance(instance_id: int, db: AsyncSession = Depends(get_db)):
    instance = await db.get(GiteaInstance, instance_id)
    if instance:
        await db.delete(instance)
        await db.commit()
    return RedirectResponse(url="/gitea", status_code=303)


@router.post("/{instance_id}/refresh")
async def refresh_instance(instance_id: int, db: AsyncSession = Depends(get_db)):
    """Manually trigger a data collection for this instance."""
    await collect_gitea_instance(instance_id, db)
    return RedirectResponse(url=f"/gitea/{instance_id}", status_code=303)
