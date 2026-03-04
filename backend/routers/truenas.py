import json

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from collectors.truenas import TruenasAPI
from database import TruenasServer, TruenasSnapshot, decrypt_value, encrypt_value, get_db

router = APIRouter(prefix="/truenas")
templates = Jinja2Templates(directory="templates")


def _fmt_uptime(seconds: int) -> str:
    if not seconds:
        return "—"
    d, r = divmod(int(seconds), 86400)
    h, r = divmod(r, 3600)
    m = r // 60
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


templates.env.globals["fmt_uptime_truenas"] = _fmt_uptime


# ── List ──────────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def truenas_list(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(TruenasServer).order_by(TruenasServer.name))
    servers = result.scalars().all()

    server_data = []
    for srv in servers:
        snap = (await db.execute(
            select(TruenasSnapshot)
            .where(TruenasSnapshot.server_id == srv.id, TruenasSnapshot.ok == True)
            .order_by(TruenasSnapshot.timestamp.desc())
            .limit(1)
        )).scalar_one_or_none()

        # Also get the most recent snapshot for error display
        last_snap = (await db.execute(
            select(TruenasSnapshot)
            .where(TruenasSnapshot.server_id == srv.id)
            .order_by(TruenasSnapshot.timestamp.desc())
            .limit(1)
        )).scalar_one_or_none()

        data = json.loads(snap.data_json) if snap else None
        server_data.append({"server": srv, "snap": last_snap, "data": data})

    return templates.TemplateResponse("truenas_list.html", {
        "request": request,
        "server_data": server_data,
        "active_page": "truenas",
    })


# ── Detail ────────────────────────────────────────────────────────────────────

@router.get("/{server_id}", response_class=HTMLResponse)
async def truenas_detail(server_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    server = await db.get(TruenasServer, server_id)
    if not server:
        return RedirectResponse(url="/truenas")

    error = None
    data = None

    snap = (await db.execute(
        select(TruenasSnapshot)
        .where(TruenasSnapshot.server_id == server_id, TruenasSnapshot.ok == True)
        .order_by(TruenasSnapshot.timestamp.desc())
        .limit(1)
    )).scalar_one_or_none()

    if snap:
        data = json.loads(snap.data_json)
    else:
        try:
            data = await TruenasAPI(
                host=server.host,
                api_key=decrypt_value(server.api_key_enc),
                verify_ssl=server.verify_ssl,
            ).fetch_all()
        except Exception as e:
            error = str(e)

    return templates.TemplateResponse("truenas_detail.html", {
        "request": request,
        "server": server,
        "data": data,
        "error": error,
        "snap": snap,
        "active_page": "truenas",
        "active_tab": request.query_params.get("tab", "overview"),
        "saved": request.query_params.get("saved"),
    })


# ── CRUD ──────────────────────────────────────────────────────────────────────

@router.post("/add")
async def add_server(
    name:       str  = Form(...),
    host:       str  = Form(...),
    api_key:    str  = Form(...),
    verify_ssl: str  = Form(""),
    db: AsyncSession = Depends(get_db),
):
    verify_ssl_bool = verify_ssl == "on"
    srv = TruenasServer(
        name=name.strip(),
        host=host.strip().rstrip("/"),
        api_key_enc=encrypt_value(api_key),
        verify_ssl=verify_ssl_bool,
    )
    db.add(srv)
    await db.commit()
    return RedirectResponse(url="/truenas", status_code=303)


@router.post("/{server_id}/edit")
async def edit_server(
    server_id:  int,
    name:       str  = Form(...),
    host:       str  = Form(...),
    api_key:    str  = Form(""),
    verify_ssl: str  = Form(""),
    db: AsyncSession = Depends(get_db),
):
    server = await db.get(TruenasServer, server_id)
    if server:
        server.name = name.strip()
        server.host = host.strip().rstrip("/")
        server.verify_ssl = verify_ssl == "on"
        if api_key.strip():
            server.api_key_enc = encrypt_value(api_key.strip())
        await db.commit()
    return RedirectResponse(url=f"/truenas/{server_id}?saved=1", status_code=303)


@router.post("/{server_id}/delete")
async def delete_server(server_id: int, db: AsyncSession = Depends(get_db)):
    server = await db.get(TruenasServer, server_id)
    if server:
        await db.delete(server)
        await db.commit()
    return RedirectResponse(url="/truenas", status_code=303)


# ── JSON API ──────────────────────────────────────────────────────────────────

@router.get("/api/{server_id}/status")
async def api_server_status(server_id: int, db: AsyncSession = Depends(get_db)):
    server = await db.get(TruenasServer, server_id)
    if not server:
        return JSONResponse({"error": "Server not found"}, status_code=404)

    snap = (await db.execute(
        select(TruenasSnapshot)
        .where(TruenasSnapshot.server_id == server_id)
        .order_by(TruenasSnapshot.timestamp.desc())
        .limit(1)
    )).scalar_one_or_none()

    base = {"server_id": server_id, "name": server.name}
    if snap:
        base["cached_at"] = snap.timestamp.isoformat()
        if snap.ok:
            return {"ok": True, **base, **json.loads(snap.data_json)}
        return {"ok": False, **base, "error": snap.error}

    try:
        data = await TruenasAPI(
            host=server.host,
            api_key=decrypt_value(server.api_key_enc),
            verify_ssl=server.verify_ssl,
        ).fetch_all()
        return {"ok": True, **base, **data}
    except Exception as exc:
        return {"ok": False, **base, "error": str(exc)}
