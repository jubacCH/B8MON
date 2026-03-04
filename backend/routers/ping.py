from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from collectors.ping import ping_host
from database import PingHost, PingResult, get_db

router = APIRouter(prefix="/ping")
templates = Jinja2Templates(directory="templates")


# ── API (JSON) — must be before /{host_id} to avoid int-cast conflict ─────────

@router.get("/api/status")
async def api_status(db: AsyncSession = Depends(get_db)):
    """JSON status for all ping hosts (used by dashboard live-update)."""
    result = await db.execute(select(PingHost).where(PingHost.enabled == True))
    hosts = result.scalars().all()
    out = []
    for host in hosts:
        latest = await db.execute(
            select(PingResult)
            .where(PingResult.host_id == host.id)
            .order_by(PingResult.timestamp.desc())
            .limit(1)
        )
        lr = latest.scalar_one_or_none()
        out.append({
            "id": host.id,
            "name": host.name,
            "hostname": host.hostname,
            "online": lr.success if lr else None,
            "latency_ms": lr.latency_ms if lr else None,
        })
    return out


@router.get("/api/test/{host_id}")
async def test_ping(host_id: int, db: AsyncSession = Depends(get_db)):
    host = await db.get(PingHost, host_id)
    if not host:
        return {"success": False, "error": "Host not found"}
    ok, latency = await ping_host(host.hostname)
    return {"success": ok, "latency_ms": latency}


# ── HTML views ─────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def ping_list(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PingHost).order_by(PingHost.name))
    hosts = result.scalars().all()

    host_data = []
    now = datetime.utcnow()
    window_24h = now - timedelta(hours=24)

    for host in hosts:
        latest = await db.execute(
            select(PingResult)
            .where(PingResult.host_id == host.id)
            .order_by(PingResult.timestamp.desc())
            .limit(1)
        )
        latest_result = latest.scalar_one_or_none()

        total = await db.execute(
            select(func.count()).where(
                PingResult.host_id == host.id,
                PingResult.timestamp >= window_24h,
            )
        )
        success = await db.execute(
            select(func.count()).where(
                PingResult.host_id == host.id,
                PingResult.timestamp >= window_24h,
                PingResult.success == True,
            )
        )
        total_c = total.scalar() or 0
        success_c = success.scalar() or 0
        uptime = round((success_c / total_c * 100) if total_c > 0 else 0, 1)

        host_data.append({
            "host": host,
            "online": latest_result.success if latest_result else None,
            "latency": latest_result.latency_ms if latest_result else None,
            "last_check": latest_result.timestamp if latest_result else None,
            "uptime_pct": uptime,
        })

    return templates.TemplateResponse("ping.html", {
        "request": request,
        "host_data": host_data,
        "active_page": "ping",
    })


@router.get("/{host_id}", response_class=HTMLResponse)
async def ping_detail(host_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    host = await db.get(PingHost, host_id)
    if not host:
        return RedirectResponse(url="/ping")

    window = datetime.utcnow() - timedelta(hours=24)
    results_q = await db.execute(
        select(PingResult)
        .where(PingResult.host_id == host_id, PingResult.timestamp >= window)
        .order_by(PingResult.timestamp.asc())
    )
    results = results_q.scalars().all()

    chart_labels = [r.timestamp.strftime("%H:%M") for r in results]
    chart_latency = [r.latency_ms if r.success else None for r in results]

    total = len(results)
    success_c = sum(1 for r in results if r.success)
    uptime = round((success_c / total * 100) if total > 0 else 0, 1)
    latencies = [r.latency_ms for r in results if r.success and r.latency_ms is not None]
    avg_lat = round(sum(latencies) / len(latencies), 2) if latencies else None
    min_lat = round(min(latencies), 2) if latencies else None
    max_lat = round(max(latencies), 2) if latencies else None

    latest_q = await db.execute(
        select(PingResult)
        .where(PingResult.host_id == host_id)
        .order_by(PingResult.timestamp.desc())
        .limit(1)
    )
    latest = latest_q.scalar_one_or_none()

    return templates.TemplateResponse("ping_detail.html", {
        "request": request,
        "host": host,
        "latest": latest,
        "uptime_pct": uptime,
        "avg_latency": avg_lat,
        "min_latency": min_lat,
        "max_latency": max_lat,
        "chart_labels": chart_labels,
        "chart_latency": chart_latency,
        "active_page": "ping",
        "saved": request.query_params.get("saved"),
        "active_tab": request.query_params.get("tab", "info"),
    })


# ── CRUD actions ───────────────────────────────────────────────────────────────

@router.post("/add")
async def add_ping_host(
    name: str = Form(...),
    hostname: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    db.add(PingHost(name=name.strip(), hostname=hostname.strip()))
    await db.commit()
    return RedirectResponse(url="/ping", status_code=303)


@router.post("/{host_id}/edit")
async def edit_ping_host(
    host_id: int,
    name: str = Form(...),
    hostname: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    host = await db.get(PingHost, host_id)
    if host:
        host.name = name.strip()
        host.hostname = hostname.strip()
        await db.commit()
    return RedirectResponse(url=f"/ping/{host_id}?tab=info&saved=1", status_code=303)


@router.post("/{host_id}/delete")
async def delete_ping_host(host_id: int, db: AsyncSession = Depends(get_db)):
    host = await db.get(PingHost, host_id)
    if host:
        await db.delete(host)
        await db.commit()
    return RedirectResponse(url="/ping", status_code=303)


@router.post("/{host_id}/toggle")
async def toggle_ping_host(host_id: int, db: AsyncSession = Depends(get_db)):
    host = await db.get(PingHost, host_id)
    if host:
        host.enabled = not host.enabled
        await db.commit()
    return RedirectResponse(url=f"/ping/{host_id}?tab=info", status_code=303)
