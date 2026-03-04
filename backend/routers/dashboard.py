from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import PingHost, PingResult, ProxmoxCluster, get_db, is_setup_complete
from fastapi.responses import RedirectResponse

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    if not await is_setup_complete(db):
        return RedirectResponse(url="/setup")

    # Fetch all ping hosts with latest result
    hosts_result = await db.execute(select(PingHost).where(PingHost.enabled == True))
    hosts = hosts_result.scalars().all()

    host_stats = []
    now = datetime.utcnow()
    window = now - timedelta(hours=24)

    for host in hosts:
        # Latest result
        latest = await db.execute(
            select(PingResult)
            .where(PingResult.host_id == host.id)
            .order_by(PingResult.timestamp.desc())
            .limit(1)
        )
        latest_result = latest.scalar_one_or_none()

        # Uptime % in last 24h
        total = await db.execute(
            select(func.count()).where(
                PingResult.host_id == host.id,
                PingResult.timestamp >= window,
            )
        )
        success = await db.execute(
            select(func.count()).where(
                PingResult.host_id == host.id,
                PingResult.timestamp >= window,
                PingResult.success == True,
            )
        )
        total_count = total.scalar() or 0
        success_count = success.scalar() or 0
        uptime_pct = round((success_count / total_count * 100) if total_count > 0 else 0, 1)

        # Avg latency (24h)
        avg_lat = await db.execute(
            select(func.avg(PingResult.latency_ms)).where(
                PingResult.host_id == host.id,
                PingResult.timestamp >= window,
                PingResult.success == True,
            )
        )
        avg_latency = avg_lat.scalar()
        if avg_latency is not None:
            avg_latency = round(avg_latency, 2)

        host_stats.append({
            "host": host,
            "online": latest_result.success if latest_result else None,
            "latency": latest_result.latency_ms if latest_result else None,
            "uptime_pct": uptime_pct,
            "avg_latency": avg_latency,
            "last_check": latest_result.timestamp if latest_result else None,
        })

    online_count = sum(1 for s in host_stats if s["online"])
    offline_count = sum(1 for s in host_stats if s["online"] is False)

    # Proxmox clusters (just IDs/names for dashboard JS fetch)
    px_result = await db.execute(select(ProxmoxCluster).order_by(ProxmoxCluster.name))
    proxmox_clusters = px_result.scalars().all()

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "host_stats": host_stats,
        "online_count": online_count,
        "offline_count": offline_count,
        "total_count": len(host_stats),
        "proxmox_clusters": proxmox_clusters,
        "active_page": "dashboard",
    })
