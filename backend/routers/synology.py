import json
from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from collectors.synology import SynologyAPI, parse_synology_data
from database import (
    SynologyServer,
    SynologySnapshot,
    decrypt_value,
    encrypt_value,
    get_db,
)

router = APIRouter(prefix="/synology")
templates = Jinja2Templates(directory="templates")


# ── List ──────────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def synology_list(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SynologyServer).order_by(SynologyServer.name))
    servers = result.scalars().all()

    # For each server, load the latest snapshot
    server_data = []
    for srv in servers:
        snap = (await db.execute(
            select(SynologySnapshot)
            .where(SynologySnapshot.server_id == srv.id)
            .order_by(SynologySnapshot.timestamp.desc())
            .limit(1)
        )).scalar_one_or_none()

        data = None
        error = None
        if snap:
            if snap.ok and snap.data_json:
                data = json.loads(snap.data_json)
            else:
                error = snap.error

        server_data.append({
            "server": srv,
            "data": data,
            "error": error,
            "last_checked": snap.timestamp if snap else None,
        })

    return templates.TemplateResponse("synology_list.html", {
        "request": request,
        "server_data": server_data,
        "active_page": "synology",
    })


# ── Detail ────────────────────────────────────────────────────────────────────

@router.get("/{server_id}", response_class=HTMLResponse)
async def synology_detail(
    server_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    server = await db.get(SynologyServer, server_id)
    if not server:
        return RedirectResponse("/synology")

    error = None
    data = None

    snap = (await db.execute(
        select(SynologySnapshot)
        .where(SynologySnapshot.server_id == server_id, SynologySnapshot.ok == True)
        .order_by(SynologySnapshot.timestamp.desc())
        .limit(1)
    )).scalar_one_or_none()

    if snap and snap.data_json:
        data = json.loads(snap.data_json)
    else:
        # No good snapshot yet – try a live fetch
        try:
            api = SynologyAPI(
                host=server.host,
                port=server.port,
                username=server.username,
                password=decrypt_value(server.password_enc),
                verify_ssl=server.verify_ssl,
            )
            raw = await api.fetch_all()
            data = parse_synology_data(
                raw["info"], raw["storage"], raw["load"]
            )
        except Exception as exc:
            error = str(exc)

    last_error_snap = (await db.execute(
        select(SynologySnapshot)
        .where(SynologySnapshot.server_id == server_id, SynologySnapshot.ok == False)
        .order_by(SynologySnapshot.timestamp.desc())
        .limit(1)
    )).scalar_one_or_none()

    last_checked = snap.timestamp if snap else None

    return templates.TemplateResponse("synology_detail.html", {
        "request": request,
        "server": server,
        "data": data,
        "error": error,
        "last_checked": last_checked,
        "last_error": last_error_snap.error if last_error_snap else None,
        "active_page": "synology",
    })


# ── Add ───────────────────────────────────────────────────────────────────────

@router.post("/add")
async def add_server(
    name: str = Form(...),
    host: str = Form(...),
    port: int = Form(5001),
    username: str = Form(...),
    password: str = Form(...),
    verify_ssl: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    db.add(SynologyServer(
        name=name.strip(),
        host=host.strip().rstrip("/"),
        port=port,
        username=username.strip(),
        password_enc=encrypt_value(password),
        verify_ssl=(verify_ssl == "on"),
    ))
    await db.commit()
    return RedirectResponse("/synology", status_code=303)


# ── Edit ──────────────────────────────────────────────────────────────────────

@router.post("/{server_id}/edit")
async def edit_server(
    server_id: int,
    name: str = Form(...),
    host: str = Form(...),
    port: int = Form(5001),
    username: str = Form(...),
    password: str = Form(""),
    verify_ssl: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    server = await db.get(SynologyServer, server_id)
    if server:
        server.name = name.strip()
        server.host = host.strip().rstrip("/")
        server.port = port
        server.username = username.strip()
        server.verify_ssl = (verify_ssl == "on")
        if password.strip():
            server.password_enc = encrypt_value(password.strip())
        await db.commit()
    return RedirectResponse(f"/synology/{server_id}", status_code=303)


# ── Delete ────────────────────────────────────────────────────────────────────

@router.post("/{server_id}/delete")
async def delete_server(server_id: int, db: AsyncSession = Depends(get_db)):
    server = await db.get(SynologyServer, server_id)
    if server:
        await db.delete(server)
        await db.commit()
    return RedirectResponse("/synology", status_code=303)
