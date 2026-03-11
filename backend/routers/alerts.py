import json
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from templating import templates
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import PingHost, PingResult, get_db
from models.incident import Incident
from models.integration import IntegrationConfig
from services import snapshot as snap_svc

router = APIRouter(prefix="/alerts")

_INTEGRATION_LABELS = {
    "proxmox": "Proxmox", "unifi": "UniFi", "unas": "UniFi NAS",
    "pihole": "Pi-hole", "adguard": "AdGuard", "portainer": "Portainer",
    "truenas": "TrueNAS", "synology": "Synology", "firewall": "Firewall",
    "hass": "Home Assistant", "gitea": "Gitea", "phpipam": "phpIPAM",
    "speedtest": "Speedtest", "ups": "UPS / NUT", "redfish": "Redfish",
}


@router.get("", response_class=HTMLResponse)
async def alerts_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    tab: str = Query("alerts", description="Active tab: alerts or incidents"),
    status: str = Query(None, description="Filter incidents by status"),
):
    alerts = []

    # ── Offline hosts ─────────────────────────────────────────────────────────
    hosts = (await db.execute(
        select(PingHost).where(PingHost.enabled == True, PingHost.maintenance == False)
    )).scalars().all()

    for host in hosts:
        latest = (await db.execute(
            select(PingResult)
            .where(PingResult.host_id == host.id)
            .order_by(PingResult.timestamp.desc())
            .limit(1)
        )).scalar_one_or_none()

        if latest and not latest.success:
            alerts.append({
                "severity": "critical",
                "category": "Host offline",
                "name": host.name,
                "detail": host.hostname,
                "url": f"/hosts/{host.id}",
                "time": latest.timestamp,
            })

        # SSL expiry warning
        if host.ssl_expiry_days is not None and host.ssl_expiry_days <= 30:
            sev = "critical" if host.ssl_expiry_days <= 7 else "warning"
            alerts.append({
                "severity": sev,
                "category": "SSL expiry",
                "name": host.name,
                "detail": f"Expires in {host.ssl_expiry_days} days",
                "url": f"/hosts/{host.id}",
                "time": None,
            })

    # ── Integration failures (generic tables only) ─────────────────────────
    all_configs = (await db.execute(select(IntegrationConfig))).scalars().all()
    all_snaps = await snap_svc.get_latest_batch_all(db)

    for cfg in all_configs:
        snap = all_snaps.get(cfg.type, {}).get(cfg.id)
        label = _INTEGRATION_LABELS.get(cfg.type, cfg.type)
        url = f"/integration/{cfg.type}/{cfg.id}" if cfg.type != "speedtest" else f"/integration/{cfg.type}"

        if snap and not snap.ok:
            alerts.append({
                "severity": "warning",
                "category": f"{label} error",
                "name": cfg.name,
                "detail": snap.error or "Connection error",
                "url": url,
                "time": snap.timestamp,
            })

        # UPS on battery check
        if cfg.type == "ups" and snap and snap.ok and snap.data_json:
            try:
                d = json.loads(snap.data_json)
                status = d.get("status", "").lower()
                if "onbatt" in status or "on battery" in status:
                    alerts.append({
                        "severity": "critical",
                        "category": "UPS on battery",
                        "name": cfg.name,
                        "detail": f"Status: {d.get('status', '?')} – Charge: {d.get('charge_pct', '?')}%",
                        "url": f"/integration/ups/{cfg.id}",
                        "time": snap.timestamp,
                    })
            except Exception:
                pass

    # Sort: critical first, then by time desc
    alerts.sort(key=lambda a: (0 if a["severity"] == "critical" else 1, -(a["time"].timestamp() if a["time"] else 0)))

    # ── Incidents ──────────────────────────────────────────────────────────────
    status_order = case(
        (Incident.status == "open", 0),
        (Incident.status == "acknowledged", 1),
        else_=2,
    )
    inc_query = select(Incident).order_by(status_order, Incident.updated_at.desc())
    if status:
        inc_query = inc_query.where(Incident.status == status)
    incidents = (await db.execute(inc_query)).scalars().all()

    inc_counts = {}
    for s in ("open", "acknowledged", "resolved"):
        c = (await db.execute(
            select(func.count(Incident.id)).where(Incident.status == s)
        )).scalar() or 0
        inc_counts[s] = c

    return templates.TemplateResponse("alerts.html", {
        "request": request,
        "alerts": alerts,
        "incidents": incidents,
        "inc_counts": inc_counts,
        "tab": tab,
        "f_status": status,
        "active_page": "alerts",
    })
