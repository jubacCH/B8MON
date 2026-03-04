import json
from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from collectors.phpipam import PhpIpamClient, sync_phpipam_hosts
from database import PhpipamServer, PhpipamSnapshot, decrypt_value, encrypt_value, get_db

router = APIRouter(prefix="/phpipam")
templates = Jinja2Templates(directory="templates")


# ── List ──────────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def phpipam_list(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PhpipamServer).order_by(PhpipamServer.name))
    servers = result.scalars().all()

    rows = []
    for srv in servers:
        snap = (await db.execute(
            select(PhpipamSnapshot)
            .where(PhpipamSnapshot.server_id == srv.id)
            .order_by(PhpipamSnapshot.timestamp.desc())
            .limit(1)
        )).scalar_one_or_none()

        data = None
        if snap and snap.ok and snap.data_json:
            try:
                data = json.loads(snap.data_json)
            except Exception:
                pass

        rows.append({
            "server": srv,
            "snap": snap,
            "data": data,
        })

    return templates.TemplateResponse("phpipam_list.html", {
        "request": request,
        "rows": rows,
        "active_page": "phpipam",
    })


# ── Detail ────────────────────────────────────────────────────────────────────

@router.get("/{server_id}", response_class=HTMLResponse)
async def phpipam_detail(server_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    server = await db.get(PhpipamServer, server_id)
    if not server:
        return RedirectResponse(url="/phpipam", status_code=303)

    # Last 10 snapshots for timeline
    snaps_result = await db.execute(
        select(PhpipamSnapshot)
        .where(PhpipamSnapshot.server_id == server_id)
        .order_by(PhpipamSnapshot.timestamp.desc())
        .limit(10)
    )
    snapshots = snaps_result.scalars().all()

    snap_rows = []
    for snap in snapshots:
        data = None
        if snap.data_json:
            try:
                data = json.loads(snap.data_json)
            except Exception:
                pass
        snap_rows.append({"snap": snap, "data": data})

    return templates.TemplateResponse("phpipam_detail.html", {
        "request": request,
        "server": server,
        "snap_rows": snap_rows,
        "active_page": "phpipam",
    })


# ── Sync ──────────────────────────────────────────────────────────────────────

@router.post("/{server_id}/sync")
async def sync_server(server_id: int, db: AsyncSession = Depends(get_db)):
    """Manually trigger a phpIPAM sync and store the result as a snapshot."""
    server = await db.get(PhpipamServer, server_id)
    if not server:
        return RedirectResponse(url="/phpipam", status_code=303)

    password = ""
    if server.password_enc:
        try:
            password = decrypt_value(server.password_enc)
        except Exception:
            pass

    client = PhpIpamClient(
        base_url=server.host,
        app_id=server.app_id,
        username=server.username or None,
        password=password or None,
        verify_ssl=server.verify_ssl,
    )

    errors: list[str] = []
    added = 0
    merged = 0
    skipped = 0
    ok = True

    try:
        await client.authenticate()
        addresses = await client.get_addresses()
    except Exception as exc:
        errors = [str(exc)]
        ok = False
        addresses = []

    if ok and addresses:
        # Use the global sync function to import into ping_hosts
        # Override settings-based approach by calling sync directly on this server
        from database import PingHost
        from sqlalchemy import select as sa_select

        existing_q = await db.execute(sa_select(PingHost))
        existing: dict[str, PingHost] = {h.hostname: h for h in existing_q.scalars().all()}

        dirty = False
        for addr in addresses:
            if str(addr.get("active", "1")) == "0":
                skipped += 1
                continue
            ip = (addr.get("ip") or "").strip()
            if not ip:
                skipped += 1
                continue
            name = (addr.get("hostname") or addr.get("description") or ip).strip() or ip
            try:
                if ip in existing:
                    host = existing[ip]
                    changed = False
                    if host.name == host.hostname and name != ip:
                        host.name = name[:128]
                        changed = True
                    if host.source == "manual":
                        host.source = "phpipam"
                        changed = True
                    if changed:
                        dirty = True
                    merged += 1
                else:
                    db.add(PingHost(
                        name=name[:128],
                        hostname=ip,
                        check_type="icmp",
                        enabled=True,
                        source="phpipam",
                        source_detail=server.host,
                    ))
                    existing[ip] = True  # type: ignore[assignment]
                    added += 1
                    dirty = True
            except Exception as exc:
                errors.append(f"{ip}: {exc}")

        if dirty:
            await db.flush()

    result_data = {"added": added, "merged": merged, "skipped": skipped, "errors": errors}

    db.add(PhpipamSnapshot(
        server_id=server_id,
        timestamp=datetime.utcnow(),
        ok=ok and len(errors) == 0,
        data_json=json.dumps(result_data),
        error="; ".join(errors) if errors else None,
    ))
    await db.commit()

    return RedirectResponse(url=f"/phpipam/{server_id}", status_code=303)


# ── CRUD ──────────────────────────────────────────────────────────────────────

@router.post("/add")
async def add_server(
    name: str = Form(...),
    host: str = Form(...),
    app_id: str = Form(...),
    username: str = Form(""),
    password: str = Form(""),
    verify_ssl: str = Form("off"),
    db: AsyncSession = Depends(get_db),
):
    db.add(PhpipamServer(
        name=name.strip(),
        host=host.strip().rstrip("/"),
        app_id=app_id.strip(),
        username=username.strip() or None,
        password_enc=encrypt_value(password.strip()) if password.strip() else None,
        verify_ssl=(verify_ssl == "on"),
    ))
    await db.commit()
    return RedirectResponse(url="/phpipam", status_code=303)


@router.post("/{server_id}/edit")
async def edit_server(
    server_id: int,
    name: str = Form(...),
    host: str = Form(...),
    app_id: str = Form(...),
    username: str = Form(""),
    password: str = Form(""),
    verify_ssl: str = Form("off"),
    db: AsyncSession = Depends(get_db),
):
    server = await db.get(PhpipamServer, server_id)
    if server:
        server.name = name.strip()
        server.host = host.strip().rstrip("/")
        server.app_id = app_id.strip()
        server.username = username.strip() or None
        server.verify_ssl = (verify_ssl == "on")
        if password.strip():
            server.password_enc = encrypt_value(password.strip())
        await db.commit()
    return RedirectResponse(url=f"/phpipam/{server_id}", status_code=303)


@router.post("/{server_id}/delete")
async def delete_server(server_id: int, db: AsyncSession = Depends(get_db)):
    server = await db.get(PhpipamServer, server_id)
    if server:
        await db.delete(server)
        await db.commit()
    return RedirectResponse(url="/phpipam", status_code=303)
