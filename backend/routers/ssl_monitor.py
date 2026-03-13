"""SSL certificate monitoring page and API."""
import asyncio
import logging
import ssl as _ssl
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import PingHost, get_db
from templating import templates

router = APIRouter()
log = logging.getLogger(__name__)


async def _get_ssl_info(hostname: str, port: int = 443) -> dict:
    """Get detailed SSL certificate info for a host."""
    try:
        loop = asyncio.get_event_loop()
        cert_pem = await loop.run_in_executor(
            None, lambda: _ssl.get_server_certificate((hostname, port), timeout=5)
        )
        # Get expiry date
        proc = await asyncio.create_subprocess_exec(
            "openssl", "x509", "-noout", "-enddate", "-startdate", "-issuer", "-subject",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate(input=cert_pem.encode())
        lines = stdout.decode().strip().split("\n")
        info = {}
        for line in lines:
            if "=" in line:
                key, _, val = line.partition("=")
                key = key.strip().lower()
                if key == "notafter":
                    info["expiry"] = val.strip()
                elif key == "notbefore":
                    info["issued"] = val.strip()
                elif key == "issuer":
                    info["issuer"] = val.strip()
                elif key == "subject":
                    info["subject"] = val.strip()

        # Parse expiry for days calculation
        if "expiry" in info:
            expiry_dt = datetime.strptime(info["expiry"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
            info["expiry_date"] = expiry_dt.strftime("%Y-%m-%d %H:%M UTC")
            info["days"] = max(0, (expiry_dt - datetime.now(timezone.utc)).days)
        if "issued" in info:
            try:
                issued_dt = datetime.strptime(info["issued"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
                info["issued_date"] = issued_dt.strftime("%Y-%m-%d")
            except Exception:
                pass

        # Extract CN from issuer/subject
        for field in ("issuer", "subject"):
            raw = info.get(field, "")
            # Parse "CN = example.com" or "CN=example.com"
            for part in raw.split(","):
                part = part.strip()
                if part.upper().startswith("CN"):
                    _, _, cn = part.partition("=")
                    info[f"{field}_cn"] = cn.strip()
                    break

        info["ok"] = True
        return info
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.get("/api/ssl/certs")
async def ssl_certs_json(db: AsyncSession = Depends(get_db)):
    """JSON endpoint for SSL certificate data."""
    result = await db.execute(
        select(PingHost)
        .where(PingHost.check_type.contains("https"))
        .order_by(PingHost.name)
    )
    hosts = result.scalars().all()
    certs = []
    for h in hosts:
        certs.append({
            "id": h.id,
            "name": h.name,
            "hostname": h.hostname,
            "enabled": h.enabled,
            "days": h.ssl_expiry_days,
        })
    certs.sort(key=lambda c: c["days"] if c["days"] is not None else 9999)
    expiring_soon = sum(1 for c in certs if c["days"] is not None and c["days"] <= 30)
    return JSONResponse({"certs": certs, "expiring_soon": expiring_soon})


@router.get("/ssl", response_class=HTMLResponse)
async def ssl_page(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(PingHost)
        .where(PingHost.check_type.contains("https"))
        .order_by(PingHost.name)
    )
    hosts = result.scalars().all()

    certs = []
    for h in hosts:
        certs.append({
            "id": h.id,
            "name": h.name,
            "hostname": h.hostname,
            "enabled": h.enabled,
            "days": h.ssl_expiry_days,
        })

    # Sort: expiring soonest first, None at bottom
    certs.sort(key=lambda c: c["days"] if c["days"] is not None else 9999)

    expiring_soon = sum(1 for c in certs if c["days"] is not None and c["days"] <= 30)

    accept = request.headers.get("accept", "")
    if "application/json" in accept:
        return JSONResponse({"certs": certs, "expiring_soon": expiring_soon})

    return templates.TemplateResponse("ssl.html", {
        "request": request,
        "certs": certs,
        "expiring_soon": expiring_soon,
        "active_page": "ssl",
    })


@router.post("/api/ssl/refresh/{host_id}")
async def refresh_ssl(host_id: int, db: AsyncSession = Depends(get_db)):
    """Manually refresh SSL info for a single host."""
    host = await db.get(PingHost, host_id)
    if not host:
        return JSONResponse({"error": "Host not found"}, status_code=404)

    hostname = (host.hostname or "").strip()
    for prefix in ("https://", "http://"):
        if hostname.startswith(prefix):
            hostname = hostname[len(prefix):]
    hostname = hostname.rstrip("/").split("/")[0]

    port = 443
    if ":" in hostname:
        parts = hostname.rsplit(":", 1)
        hostname = parts[0]
        try:
            port = int(parts[1])
        except ValueError:
            pass

    info = await _get_ssl_info(hostname, port)
    if info.get("ok") and "days" in info:
        host.ssl_expiry_days = info["days"]
        await db.commit()

    return JSONResponse(info)


@router.post("/api/ssl/refresh-all")
async def refresh_all_ssl(db: AsyncSession = Depends(get_db)):
    """Refresh SSL info for all HTTPS hosts."""
    from utils.ping import get_ssl_expiry_days
    result = await db.execute(
        select(PingHost).where(PingHost.check_type.contains("https"), PingHost.enabled == True)
    )
    hosts = result.scalars().all()
    updated = 0
    for h in hosts:
        hostname = (h.hostname or "").strip()
        for prefix in ("https://", "http://"):
            if hostname.startswith(prefix):
                hostname = hostname[len(prefix):]
        hostname = hostname.rstrip("/").split("/")[0]
        port = 443
        if ":" in hostname:
            parts = hostname.rsplit(":", 1)
            hostname = parts[0]
            try:
                port = int(parts[1])
            except ValueError:
                pass
        days = await get_ssl_expiry_days(hostname, port)
        if days is not None:
            h.ssl_expiry_days = days
            updated += 1
    await db.commit()
    return JSONResponse({"ok": True, "updated": updated, "total": len(hosts)})
