import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import (
    PingHost, PingResult,
    UnifiController, UnifiSnapshot,
    UnasServer, UnasSnapshot,
    PiholeInstance, PiholeSnapshot,
    AdguardInstance, AdguardSnapshot,
    PortainerInstance, PortainerSnapshot,
    TruenasServer, TruenasSnapshot,
    SynologyServer, SynologySnapshot,
    FirewallInstance, FirewallSnapshot,
    HassInstance, HassSnapshot,
    GiteaInstance, GiteaSnapshot,
    NutInstance, NutSnapshot,
    RedfishServer, RedfishSnapshot,
    SpeedtestConfig, SpeedtestResult,
    ProxmoxCluster, ProxmoxSnapshot,
    get_db,
)

router = APIRouter(prefix="/alerts")
templates = Jinja2Templates(directory="templates")


@router.get("", response_class=HTMLResponse)
async def alerts_page(request: Request, db: AsyncSession = Depends(get_db)):
    now = datetime.utcnow()
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
                "url": f"/ping/{host.id}",
                "time": latest.timestamp,
            })

        # SSL expiry warning
        if host.ssl_expiry_days is not None and host.ssl_expiry_days <= 30:
            sev = "critical" if host.ssl_expiry_days <= 7 else "warning"
            alerts.append({
                "severity": sev,
                "category": "SSL expiry",
                "name": host.name,
                "detail": f"Läuft in {host.ssl_expiry_days} Tagen ab",
                "url": f"/ping/{host.id}",
                "time": None,
            })

    # ── Integration failures ──────────────────────────────────────────────────
    for label, config_model, snap_model, snap_fk, url_prefix in [
        ("Proxmox",       ProxmoxCluster,   ProxmoxSnapshot,   "cluster_id",  "/proxmox"),
        ("UniFi",         UnifiController,  UnifiSnapshot,     "controller_id","/unifi"),
        ("UniFi NAS",     UnasServer,       UnasSnapshot,      "server_id",   "/unas"),
        ("Pi-hole",       PiholeInstance,   PiholeSnapshot,    "instance_id", "/pihole"),
        ("AdGuard",       AdguardInstance,  AdguardSnapshot,   "instance_id", "/adguard"),
        ("Portainer",     PortainerInstance,PortainerSnapshot, "instance_id", "/portainer"),
        ("TrueNAS",       TruenasServer,    TruenasSnapshot,   "server_id",   "/truenas"),
        ("Synology",      SynologyServer,   SynologySnapshot,  "server_id",   "/synology"),
        ("Firewall",      FirewallInstance, FirewallSnapshot,  "instance_id", "/firewall"),
        ("Home Assistant",HassInstance,     HassSnapshot,      "instance_id", "/hass"),
        ("Gitea",         GiteaInstance,    GiteaSnapshot,     "instance_id", "/gitea"),
        ("UPS / NUT",     NutInstance,      NutSnapshot,       "instance_id", "/ups"),
        ("Redfish",       RedfishServer,    RedfishSnapshot,   "server_id",   "/redfish"),
    ]:
        instances = (await db.execute(select(config_model))).scalars().all()
        for inst in instances:
            snap = (await db.execute(
                select(snap_model)
                .where(getattr(snap_model, snap_fk) == inst.id)
                .order_by(snap_model.timestamp.desc())
                .limit(1)
            )).scalar_one_or_none()

            if snap and not snap.ok:
                alerts.append({
                    "severity": "warning",
                    "category": f"{label} Fehler",
                    "name": inst.name,
                    "detail": snap.error or "Verbindungsfehler",
                    "url": f"{url_prefix}/{inst.id}",
                    "time": snap.timestamp,
                })
            elif snap is None and instances:
                # Configured but no data yet — not an alert, skip
                pass

    # Speedtest
    for st in (await db.execute(select(SpeedtestConfig))).scalars().all():
        snap = (await db.execute(
            select(SpeedtestResult)
            .where(SpeedtestResult.config_id == st.id)
            .order_by(SpeedtestResult.timestamp.desc())
            .limit(1)
        )).scalar_one_or_none()
        if snap and not snap.ok:
            alerts.append({
                "severity": "warning",
                "category": "Speedtest Fehler",
                "name": st.name,
                "detail": snap.error or "Test fehlgeschlagen",
                "url": "/speedtest",
                "time": snap.timestamp,
            })

    # ── UPS on battery ────────────────────────────────────────────────────────
    for nut in (await db.execute(select(NutInstance))).scalars().all():
        snap = (await db.execute(
            select(NutSnapshot)
            .where(NutSnapshot.instance_id == nut.id, NutSnapshot.ok == True)
            .order_by(NutSnapshot.timestamp.desc())
            .limit(1)
        )).scalar_one_or_none()
        if snap:
            try:
                d = json.loads(snap.data_json)
                status = d.get("status", "").lower()
                if "onbatt" in status or "on battery" in status:
                    alerts.append({
                        "severity": "critical",
                        "category": "UPS auf Batterie",
                        "name": nut.name,
                        "detail": f"Status: {d.get('status', '?')} – Ladung: {d.get('charge_pct', '?')}%",
                        "url": f"/ups/{nut.id}",
                        "time": snap.timestamp,
                    })
            except Exception:
                pass

    # Sort: critical first, then by time desc
    alerts.sort(key=lambda a: (0 if a["severity"] == "critical" else 1, -(a["time"].timestamp() if a["time"] else 0)))

    return templates.TemplateResponse("alerts.html", {
        "request": request,
        "alerts": alerts,
        "active_page": "alerts",
    })
