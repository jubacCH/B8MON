import json

import httpx
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from collectors.unas import UnasAPI
from database import PingHost, UnasServer, UnasSnapshot, decrypt_value, encrypt_value, get_db

router = APIRouter(prefix="/unas")
templates = Jinja2Templates(directory="templates")


async def _ensure_ping_host(server: UnasServer, db: AsyncSession) -> None:
    """Create or update a PingHost entry for this UNAS device."""
    import re
    # Extract bare hostname/IP from the URL (strip scheme + trailing slashes)
    host_str = re.sub(r"^https?://", "", server.host).rstrip("/").split(":")[0]

    existing = (await db.execute(
        select(PingHost).where(PingHost.hostname == host_str)
    )).scalar_one_or_none()

    if existing:
        # Update name/source if it was manually added
        if existing.source == "manual":
            existing.source        = "unas"
            existing.source_detail = server.name
    else:
        db.add(PingHost(
            name          = server.name,
            hostname      = host_str,
            check_type    = "https",
            enabled       = True,
            source        = "unas",
            source_detail = server.name,
        ))
    await db.commit()


def _fmt_uptime(seconds: int) -> str:
    if not seconds:
        return "—"
    d, r = divmod(int(seconds), 86400)
    h, r = divmod(r, 3600)
    m    = r // 60
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


templates.env.globals["fmt_uptime_unas"] = _fmt_uptime


# ── List ──────────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def unas_list(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(UnasServer).order_by(UnasServer.name))
    servers = result.scalars().all()

    server_data = []
    for srv in servers:
        snap = (await db.execute(
            select(UnasSnapshot)
            .where(UnasSnapshot.server_id == srv.id, UnasSnapshot.ok == True)
            .order_by(UnasSnapshot.timestamp.desc())
            .limit(1)
        )).scalar_one_or_none()

        data = json.loads(snap.data_json) if snap else None
        server_data.append({"server": srv, "snap": snap, "data": data})

    return templates.TemplateResponse("unas.html", {
        "request":     request,
        "server_data": server_data,
        "active_page": "unas",
    })


# ── Detail ────────────────────────────────────────────────────────────────────

@router.get("/{server_id}", response_class=HTMLResponse)
async def unas_detail(server_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    server = await db.get(UnasServer, server_id)
    if not server:
        return RedirectResponse(url="/unas")

    error = None
    data  = None

    snap = (await db.execute(
        select(UnasSnapshot)
        .where(UnasSnapshot.server_id == server_id, UnasSnapshot.ok == True)
        .order_by(UnasSnapshot.timestamp.desc())
        .limit(1)
    )).scalar_one_or_none()

    if snap:
        data = json.loads(snap.data_json)
    else:
        try:
            data = await UnasAPI(
                host       = server.host,
                username   = server.username,
                password   = decrypt_value(server.password_enc),
                verify_ssl = server.verify_ssl,
            ).fetch_all()
        except Exception as e:
            error = str(e)

    return templates.TemplateResponse("unas_detail.html", {
        "request":    request,
        "server":     server,
        "data":       data,
        "error":      error,
        "snap":       snap,
        "active_page": "unas",
        "active_tab":  request.query_params.get("tab", "overview"),
        "saved":       request.query_params.get("saved"),
    })


# ── CRUD ──────────────────────────────────────────────────────────────────────

@router.post("/add")
async def add_server(
    name:       str  = Form(...),
    host:       str  = Form(...),
    username:   str  = Form(...),
    password:   str  = Form(...),
    verify_ssl: str  = Form("off"),
    db: AsyncSession = Depends(get_db),
):
    srv = UnasServer(
        name         = name.strip(),
        host         = host.strip().rstrip("/"),
        username     = username.strip(),
        password_enc = encrypt_value(password),
        verify_ssl   = (verify_ssl == "on"),
    )
    db.add(srv)
    await db.commit()
    await db.refresh(srv)
    await _ensure_ping_host(srv, db)
    return RedirectResponse(url="/unas", status_code=303)


@router.post("/{server_id}/edit")
async def edit_server(
    server_id:  int,
    name:       str  = Form(...),
    host:       str  = Form(...),
    username:   str  = Form(...),
    password:   str  = Form(""),
    verify_ssl: str  = Form("off"),
    db: AsyncSession = Depends(get_db),
):
    server = await db.get(UnasServer, server_id)
    if server:
        server.name       = name.strip()
        server.host       = host.strip().rstrip("/")
        server.username   = username.strip()
        server.verify_ssl = (verify_ssl == "on")
        if password.strip():
            server.password_enc = encrypt_value(password.strip())
        await db.commit()
        await _ensure_ping_host(server, db)
    return RedirectResponse(url=f"/unas/{server_id}?saved=1", status_code=303)


@router.post("/{server_id}/delete")
async def delete_server(server_id: int, db: AsyncSession = Depends(get_db)):
    server = await db.get(UnasServer, server_id)
    if server:
        await db.delete(server)
        await db.commit()
    return RedirectResponse(url="/unas", status_code=303)


# ── JSON API ──────────────────────────────────────────────────────────────────

@router.get("/api/{server_id}/status")
async def api_server_status(server_id: int, db: AsyncSession = Depends(get_db)):
    server = await db.get(UnasServer, server_id)
    if not server:
        return JSONResponse({"error": "Server not found"}, status_code=404)

    snap = (await db.execute(
        select(UnasSnapshot)
        .where(UnasSnapshot.server_id == server_id)
        .order_by(UnasSnapshot.timestamp.desc())
        .limit(1)
    )).scalar_one_or_none()

    base = {"server_id": server_id, "name": server.name}
    if snap:
        base["cached_at"] = snap.timestamp.isoformat()
        if snap.ok:
            return {"ok": True, **base, **json.loads(snap.data_json)}
        return {"ok": False, **base, "error": snap.error}

    try:
        data = await UnasAPI(
            host       = server.host,
            username   = server.username,
            password   = decrypt_value(server.password_enc),
            verify_ssl = server.verify_ssl,
        ).fetch_all()
        return {"ok": True, **base, **data}
    except Exception as exc:
        return {"ok": False, **base, "error": str(exc)}


@router.get("/api/{server_id}/debug")
async def api_debug(server_id: int, db: AsyncSession = Depends(get_db)):
    """Return raw /api/system response for debugging."""
    server = await db.get(UnasServer, server_id)
    if not server:
        return JSONResponse({"error": "Server not found"}, status_code=404)
    try:
        async with httpx.AsyncClient(verify=server.verify_ssl, timeout=15.0,
                                     follow_redirects=True) as client:
            resp = await client.post(
                f"{server.host}/api/auth/login",
                json={"username": server.username,
                      "password": decrypt_value(server.password_enc)},
            )
            resp.raise_for_status()
            csrf = resp.headers.get("x-csrf-token", "")
            hdrs = {"x-csrf-token": csrf} if csrf else {}
            sr = await client.get(f"{server.host}/api/system", headers=hdrs)
            sr.raise_for_status()
            return {"ok": True, "system": sr.json()}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
