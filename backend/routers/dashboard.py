import json
from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import (
    PingHost, PingResult, ProxmoxCluster, ProxmoxSnapshot,
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
    get_db, get_setting, is_setup_complete,
)

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    if not await is_setup_complete(db):
        return RedirectResponse(url="/setup")

    now = datetime.utcnow()
    window_24h = now - timedelta(hours=24)

    # ── Global thresholds (read once, used throughout) ─────────────────────────
    global_latency_threshold = await get_setting(db, "latency_threshold_ms", "")
    global_latency_ms = int(global_latency_threshold) if global_latency_threshold.strip() else None
    px_cpu_threshold     = int(await get_setting(db, "proxmox_cpu_threshold", "85"))
    px_ram_pct_threshold = int(await get_setting(db, "proxmox_ram_threshold", "85"))
    px_disk_threshold    = int(await get_setting(db, "proxmox_disk_threshold", "90"))

    # ── Ping hosts ────────────────────────────────────────────────────────────
    hosts_result = await db.execute(select(PingHost).where(PingHost.enabled == True))
    hosts = hosts_result.scalars().all()

    window_2h = now - timedelta(hours=2)
    host_stats = []
    ping_alarms: list[dict] = []

    for host in hosts:
        latest_row = (await db.execute(
            select(PingResult)
            .where(PingResult.host_id == host.id)
            .order_by(PingResult.timestamp.desc())
            .limit(1)
        )).scalar_one_or_none()

        total_count = (await db.execute(
            select(func.count()).where(
                PingResult.host_id == host.id,
                PingResult.timestamp >= window_24h,
            )
        )).scalar() or 0

        success_count = (await db.execute(
            select(func.count()).where(
                PingResult.host_id == host.id,
                PingResult.timestamp >= window_24h,
                PingResult.success == True,
            )
        )).scalar() or 0

        avg_latency_raw = (await db.execute(
            select(func.avg(PingResult.latency_ms)).where(
                PingResult.host_id == host.id,
                PingResult.timestamp >= window_24h,
                PingResult.success == True,
            )
        )).scalar()

        # Sparkline: last 2h latency values (max 60 points)
        sparkline_rows = (await db.execute(
            select(PingResult)
            .where(PingResult.host_id == host.id, PingResult.timestamp >= window_2h)
            .order_by(PingResult.timestamp.asc())
            .limit(60)
        )).scalars().all()
        sparkline = [r.latency_ms if r.success else None for r in sparkline_rows]

        avg_latency = round(avg_latency_raw, 2) if avg_latency_raw is not None else None
        uptime_pct = round((success_count / total_count * 100) if total_count > 0 else 0, 1)

        # Latency threshold alarm (skip maintenance hosts)
        # Per-host threshold takes priority; fall back to global default
        effective_threshold = host.latency_threshold_ms if host.latency_threshold_ms is not None else global_latency_ms
        if (
            not host.maintenance
            and latest_row and latest_row.success
            and latest_row.latency_ms is not None
            and effective_threshold is not None
            and latest_row.latency_ms > effective_threshold
        ):
            ping_alarms.append({
                "name": host.name,
                "hostname": host.hostname,
                "latency": latest_row.latency_ms,
                "threshold": effective_threshold,
                "host_id": host.id,
            })

        host_stats.append({
            "host": host,
            "online": latest_row.success if latest_row else None,
            "latency": latest_row.latency_ms if latest_row else None,
            "uptime_pct": uptime_pct,
            "avg_latency": avg_latency,
            "last_check": latest_row.timestamp if latest_row else None,
            "sparkline": sparkline,
        })

    # Exclude maintenance hosts from counts and Top-10
    active_stats = [s for s in host_stats if not s["host"].maintenance]
    online_count  = sum(1 for s in active_stats if s["online"])
    offline_count = sum(1 for s in active_stats if s["online"] is False)

    # ── Top 10 Ping ───────────────────────────────────────────────────────────
    with_latency = [s for s in active_stats if s["avg_latency"] is not None]
    top_latency  = sorted(with_latency, key=lambda s: s["avg_latency"], reverse=True)[:10]
    top_downtime = sorted(active_stats, key=lambda s: s["uptime_pct"])[:10]

    # ── Proxmox clusters ──────────────────────────────────────────────────────
    px_result = await db.execute(select(ProxmoxCluster).order_by(ProxmoxCluster.name))
    proxmox_clusters = px_result.scalars().all()

    anomaly_threshold = float(await get_setting(db, "anomaly_threshold", "2.0"))

    all_guests: list[dict] = []
    anomalies:  list[dict] = []

    for cluster in proxmox_clusters:
        latest_snap = (await db.execute(
            select(ProxmoxSnapshot)
            .where(ProxmoxSnapshot.cluster_id == cluster.id, ProxmoxSnapshot.ok == True)
            .order_by(ProxmoxSnapshot.timestamp.desc())
            .limit(1)
        )).scalar_one_or_none()

        if not latest_snap:
            continue

        latest_data = json.loads(latest_snap.data_json)
        guests_now = latest_data.get("vms", []) + latest_data.get("containers", [])

        # Historical snapshots for anomaly baseline
        hist_snaps = (await db.execute(
            select(ProxmoxSnapshot)
            .where(
                ProxmoxSnapshot.cluster_id == cluster.id,
                ProxmoxSnapshot.ok == True,
                ProxmoxSnapshot.timestamp >= window_24h,
                ProxmoxSnapshot.timestamp < latest_snap.timestamp,
            )
        )).scalars().all()

        hist: dict[int, dict] = defaultdict(lambda: {"cpu": [], "mem": []})
        for snap in hist_snaps:
            for g in json.loads(snap.data_json).get("vms", []) + json.loads(snap.data_json).get("containers", []):
                gid = g.get("id")
                if gid is not None:
                    hist[gid]["cpu"].append(g.get("cpu_pct", 0))
                    hist[gid]["mem"].append(g.get("mem_used_gb", 0))

        for g in guests_now:
            gid = g.get("id")
            all_guests.append({**g, "cluster_name": cluster.name})
            cur_cpu  = g.get("cpu_pct", 0)
            cur_mem  = g.get("mem_used_gb", 0)
            mem_total = g.get("mem_total_gb", 0)
            cur_mem_pct = round(cur_mem / mem_total * 100, 1) if mem_total > 0 else 0

            # Absolute threshold checks (from settings)
            if cur_cpu >= px_cpu_threshold:
                anomalies.append({
                    "name": g["name"], "type": g["type"], "node": g["node"],
                    "cluster_name": cluster.name, "metric": "CPU",
                    "current": cur_cpu, "mean": px_cpu_threshold, "factor": None,
                })
            elif gid in hist and len(hist[gid]["cpu"]) >= 3:
                # Anomaly-detection fallback: multiplier above 24h average
                mean_cpu = sum(hist[gid]["cpu"]) / len(hist[gid]["cpu"])
                if cur_cpu > 15 and mean_cpu > 0 and cur_cpu > anomaly_threshold * mean_cpu:
                    anomalies.append({
                        "name": g["name"], "type": g["type"], "node": g["node"],
                        "cluster_name": cluster.name, "metric": "CPU",
                        "current": cur_cpu, "mean": round(mean_cpu, 1),
                        "factor": round(cur_cpu / mean_cpu, 1),
                    })

            if cur_mem_pct >= px_ram_pct_threshold:
                anomalies.append({
                    "name": g["name"], "type": g["type"], "node": g["node"],
                    "cluster_name": cluster.name, "metric": "RAM",
                    "current": round(cur_mem, 2), "mean": None,
                    "factor": f"{cur_mem_pct}%",
                })
            elif gid in hist and len(hist[gid]["mem"]) >= 3:
                mean_mem = sum(hist[gid]["mem"]) / len(hist[gid]["mem"])
                if cur_mem > 0.5 and mean_mem > 0 and cur_mem > anomaly_threshold * mean_mem:
                    anomalies.append({
                        "name": g["name"], "type": g["type"], "node": g["node"],
                        "cluster_name": cluster.name, "metric": "RAM",
                        "current": round(cur_mem, 2), "mean": round(mean_mem, 2),
                        "factor": round(cur_mem / mean_mem, 1),
                    })

            disk_pct = g.get("disk_pct", 0)
            if disk_pct >= px_disk_threshold:
                anomalies.append({
                    "name": g["name"], "type": g["type"], "node": g["node"],
                    "cluster_name": cluster.name, "metric": "Disk",
                    "current": disk_pct, "mean": None, "factor": None,
                })

    # Merge ping alarms into anomalies list
    for pa in ping_alarms:
        anomalies.append({
            "name": pa["name"], "type": "Host", "node": pa["hostname"],
            "cluster_name": "Ping", "metric": "Latency",
            "current": pa["latency"], "mean": pa["threshold"], "factor": None,
            "host_id": pa["host_id"],
        })

    running_guests = [g for g in all_guests if g.get("running")]
    top_cpu  = sorted(running_guests, key=lambda g: g.get("cpu_pct", 0), reverse=True)[:10]
    top_ram  = sorted(running_guests, key=lambda g: g.get("mem_used_gb", 0), reverse=True)[:10]
    top_disk = sorted(
        [g for g in running_guests if g.get("disk_total_gb", 0) > 0],
        key=lambda g: g.get("disk_pct", 0),
        reverse=True,
    )[:10]

    # Build name/hostname → PingHost.id map for linking Proxmox VMs to host objects
    all_ph = (await db.execute(select(PingHost))).scalars().all()
    ping_host_map: dict[str, int] = {}
    for h in all_ph:
        ping_host_map[h.hostname] = h.id
        ping_host_map.setdefault(h.name, h.id)

    # ── Integration health ─────────────────────────────────────────────────────
    integration_health = []
    for label, config_model, snap_model, snap_fk, url_prefix, color in [
        ("UniFi",         UnifiController,   UnifiSnapshot,     "controller_id", "/unifi",     "blue"),
        ("UniFi NAS",     UnasServer,        UnasSnapshot,      "server_id",     "/unas",      "cyan"),
        ("Pi-hole",       PiholeInstance,    PiholeSnapshot,    "instance_id",   "/pihole",    "red"),
        ("AdGuard",       AdguardInstance,   AdguardSnapshot,   "instance_id",   "/adguard",   "emerald"),
        ("Portainer",     PortainerInstance, PortainerSnapshot, "instance_id",   "/portainer", "teal"),
        ("TrueNAS",       TruenasServer,     TruenasSnapshot,   "server_id",     "/truenas",   "slate"),
        ("Synology",      SynologyServer,    SynologySnapshot,  "server_id",     "/synology",  "blue"),
        ("Firewall",      FirewallInstance,  FirewallSnapshot,  "instance_id",   "/firewall",  "orange"),
        ("Home Assistant",HassInstance,      HassSnapshot,      "instance_id",   "/hass",      "orange"),
        ("Gitea",         GiteaInstance,     GiteaSnapshot,     "instance_id",   "/gitea",     "green"),
        ("UPS / NUT",     NutInstance,       NutSnapshot,       "instance_id",   "/ups",       "yellow"),
        ("Redfish",       RedfishServer,     RedfishSnapshot,   "server_id",     "/redfish",   "purple"),
    ]:
        instances_result = await db.execute(select(config_model))
        instances = instances_result.scalars().all()
        if not instances:
            continue
        for inst in instances:
            snap = (await db.execute(
                select(snap_model)
                .where(getattr(snap_model, snap_fk) == inst.id)
                .order_by(snap_model.timestamp.desc())
                .limit(1)
            )).scalar_one_or_none()
            integration_health.append({
                "label": label,
                "name": inst.name,
                "url": f"{url_prefix}/{inst.id}",
                "color": color,
                "ok": snap.ok if snap else None,
                "error": snap.error if snap and not snap.ok else None,
                "cached_at": snap.timestamp if snap else None,
            })

    # Speedtest (single config, no per-instance page)
    st_configs = (await db.execute(select(SpeedtestConfig))).scalars().all()
    for st in st_configs:
        snap = (await db.execute(
            select(SpeedtestResult)
            .where(SpeedtestResult.config_id == st.id)
            .order_by(SpeedtestResult.timestamp.desc())
            .limit(1)
        )).scalar_one_or_none()
        integration_health.append({
            "label": "Speedtest",
            "name": st.name,
            "url": "/speedtest",
            "color": "blue",
            "ok": snap.ok if snap else None,
            "error": snap.error if snap and not snap.ok else None,
            "cached_at": snap.timestamp if snap else None,
        })

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "host_stats": host_stats,
        "online_count": online_count,
        "offline_count": offline_count,
        "total_count": len(active_stats),
        "proxmox_clusters": proxmox_clusters,
        "top_latency": top_latency,
        "top_downtime": top_downtime,
        "top_cpu": top_cpu,
        "top_ram": top_ram,
        "top_disk": top_disk,
        "ping_host_map": ping_host_map,
        "anomalies": anomalies,
        "integration_health": integration_health,
        "active_page": "dashboard",
    })
