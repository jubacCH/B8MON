import json
from collections import defaultdict
from datetime import datetime, timedelta

# Stores last dashboard API timing for /system/status display
from services._sampling import select_sample_indices

_last_perf: dict = {}

# ── Anomaly baseline sampling ────────────────────────────────────────────────
#
# The baseline used to be built from every proxmox snapshot in the window: 2830
# rows and 140 MB of JSON on production, loaded and parsed on every dashboard
# request, which was ~1.5 s of a ~2.1 s response.
#
# Mean and standard deviation converge long before 2830 points. What does need
# to be exact is the tail: the sustained-anomaly check reads the last few values
# and they have to be real consecutive samples, not thinned-out ones.

BASELINE_SAMPLE_BUDGET = 120
BASELINE_KEEP_NEWEST = 6


def select_baseline_indices(total: int, budget: int, keep_newest: int) -> list[int]:
    """See :func:`services._sampling.select_sample_indices`."""
    return select_sample_indices(total, budget, keep_newest)



from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from templating import localtime
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import (
    PingHost,
    get_db, get_setting, set_setting, is_setup_complete,
)
from models.integration import IntegrationConfig, Snapshot
from integrations import get_meta as _int_meta
from services import integration as int_svc
from services import snapshot as snap_svc
from services import predictions as pred_svc
from services import health as health_svc

router = APIRouter()


async def _predict_agent_disks(db, days_back: int = 14) -> dict[str, dict]:
    """Predict disk-full for agent disks using historical agent_metrics data."""
    from models.agent import Agent
    from services.predictions import _linear_predict
    from services.clickhouse_client import get_agent_history

    predictions: dict[str, dict] = {}

    result = await db.execute(select(Agent).where(Agent.enabled == True))
    agents = result.scalars().all()

    for agent in agents:
        snapshots = await get_agent_history(agent.id, limit=10000, hours=days_back * 24)
        if len(snapshots) < 3:
            continue

        # Build time-series per mount: {mount: [(epoch, pct), ...]}
        mount_series: dict[str, list[tuple[float, float]]] = {}
        for snap in snapshots:
            data_json = snap.get("data_json")
            if not data_json:
                continue
            try:
                data = json.loads(data_json)
            except (json.JSONDecodeError, TypeError):
                continue
            ts_val = snap.get("timestamp")
            if not isinstance(ts_val, datetime):
                continue
            ts = ts_val.timestamp()
            for disk in data.get("disks", []):
                mount = disk.get("mount", "/")
                pct = disk.get("pct")
                total = disk.get("total_gb", 0)
                if pct is not None and total > 0.5:
                    mount_series.setdefault(mount, []).append((ts, float(pct)))

        for mount, series in mount_series.items():
            if len(series) < 3:
                continue
            pred = _linear_predict(series)
            if pred is None:
                continue
            key = f"agent-{agent.id}:{mount}"
            predictions[key] = {
                "current_pct": pred["current"],
                "trend_pct_per_day": pred["slope_per_day"],
                "days_until_full": pred["days_until_full"],
                "confidence": pred["r_squared"],
            }

    return predictions


# ── Default dashboard widget layout (gridstack 12-col, cellHeight=40px) ──────
VALID_WIDGET_IDS = {
    "integrations", "syslog", "gravity", "offline", "hosts", "proxmox", "top10",
    "speedtest", "heatmap", "storage", "containers", "ups", "ssl", "alerts",
    "uptime", "clock", "quickstats", "alert-trends",
}
DEFAULT_LAYOUT = [
    # Row 0 – Gravity Well + sidebar (6+6)
    {"id": "gravity",       "x": 0, "y": 0,  "w": 6,  "h": 12},
    {"id": "integrations",  "x": 6, "y": 0,  "w": 6,  "h": 5},
    {"id": "syslog",        "x": 6, "y": 5,  "w": 6,  "h": 5},
    {"id": "alerts",        "x": 6, "y": 10, "w": 6,  "h": 5},
    # Row 1 – Offline + Hosts (6+6 / 12)
    {"id": "offline",       "x": 0, "y": 15, "w": 6,  "h": 5},
    {"id": "hosts",         "x": 6, "y": 15, "w": 6,  "h": 8},
    # Row 2 – Analytics (6+6)
    {"id": "heatmap",       "x": 0, "y": 23, "w": 6,  "h": 13},
    {"id": "uptime",        "x": 6, "y": 23, "w": 6,  "h": 13},
    # Row 3 – Top 10 resource tables (full width)
    {"id": "top10",         "x": 0, "y": 36, "w": 12, "h": 24},
]


# ── Helper: build integration_health from generic tables ─────────────────

    # Integration metadata is now sourced from the plugin registry (integrations.get_meta)


@router.get("/api/dashboard")
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    import time as _time; _t0 = _time.time(); _checkpoints = []
    def _cp(label): _checkpoints.append((label, (_time.time()-_t0)*1000))
    if not await is_setup_complete(db):
        return RedirectResponse(url="/setup")

    now = datetime.utcnow()
    window_24h = now - timedelta(hours=24)

    _cp("init")
    # ── Global thresholds (read once, used throughout) ─────────────────────────
    global_latency_threshold = await get_setting(db, "latency_threshold_ms", "")
    global_latency_ms = int(global_latency_threshold) if global_latency_threshold.strip() else None
    px_cpu_threshold     = int(await get_setting(db, "proxmox_cpu_threshold", "85"))
    px_ram_pct_threshold = int(await get_setting(db, "proxmox_ram_threshold", "85"))
    px_disk_threshold    = int(await get_setting(db, "proxmox_disk_threshold", "90"))

    _cp("ping")
    # ── Ping hosts (batch queries instead of N+1) ───────────────────────────
    hosts_result = await db.execute(select(PingHost).where(PingHost.enabled == True))
    hosts = hosts_result.scalars().all()
    host_ids = [h.id for h in hosts]

    window_2h = now - timedelta(hours=2)
    host_stats = []
    ping_alarms: list[dict] = []

    # All time-series ping data comes from ClickHouse.
    from services.clickhouse_client import (
        get_latest_ping_per_host,
        get_ping_uptime,
        query as ch_query,
    )

    latest_by_host: dict[int, dict] = {}
    stats_by_host: dict[int, dict] = {}
    sparklines_by_host: dict[int, list] = defaultdict(list)

    if host_ids:
        latest_by_host = await get_latest_ping_per_host(host_ids)

        # 24h aggregation in one query
        uptime_24h = await get_ping_uptime(host_ids, hours=24)
        for hid, stats in uptime_24h.items():
            stats_by_host[hid] = {
                "total":   stats["total"],
                "success": stats["ok"],
                "avg_lat": stats["avg_latency"],
            }

        # Sparkline (last 2h) — single CH query
        spark_rows = await ch_query(
            "SELECT host_id, success, latency_ms "
            "FROM ping_checks "
            "WHERE host_id IN ({hids:Array(UInt32)}) "
            "  AND timestamp >= {since:DateTime64(3)} "
            "ORDER BY host_id, timestamp ASC",
            {"hids": host_ids, "since": window_2h},
        )
        for row in spark_rows:
            hid = int(row["host_id"])
            sparklines_by_host[hid].append(
                float(row["latency_ms"]) if row.get("success") and row.get("latency_ms") is not None else None
            )
        for hid in sparklines_by_host:
            sparklines_by_host[hid] = sparklines_by_host[hid][-60:]

    for host in hosts:
        latest_row = latest_by_host.get(host.id)
        latest_success = bool(latest_row.get("success")) if latest_row else None
        latest_latency = latest_row.get("latency_ms") if latest_row else None
        latest_ts = latest_row.get("timestamp") if latest_row else None

        st = stats_by_host.get(host.id, {"total": 0, "success": 0, "avg_lat": None})
        total_count = st["total"]
        success_count = st["success"]
        avg_latency_raw = st["avg_lat"]
        sparkline = sparklines_by_host.get(host.id, [])

        avg_latency = round(avg_latency_raw, 2) if avg_latency_raw is not None else None
        uptime_pct = round((success_count / total_count * 100) if total_count > 0 else 0, 1)

        # Latency threshold alarm (skip maintenance hosts)
        effective_threshold = host.latency_threshold_ms if host.latency_threshold_ms is not None else global_latency_ms
        if (
            not host.maintenance
            and latest_success
            and latest_latency is not None
            and effective_threshold is not None
            and latest_latency > effective_threshold
        ):
            ping_alarms.append({
                "name": host.name,
                "hostname": host.hostname,
                "latency": latest_latency,
                "threshold": effective_threshold,
                "host_id": host.id,
            })

        host_stats.append({
            "host": host,
            "online": latest_success,
            "latency": latest_latency,
            "uptime_pct": uptime_pct,
            "avg_latency": avg_latency,
            "last_check": latest_ts,
            "sparkline": sparkline,
            "effective_threshold": effective_threshold,
            "health_score": 0.0,  # computed later with all metrics
        })

    # Exclude maintenance hosts from counts and Top-10
    active_stats = [s for s in host_stats if not s["host"].maintenance]
    online_count  = sum(1 for s in active_stats if s["online"])
    offline_count = sum(1 for s in active_stats if s["online"] is False)

    _cp("top10")
    # ── Top 10 Ping ───────────────────────────────────────────────────────────
    with_latency = [s for s in active_stats if s["avg_latency"] is not None]
    top_latency  = sorted(with_latency, key=lambda s: s["avg_latency"], reverse=True)[:10]
    top_downtime = sorted(active_stats, key=lambda s: s["uptime_pct"])[:10]

    # ── Pre-fetch all integration data ────────────────────────────────────────
    all_configs_result = await db.execute(
        select(IntegrationConfig).order_by(IntegrationConfig.type, IntegrationConfig.name)
    )
    all_configs = all_configs_result.scalars().all()
    all_snaps_cache = await snap_svc.get_latest_batch_all(db)

    _cp("proxmox")
    # ── Proxmox clusters ──────────────────────────────────────────────────────
    px_configs = [c for c in all_configs if c.type == "proxmox"]

    class _PxCluster:
        def __init__(self, cfg):
            self.id = cfg.id
            self.name = cfg.name
            try:
                d = int_svc.decrypt_config(cfg.config_json)
                self.host = d.get("host", "")
            except Exception:
                self.host = ""
    proxmox_clusters = [_PxCluster(c) for c in px_configs]

    anomaly_threshold = float(await get_setting(db, "anomaly_threshold", "2.0"))

    all_guests: list[dict] = []
    anomalies:  list[dict] = []
    warnings:   list[dict] = []

    px_snapshots = await snap_svc.get_latest_batch(db, "proxmox")

    for cluster in proxmox_clusters:
        latest_snap = px_snapshots.get(cluster.id)
        if not latest_snap or not latest_snap.ok or not latest_snap.data_json:
            continue
        latest_data = json.loads(latest_snap.data_json)

        guests_now = latest_data.get("vms", []) + latest_data.get("containers", [])

        # Historical snapshots for anomaly baseline.
        #
        # Two queries on purpose: the ids alone are cheap, while data_json runs
        # to ~100 kB per row. Loading the whole window meant 2830 rows and
        # 140 MB of JSON parsed per request. Only the sampled subset is fetched
        # with its payload.
        hist_ids = [
            row[0] for row in (await db.execute(
                select(Snapshot.id)
                .where(
                    Snapshot.entity_type == "proxmox",
                    Snapshot.entity_id == cluster.id,
                    Snapshot.ok == True,
                    Snapshot.timestamp >= window_24h,
                    Snapshot.timestamp < latest_snap.timestamp,
                )
                .order_by(Snapshot.timestamp)
            )).all()
        ]

        wanted = [
            hist_ids[i] for i in select_baseline_indices(
                len(hist_ids), BASELINE_SAMPLE_BUDGET, BASELINE_KEEP_NEWEST
            )
        ]

        hist_rows = (await db.execute(
            select(Snapshot.data_json)
            .where(Snapshot.id.in_(wanted))
            .order_by(Snapshot.timestamp)
        )).all() if wanted else []

        # Build per-guest time series from the sampled snapshots
        hist: dict[int, dict] = defaultdict(lambda: {"cpu": [], "mem": []})
        for row in hist_rows:
            snap_data = json.loads(row[0])
            for g in snap_data.get("vms", []) + snap_data.get("containers", []):
                gid = g.get("id")
                if gid is not None:
                    hist[gid]["cpu"].append(g.get("cpu_pct", 0))
                    hist[gid]["mem"].append(g.get("mem_used_gb", 0))

        # Stddev floors to prevent micro-fluctuations from triggering anomalies
        CPU_STD_FLOOR = 3.0
        MEM_STD_FLOOR = 0.3
        SUSTAINED_WINDOW = 5
        SUSTAINED_MIN = 3

        for g in guests_now:
            gid = g.get("id")
            all_guests.append({**g, "cluster_name": cluster.name})
            cur_cpu  = g.get("cpu_pct", 0)
            cur_mem  = g.get("mem_used_gb", 0)
            mem_total = g.get("mem_total_gb", 0)
            cur_mem_pct = round(cur_mem / mem_total * 100, 1) if mem_total > 0 else 0

            # ── Statistical anomalies (sustained deviation from baseline) ──
            if gid in hist and len(hist[gid]["cpu"]) >= 6:
                all_cpu = hist[gid]["cpu"]
                mean_cpu = sum(all_cpu) / len(all_cpu)
                std_cpu = max((sum((x - mean_cpu) ** 2 for x in all_cpu) / len(all_cpu)) ** 0.5, CPU_STD_FLOOR)
                if cur_cpu > 10 and mean_cpu > 0:
                    recent_values = all_cpu[-SUSTAINED_WINDOW:] + [cur_cpu]
                    anomalous_count = sum(1 for v in recent_values if (v - mean_cpu) / std_cpu >= anomaly_threshold)
                    if anomalous_count >= SUSTAINED_MIN:
                        z_score = (cur_cpu - mean_cpu) / std_cpu
                        severity = round(z_score * (anomalous_count / len(recent_values)), 1)
                        anomalies.append({
                            "name": g["name"], "type": g["type"], "node": g["node"],
                            "cluster_name": cluster.name, "metric": "CPU",
                            "current": cur_cpu, "mean": round(mean_cpu, 1),
                            "factor": round(z_score, 1),
                            "sustained": anomalous_count,
                            "severity": severity,
                        })

            if gid in hist and len(hist[gid]["mem"]) >= 6:
                all_mem = hist[gid]["mem"]
                mean_mem = sum(all_mem) / len(all_mem)
                std_mem = max((sum((x - mean_mem) ** 2 for x in all_mem) / len(all_mem)) ** 0.5, MEM_STD_FLOOR)
                if cur_mem > 0.5 and mean_mem > 0:
                    recent_values = all_mem[-SUSTAINED_WINDOW:] + [cur_mem]
                    anomalous_count = sum(1 for v in recent_values if (v - mean_mem) / std_mem >= anomaly_threshold)
                    if anomalous_count >= SUSTAINED_MIN:
                        z_score = (cur_mem - mean_mem) / std_mem
                        severity = round(z_score * (anomalous_count / len(recent_values)), 1)
                        anomalies.append({
                            "name": g["name"], "type": g["type"], "node": g["node"],
                            "cluster_name": cluster.name, "metric": "RAM",
                            "current": round(cur_mem, 2), "mean": round(mean_mem, 2),
                            "factor": round(z_score, 1),
                            "sustained": anomalous_count,
                            "severity": severity,
                        })

            # ── Absolute threshold warnings (persistent resource pressure) ──
            if cur_cpu >= px_cpu_threshold:
                warnings.append({
                    "name": g["name"], "type": g["type"], "node": g["node"],
                    "cluster_name": cluster.name, "metric": "CPU",
                    "current": cur_cpu, "threshold": px_cpu_threshold,
                })
            if cur_mem_pct >= px_ram_pct_threshold:
                warnings.append({
                    "name": g["name"], "type": g["type"], "node": g["node"],
                    "cluster_name": cluster.name, "metric": "RAM",
                    "current": cur_mem_pct, "threshold": px_ram_pct_threshold,
                })
            disk_pct = g.get("disk_pct", 0)
            if disk_pct >= px_disk_threshold:
                warnings.append({
                    "name": g["name"], "type": g["type"], "node": g["node"],
                    "cluster_name": cluster.name, "metric": "Disk",
                    "current": disk_pct, "threshold": px_disk_threshold,
                })

    # Merge ping alarms into anomalies list
    for pa in ping_alarms:
        anomalies.append({
            "name": pa["name"], "type": "Host", "node": pa["hostname"],
            "cluster_name": "Ping", "metric": "Latency",
            "current": pa["latency"], "mean": pa["threshold"], "factor": None,
            "host_id": pa["host_id"],
            "sustained": None, "severity": 99,
        })

    # Sort anomalies by severity (highest first)
    anomalies.sort(key=lambda a: a.get("severity", 0), reverse=True)

    running_guests = [g for g in all_guests if g.get("running")]
    top_cpu  = sorted(running_guests, key=lambda g: g.get("cpu_pct", 0), reverse=True)[:10]
    top_ram  = sorted(running_guests, key=lambda g: g.get("mem_used_gb", 0), reverse=True)[:10]
    top_disk = sorted(
        [g for g in running_guests if g.get("disk_total_gb", 0) > 0],
        key=lambda g: g.get("disk_pct", 0),
        reverse=True,
    )[:10]

    # Build name/hostname -> PingHost.id map for linking Proxmox VMs to host objects
    all_ph = (await db.execute(select(PingHost))).scalars().all()
    ping_host_map: dict[str, int] = {}
    for h in all_ph:
        ping_host_map[h.hostname] = h.id
        ping_host_map.setdefault(h.name, h.id)
        ping_host_map.setdefault(h.hostname.lower(), h.id)
        ping_host_map.setdefault(h.name.lower(), h.id)
        raw = h.hostname
        for pfx in ("https://", "http://"):
            if raw.startswith(pfx):
                raw = raw[len(pfx):]
                break
        short_host = raw.split("/")[0].split(":")[0]
        ping_host_map.setdefault(short_host, h.id)
        ping_host_map.setdefault(short_host.lower(), h.id)
        if "." in short_host:
            ping_host_map.setdefault(short_host.split(".")[0].lower(), h.id)

    _cp("topology")
    # ── Build topology tree ──────────────────────────────────────────────────
    topology: dict[int, int | None] = {}
    host_by_id = {h.id: h for h in all_ph}

    for h in all_ph:
        topology[h.id] = getattr(h, 'parent_id', None)

    # Auto-detect Proxmox VM/LXC → Proxmox node relationships
    px_node_names: set[str] = set()
    for cluster in proxmox_clusters:
        snap = px_snapshots.get(cluster.id)
        if not snap or not snap.ok or not snap.data_json:
            continue
        d = json.loads(snap.data_json)
        for node_info in d.get("nodes", []):
            px_node_names.add(node_info.get("node", ""))
        for g in d.get("vms", []) + d.get("containers", []):
            guest_name = (g.get("name") or "").strip()
            node_name = (g.get("node") or "").strip()
            if not guest_name or not node_name:
                continue
            guest_ph_id = (ping_host_map.get(guest_name)
                           or ping_host_map.get(guest_name.lower()))
            node_ph_id = (ping_host_map.get(node_name)
                          or ping_host_map.get(node_name.lower()))
            if guest_ph_id and node_ph_id and guest_ph_id != node_ph_id:
                if topology.get(guest_ph_id) is None:
                    topology[guest_ph_id] = node_ph_id

    # Auto-detect UniFi device hierarchy (Gateway → Switch → AP)
    ip_to_ph: dict[str, int] = {}
    for h in all_ph:
        raw = h.hostname
        for pfx in ("https://", "http://"):
            if raw.startswith(pfx):
                raw = raw[len(pfx):]
                break
        raw = raw.split("/")[0].split(":")[0]
        ip_to_ph[raw] = h.id
        ip_to_ph[h.name] = h.id

    unifi_configs = [c for c in all_configs if c.type == "unifi"]
    if unifi_configs:
        unifi_snaps = await snap_svc.get_latest_batch(db, "unifi")
        for ucfg in unifi_configs:
            usnap = unifi_snaps.get(ucfg.id)
            if not usnap or not usnap.ok or not usnap.data_json:
                continue
            ud = json.loads(usnap.data_json)
            devices = ud.get("devices", [])
            gw_ph_id = None
            sw_ph_ids: list[int] = []
            ap_ph_ids: list[int] = []
            for dev in devices:
                dev_ip = (dev.get("ip") or "").strip()
                dev_name = (dev.get("name") or "").strip()
                dtype = dev.get("type", "")
                ph_id = ip_to_ph.get(dev_ip) or ip_to_ph.get(dev_name) or ping_host_map.get(dev_name)
                if not ph_id:
                    continue
                if dtype in ("ugw", "usg", "udm", "udmpro", "uxg"):
                    gw_ph_id = ph_id
                elif dtype == "usw":
                    sw_ph_ids.append(ph_id)
                elif dtype == "uap":
                    ap_ph_ids.append(ph_id)
            if gw_ph_id:
                for sw_id in sw_ph_ids:
                    if topology.get(sw_id) is None:
                        topology[sw_id] = gw_ph_id
                parent_for_ap = sw_ph_ids[0] if sw_ph_ids else gw_ph_id
                for ap_id in ap_ph_ids:
                    if topology.get(ap_id) is None:
                        topology[ap_id] = parent_for_ap

    _cp("int_health")
    # ── Integration health ──────────────────────────────────────────────────
    integration_health = []
    non_px_configs = [c for c in all_configs if c.type != "proxmox"]

    for cfg in non_px_configs:
        meta = _int_meta(cfg.type)
        snap = all_snaps_cache.get(cfg.type, {}).get(cfg.id)
        url = f"{meta['url_prefix']}/{cfg.id}" if not meta.get("single_instance") else meta["url_prefix"]
        integration_health.append({
            "config_id": cfg.id,
            "label": meta["label"],
            "name": cfg.name,
            "url": url,
            "color": meta["color"],
            "ok": snap.ok if snap else None,
            "error": snap.error if snap and not snap.ok else None,
            "cached_at": snap.timestamp if snap else None,
        })

    _cp("health_scores")
    # ── Integration health scores ─────────────────────────────────────────
    try:
        int_health_scores = await health_svc.compute_integration_health(db)
        for ih in integration_health:
            cfg_id = ih.get("config_id")
            if cfg_id and cfg_id in int_health_scores:
                ih["health_score"] = int_health_scores[cfg_id]
    except Exception:
        pass

    # Active incidents
    from models.incident import Incident, IncidentEvent
    active_incidents = (await db.execute(
        select(Incident)
        .where(Incident.status.in_(["open", "acknowledged"]))
        .order_by(Incident.created_at.desc())
        .limit(5)
    )).scalars().all()

    _cp("syslog")
    # ── Syslog stats (last 24h) ──────────────────────────────────────────────
    from models.syslog import SEVERITY_LABELS
    from services.clickhouse_client import query as ch_query, query_scalar as ch_scalar
    syslog_stats = {
        "total": 0, "by_severity": {}, "top_sources": [], "error_rate_1h": 0,
        "error_trend": [], "errors_by_host": [], "recent_critical": [],
    }
    try:
        syslog_stats["total"] = int(await ch_scalar(
            "SELECT count() FROM syslog_messages WHERE timestamp >= {t:DateTime64(3)}",
            {"t": window_24h},
        ) or 0)

        sev_rows = await ch_query(
            "SELECT severity, count() AS cnt FROM syslog_messages WHERE timestamp >= {t:DateTime64(3)} GROUP BY severity ORDER BY severity",
            {"t": window_24h},
        )
        syslog_stats["by_severity"] = {
            r["severity"]: {"count": r["cnt"], "label": SEVERITY_LABELS.get(r["severity"], f"Sev {r['severity']}")}
            for r in sev_rows if r["severity"] is not None
        }

        src_rows = await ch_query(
            "SELECT source_ip, count() AS cnt FROM syslog_messages WHERE timestamp >= {t:DateTime64(3)} GROUP BY source_ip ORDER BY cnt DESC LIMIT 5",
            {"t": window_24h},
        )
        syslog_stats["top_sources"] = [{"ip": r["source_ip"], "count": r["cnt"]} for r in src_rows]

        syslog_stats["error_rate_1h"] = int(await ch_scalar(
            "SELECT count() FROM syslog_messages WHERE severity <= 3 AND timestamp >= {t:DateTime64(3)}",
            {"t": now - timedelta(hours=1)},
        ) or 0)

        # Error trend: hourly buckets for 24h (errors + warnings)
        trend_rows = await ch_query(
            """SELECT toStartOfHour(timestamp) AS hour,
                      countIf(severity <= 3) AS errors,
                      countIf(severity = 4) AS warnings
               FROM syslog_messages
               WHERE timestamp >= {t:DateTime64(3)}
               GROUP BY hour ORDER BY hour""",
            {"t": window_24h},
        )
        syslog_stats["error_trend"] = [
            {"hour": localtime(r["hour"], "%H:%M"), "errors": r["errors"], "warnings": r["warnings"]}
            for r in trend_rows
        ]

        # Errors by host: top 5 hosts with most errors in 24h
        host_err_rows = await ch_query(
            """SELECT coalesce(nullIf(hostname, ''), source_ip) AS host,
                      count() AS cnt
               FROM syslog_messages
               WHERE severity <= 3 AND timestamp >= {t:DateTime64(3)}
               GROUP BY host ORDER BY cnt DESC LIMIT 5""",
            {"t": window_24h},
        )
        syslog_stats["errors_by_host"] = [{"host": r["host"], "count": r["cnt"]} for r in host_err_rows]

        # Recent critical messages (severity 0-2: emergency, alert, critical)
        crit_rows = await ch_query(
            """SELECT timestamp, severity, coalesce(nullIf(hostname, ''), source_ip) AS host,
                      substring(message, 1, 120) AS msg
               FROM syslog_messages
               WHERE severity <= 2 AND timestamp >= {t:DateTime64(3)}
               ORDER BY timestamp DESC LIMIT 5""",
            {"t": window_24h},
        )
        syslog_stats["recent_critical"] = [
            {"time": localtime(r["timestamp"], "%H:%M"), "severity": r["severity"],
             "host": r["host"], "message": r["msg"]}
            for r in crit_rows
        ]
    except Exception:
        pass

    # ── Speedtest (latest + 24h history) ─────────────────────────────────────
    speedtest_data = None
    speedtest_history = []
    try:
        st_configs = [c for c in all_configs if c.type == "speedtest"]
        if st_configs:
            st_snap = all_snaps_cache.get("speedtest", {}).get(st_configs[0].id)
            if st_snap and st_snap.ok and st_snap.data_json:
                st_d = json.loads(st_snap.data_json)
                speedtest_data = {
                    "download_mbps": st_d.get("download_mbps", 0),
                    "upload_mbps": st_d.get("upload_mbps", 0),
                    "ping_ms": st_d.get("ping_ms", 0),
                    "server_name": st_d.get("server_name", ""),
                    "timestamp": localtime(st_snap.timestamp, "%d.%m %H:%M") if st_snap.timestamp else "",
                }
                hist_rows = (await db.execute(
                    select(Snapshot)
                    .where(Snapshot.entity_type == "speedtest", Snapshot.entity_id == st_configs[0].id,
                           Snapshot.ok == True, Snapshot.timestamp >= window_24h)
                    .order_by(Snapshot.timestamp.asc())
                )).scalars().all()
                for hr in hist_rows:
                    hd = json.loads(hr.data_json)
                    speedtest_history.append({
                        "download_mbps": hd.get("download_mbps", 0),
                        "upload_mbps": hd.get("upload_mbps", 0),
                    })

        # Fallback: extract speedtest data from UniFi integration snapshots
        if not speedtest_data:
            unifi_configs = [c for c in all_configs if c.type == "unifi"]
            for ucfg in unifi_configs:
                u_snap = all_snaps_cache.get("unifi", {}).get(ucfg.id)
                if not u_snap or not u_snap.ok or not u_snap.data_json:
                    continue
                u_d = json.loads(u_snap.data_json)
                st_latest = u_d.get("speedtest_latest")
                if st_latest:
                    speedtest_data = {
                        "download_mbps": st_latest.get("download_mbps", 0),
                        "upload_mbps": st_latest.get("upload_mbps", 0),
                        "ping_ms": st_latest.get("latency_ms", 0),
                        "server_name": "UniFi Gateway",
                        "timestamp": st_latest.get("timestamp", ""),
                    }
                    # Use embedded speedtest history from the UniFi snapshot
                    for st_entry in u_d.get("speedtest", []):
                        speedtest_history.append({
                            "download_mbps": st_entry.get("download_mbps", 0),
                            "upload_mbps": st_entry.get("upload_mbps", 0),
                        })
                    break
    except Exception:
        pass

    # ── Storage pools (all sources) ─────────────────────────────────────────
    storage_pools = []

    # Integration pools: TrueNAS, Synology, UNAS
    try:
        for stype in ("truenas", "synology", "unas"):
            type_snaps = all_snaps_cache.get(stype, {})
            type_configs = [c for c in all_configs if c.type == stype]
            meta = _int_meta(stype)
            for cfg in type_configs:
                snap = type_snaps.get(cfg.id)
                if not snap or not snap.ok or not snap.data_json:
                    continue
                sd = json.loads(snap.data_json)
                for pool in sd.get("storage_pools", []):
                    storage_pools.append({
                        "name": pool.get("name", "?"),
                        "source": f"{meta['label']}: {cfg.name}",
                        "healthy": pool.get("healthy", True),
                        "pct": pool.get("pct", 0),
                        "used_gb": pool.get("used_gb", 0),
                        "total_gb": pool.get("size_gb", 0),
                        "_pred_key": f"{cfg.id}:{pool.get('name', '?')}",
                    })
    except Exception:
        pass

    _cp("disks")
    # Proxmox node disks
    try:
        px_snaps = all_snaps_cache.get("proxmox", {})
        px_configs = [c for c in all_configs if c.type == "proxmox"]
        for cfg in px_configs:
            snap = px_snaps.get(cfg.id)
            if not snap or not snap.ok or not snap.data_json:
                continue
            sd = json.loads(snap.data_json)
            for node in sd.get("nodes", []):
                total = node.get("disk_total_gb", 0)
                if total <= 0:
                    continue
                storage_pools.append({
                    "name": node.get("name", "?"),
                    "source": f"Proxmox: {cfg.name}",
                    "healthy": node.get("online", True),
                    "pct": node.get("disk_pct", 0),
                    "used_gb": node.get("disk_used_gb", 0),
                    "total_gb": total,
                    "_pred_key": f"px-{cfg.id}:{node.get('name', '?')}",
                })
    except Exception:
        pass

    # Agent disks (from latest agent_metrics in ClickHouse)
    try:
        from models.agent import Agent
        from services.clickhouse_client import get_latest_agent_metrics
        agents_q = await db.execute(select(Agent).where(Agent.enabled == True))
        enabled_agents = agents_q.scalars().all()
        snaps = await get_latest_agent_metrics([a.id for a in enabled_agents])
        for a in enabled_agents:
            snap = snaps.get(a.id)
            data_json = snap.get("data_json") if snap else None
            if not data_json:
                continue
            try:
                adata = json.loads(data_json)
            except (json.JSONDecodeError, TypeError):
                continue
            for disk in adata.get("disks", []):
                total = disk.get("total_gb", 0)
                if total <= 0.5:
                    continue  # skip tiny mounts (tmpfs etc.)
                mount = disk.get("mount", "/")
                storage_pools.append({
                    "name": f"{a.hostname}:{mount}",
                    "source": f"Agent: {a.hostname}",
                    "healthy": True,
                    "pct": disk.get("pct", 0),
                    "used_gb": disk.get("used_gb", 0),
                    "total_gb": total,
                    "_pred_key": f"agent-{a.id}:{mount}",
                })
    except Exception:
        pass

    # Sort: fullest first
    storage_pools.sort(key=lambda p: p.get("pct", 0), reverse=True)

    # ── Disk-full predictions (cached for 5 minutes) ────────────────────────
    disk_predictions = {}
    try:
        import time as _cache_time
        _PRED_CACHE = getattr(pred_svc, '_disk_pred_cache', None)
        _PRED_CACHE_TS = getattr(pred_svc, '_disk_pred_cache_ts', 0)
        if _PRED_CACHE is not None and (_cache_time.time() - _PRED_CACHE_TS) < 300:
            disk_predictions = _PRED_CACHE
        else:
            disk_predictions = await pred_svc.predict_disk_full(db)
            pred_svc._disk_pred_cache = disk_predictions
            pred_svc._disk_pred_cache_ts = _cache_time.time()

        for pool in storage_pools:
            pred = disk_predictions.get(pool.get("_pred_key"))
            if pred and pred.get("confidence", 0) >= 0.3:
                pool["days_until_full"] = pred["days_until_full"]
                pool["trend_pct_per_day"] = pred["trend_pct_per_day"]
    except Exception:
        pass

    _cp("containers")
    # ── Containers (Portainer + Agent Docker data) ─────────────────────────────
    container_data = None
    all_containers = []  # unified list across all sources
    try:
        # Portainer containers
        port_configs = [c for c in all_configs if c.type == "portainer"]
        if port_configs:
            port_snaps = all_snaps_cache.get("portainer", {})
            for cfg in port_configs:
                snap = port_snaps.get(cfg.id)
                if not snap or not snap.ok or not snap.data_json:
                    continue
                pd = json.loads(snap.data_json)
                for env in pd.get("environments", []):
                    for ct in env.get("containers", []):
                        all_containers.append({
                            "name": ct.get("name", "?"),
                            "image": ct.get("image", "?"),
                            "state": ct.get("state", "unknown"),
                            "host": env.get("name", cfg.name),
                            "source": "portainer",
                        })

        _cp("containers_portainer_done")
        # Agent Docker containers (from latest CH snapshot per agent)
        try:
            from models.agent import Agent
            from services.clickhouse_client import get_latest_agent_metrics
            agents_q = await db.execute(select(Agent).where(Agent.enabled == True))
            enabled_agents = agents_q.scalars().all()
            snaps = await get_latest_agent_metrics([a.id for a in enabled_agents])
            _cp("containers_query_done")
            for a in enabled_agents:
                snap = snaps.get(a.id)
                data_json = snap.get("data_json") if snap else None
                if not data_json:
                    continue
                try:
                    ad = json.loads(data_json)
                except (json.JSONDecodeError, TypeError):
                    continue
                for ct in ad.get("docker_containers", []):
                    all_containers.append({
                        "name": ct.get("name", "?"),
                        "image": ct.get("image", "?"),
                        "state": ct.get("state", "running" if "Up" in ct.get("status", "") else "exited"),
                        "host": a.hostname,
                        "source": "agent",
                        "cpu_pct": ct.get("cpu_pct"),
                        "mem_pct": ct.get("mem_pct"),
                        "mem_mb": ct.get("mem_mb"),
                        "health": ct.get("health"),
                        "restart_count": ct.get("restart_count", 0),
                        "update_available": ct.get("update_available", False),
                    })
        except Exception:
            pass
        _cp("containers_parse_done")
        # Deduplicate: if same container name + host from both sources, prefer agent (richer data)
        seen = set()
        deduped = []
        # Agent first (richer data)
        for c in sorted(all_containers, key=lambda x: 0 if x.get("source") == "agent" else 1):
            key = (c.get("host", ""), c.get("name", ""))
            if key not in seen:
                seen.add(key)
                deduped.append(c)
        all_containers = deduped

        total_running = sum(1 for c in all_containers if c.get("state") == "running")
        total_stopped = len(all_containers) - total_running
        updates_available = sum(1 for c in all_containers if c.get("update_available"))

        if all_containers:
            container_data = {
                "running": total_running,
                "stopped": total_stopped,
                "updates_available": updates_available,
                "containers": all_containers,
            }
    except Exception:
        pass

    _cp("ups")
    # ── UPS / NUT ─────────────────────────────────────────────────────────────
    ups_data = None
    try:
        ups_configs = [c for c in all_configs if c.type == "ups"]
        if ups_configs:
            ups_snaps = all_snaps_cache.get("ups", {})
            units = []
            any_on_battery = False
            for cfg in ups_configs:
                snap = ups_snaps.get(cfg.id)
                if not snap or not snap.ok or not snap.data_json:
                    continue
                ud = json.loads(snap.data_json)
                on_bat = ud.get("on_battery", False)
                if on_bat:
                    any_on_battery = True
                units.append({
                    "name": cfg.name,
                    "status_label": ud.get("status_label", ud.get("status", "?")),
                    "on_battery": on_bat,
                    "battery_pct": ud.get("battery_pct", 0),
                    "load_pct": ud.get("load_pct", 0),
                    "runtime_s": ud.get("runtime_s", 0),
                    "model": ud.get("model", ""),
                })
            if units:
                ups_data = {"units": units, "on_battery": any_on_battery}
    except Exception:
        pass

    _cp("ssl")
    # ── SSL certificates (hosts + integrations) ─────────────────────────────
    ssl_certs = []
    try:
        https_hosts = [h for h in hosts if "https" in (h.check_type or "")]
        for h in sorted(https_hosts, key=lambda x: x.ssl_expiry_days if x.ssl_expiry_days is not None else 9999):
            ssl_certs.append({
                "host_id": h.id,
                "name": h.name,
                "days": h.ssl_expiry_days,
                "source": "host",
            })
    except Exception:
        pass
    try:
        from routers.ssl_monitor import _get_integration_certs
        int_certs = await _get_integration_certs(db)
        for ic in int_certs:
            ssl_certs.append({
                "host_id": None,
                "name": ic["name"],
                "days": ic["days"],
                "source": ic.get("source", ""),
                "provider": ic.get("provider", ""),
            })
        ssl_certs.sort(key=lambda c: c["days"] if c["days"] is not None else 9999)
    except Exception:
        pass

    # ── Recent incidents (for alerts widget) ──────────────────────────────────
    recent_incidents = []
    incident_summary_map: dict[int, str] = {}
    try:
        recent_incidents = (await db.execute(
            select(Incident)
            .order_by(Incident.created_at.desc())
            .limit(10)
        )).scalars().all()
        # Fetch latest meaningful event summary per incident
        if recent_incidents:
            inc_ids = [inc.id for inc in recent_incidents]
            ev_rows = (await db.execute(
                select(IncidentEvent)
                .where(
                    IncidentEvent.incident_id.in_(inc_ids),
                    IncidentEvent.event_type.notin_(["acknowledged", "resolved"]),
                )
                .order_by(IncidentEvent.timestamp.desc())
            )).scalars().all()
            for ev in ev_rows:
                if ev.incident_id not in incident_summary_map:
                    incident_summary_map[ev.incident_id] = ev.summary
    except Exception:
        pass

    # ── Incident trend (daily counts for 14 days) ──────────────────────────────
    incident_trend = []
    try:
        from sqlalchemy import cast, Date
        _TREND_DAYS = 14
        trend_window = now - timedelta(days=_TREND_DAYS)
        trend_rows = (await db.execute(
            select(
                cast(Incident.created_at, Date).label("day"),
                Incident.severity,
                func.count().label("cnt"),
            )
            .where(Incident.created_at >= trend_window)
            .group_by(cast(Incident.created_at, Date), Incident.severity)
        )).all()
        trend_map: dict[str, dict] = {}
        for row in trend_rows:
            day_str = str(row.day)
            if day_str not in trend_map:
                trend_map[day_str] = {"critical": 0, "warning": 0, "info": 0}
            sev = row.severity if row.severity in ("critical", "warning", "info") else "info"
            trend_map[day_str][sev] += row.cnt
        for i in range(_TREND_DAYS):
            d = (now - timedelta(days=_TREND_DAYS - 1 - i)).date()
            day_str = str(d)
            counts = trend_map.get(day_str, {"critical": 0, "warning": 0, "info": 0})
            incident_trend.append({
                "date": d.strftime("%m/%d"),
                "critical": counts["critical"],
                "warning": counts["warning"],
                "info": counts["info"],
            })
    except Exception:
        pass

    # ── Uptime ranking ────────────────────────────────────────────────────────
    uptime_ranking = sorted(
        [{"host_id": s["host"].id, "name": s["host"].name, "uptime": s["uptime_pct"]}
         for s in active_stats if not s["host"].maintenance],
        key=lambda x: x["uptime"],
    )[:15]

    # ── Heatmap (30-day per-host availability) ────────────────────────────────
    heatmap_data = []
    heatmap_days = []
    _HEATMAP_DAYS = 30
    try:
        window_30d = now - timedelta(days=_HEATMAP_DAYS)
        for i in range(_HEATMAP_DAYS):
            d = (now - timedelta(days=_HEATMAP_DAYS - 1 - i))
            heatmap_days.append(d.strftime("%a %d.%m"))

        # Daily availability per host from ClickHouse — single query.
        day_stats = await ch_query(
            """
            SELECT
                host_id,
                toDate(timestamp) AS day,
                count() AS total,
                sumIf(1, success = 1) AS ok
            FROM ping_checks
            WHERE host_id IN ({hids:Array(UInt32)})
              AND timestamp >= {since:DateTime64(3)}
            GROUP BY host_id, day
            """,
            {"hids": host_ids, "since": window_30d},
        )

        day_map: dict[tuple, float] = {}
        for row in day_stats:
            total = int(row.get("total") or 0)
            ok = int(row.get("ok") or 0)
            pct = round(ok / total * 100, 1) if total > 0 else None
            day_map[(int(row["host_id"]), str(row["day"]))] = pct

        heatmap_hosts = sorted(
            [h for h in hosts if not h.maintenance],
            key=lambda h: min(
                (day_map.get((h.id, str((now - timedelta(days=_HEATMAP_DAYS - 1 - i)).date())), 100) or 100)
                for i in range(_HEATMAP_DAYS)
            ),
        )[:20]

        for h in heatmap_hosts:
            days = []
            for i in range(_HEATMAP_DAYS):
                d = (now - timedelta(days=_HEATMAP_DAYS - 1 - i)).date()
                days.append(day_map.get((h.id, str(d))))
            heatmap_data.append({"host_id": h.id, "name": h.name, "days": days})
    except Exception:
        pass

    # ── Nodeglow uptime ────────────────────────────────────────────────────────
    nodeglow_uptime = ""
    try:
        import os
        _start = float(os.environ.get("NODEGLOW_START_TIME", "0"))
        if _start > 0:
            delta = now.timestamp() - _start
        else:
            delta = 0
        if delta > 86400:
            nodeglow_uptime = f"{int(delta // 86400)}d {int((delta % 86400) // 3600)}h"
        elif delta > 3600:
            nodeglow_uptime = f"{int(delta // 3600)}h {int((delta % 3600) // 60)}m"
        elif delta > 0:
            nodeglow_uptime = f"{int(delta // 60)}m"
    except Exception:
        pass

    _cp("gravity")
    # ── Compute health scores (gravity well) with ALL metrics ──────────────
    guest_metrics_by_host: dict[int, dict] = {}
    for g in all_guests:
        if not g.get("running"):
            continue
        gname = g.get("name", "").lower()
        gnode = g.get("node", "").lower()
        host_id = ping_host_map.get(gname) or ping_host_map.get(gnode)
        if not host_id:
            continue
        cpu_ratio = g.get("cpu_pct", 0) / px_cpu_threshold if px_cpu_threshold else 0
        mem_total = g.get("mem_total_gb", 0)
        ram_pct = (g.get("mem_used_gb", 0) / mem_total * 100) if mem_total > 0 else 0
        ram_ratio = ram_pct / px_ram_pct_threshold if px_ram_pct_threshold else 0
        disk_ratio = g.get("disk_pct", 0) / px_disk_threshold if px_disk_threshold else 0
        prev = guest_metrics_by_host.get(host_id)
        if prev:
            prev["cpu"] = max(prev["cpu"], cpu_ratio)
            prev["ram"] = max(prev["ram"], ram_ratio)
            prev["disk"] = max(prev["disk"], disk_ratio)
        else:
            guest_metrics_by_host[host_id] = {"cpu": cpu_ratio, "ram": ram_ratio, "disk": disk_ratio}

    syslog_errors_by_host: dict[int, int] = {}
    try:
        syslog_err_rows = await ch_query(
            "SELECT host_id, count() AS cnt FROM syslog_messages WHERE host_id IS NOT NULL AND severity <= 3 AND timestamp >= {t:DateTime64(3)} GROUP BY host_id",
            {"t": window_24h},
        )
        syslog_errors_by_host = {int(r["host_id"]): r["cnt"] for r in syslog_err_rows if r["host_id"] is not None}
    except Exception:
        pass

    int_errors_by_host: set[int] = set()
    for ih in integration_health:
        if not ih.get("ok"):
            host_str = ih.get("host", "").lower()
            hid = ping_host_map.get(host_str)
            if hid:
                int_errors_by_host.add(hid)

    for s in host_stats:
        h = s["host"]
        if s["online"] is False:
            s["health_score"] = 1.0
            continue
        if h.maintenance:
            s["health_score"] = 0.5
            continue
        if s["online"] is None:
            s["health_score"] = 0.8
            continue

        score = 0.0
        _lat = s["latency"]
        _thr = s["effective_threshold"]

        if _lat is not None and _thr:
            ratio = _lat / _thr
            if ratio <= 0.5:
                score += ratio * 0.05
            elif ratio <= 0.8:
                score += 0.025 + (ratio - 0.5) / 0.3 * 0.075
            elif ratio <= 1.0:
                score += 0.10 + (ratio - 0.8) / 0.2 * 0.10
            else:
                score += 0.20
        elif _lat is not None:
            score += min(_lat / 200.0, 0.20)

        deficit = 1 - s["uptime_pct"] / 100.0
        if deficit > 0:
            score += min((deficit ** 0.5) * 0.15, 0.15)

        sp = s["sparkline"]
        if sp:
            losses = sum(1 for v in sp if v is None)
            if losses > 0:
                score += min((losses / len(sp)) ** 0.6 * 0.10, 0.10)

        gm = guest_metrics_by_host.get(h.id)
        if gm:
            cpu_r = gm["cpu"]
            if cpu_r <= 0.5:
                score += cpu_r * 0.03
            elif cpu_r <= 0.8:
                score += 0.015 + (cpu_r - 0.5) / 0.3 * 0.06
            elif cpu_r <= 1.0:
                score += 0.075 + (cpu_r - 0.8) / 0.2 * 0.075
            else:
                score += 0.15

            ram_r = gm["ram"]
            if ram_r <= 0.5:
                score += ram_r * 0.03
            elif ram_r <= 0.8:
                score += 0.015 + (ram_r - 0.5) / 0.3 * 0.06
            elif ram_r <= 1.0:
                score += 0.075 + (ram_r - 0.8) / 0.2 * 0.075
            else:
                score += 0.15

            disk_r = gm["disk"]
            if disk_r <= 0.7:
                score += disk_r * 0.02
            elif disk_r <= 0.9:
                score += 0.014 + (disk_r - 0.7) / 0.2 * 0.04
            elif disk_r <= 1.0:
                score += 0.054 + (disk_r - 0.9) / 0.1 * 0.046
            else:
                score += 0.10

        err_count = syslog_errors_by_host.get(h.id, 0)
        if err_count > 0:
            score += min((err_count ** 0.4) / 10.0 * 0.10, 0.10)

        if h.id in int_errors_by_host:
            score += 0.05

        s["health_score"] = round(min(score, 1.0), 3)

    # Dashboard widget layout
    layout_json = await get_setting(db, "dashboard_layout")
    try:
        layout = json.loads(layout_json) if layout_json else DEFAULT_LAYOUT
        if layout and isinstance(layout[0], dict) and "x" not in layout[0]:
            layout = DEFAULT_LAYOUT
        elif layout and max(w.get("h", 0) for w in layout) <= 8:
            for w in layout:
                w["h"] = w.get("h", 2) * 2
                w["y"] = w.get("y", 0) * 2
    except (json.JSONDecodeError, TypeError, IndexError):
        layout = DEFAULT_LAYOUT

    # ── Serialize for JSON ────────────────────────────────────────────────────

    def _host_dict(h):
        return {"id": h.id, "name": h.name, "hostname": h.hostname,
                "source": getattr(h, "source", ""), "check_type": getattr(h, "check_type", ""),
                "maintenance": h.maintenance, "port_error": getattr(h, "port_error", False) or False}

    def _stat_dict(s):
        return {
            "host": _host_dict(s["host"]),
            "online": s["online"], "latency": s["latency"],
            "uptime_pct": s["uptime_pct"], "avg_latency": s["avg_latency"],
            "last_check": str(s["last_check"]) if s["last_check"] else None,
            "sparkline": s["sparkline"],
            "effective_threshold": s["effective_threshold"],
            "health_score": s["health_score"],
        }

    def _incident_dict(inc):
        return {
            "id": inc.id, "title": inc.title, "severity": inc.severity,
            "status": inc.status, "created_at": str(inc.created_at),
            "summary": incident_summary_map.get(inc.id),
        }

    for ih in integration_health:
        if ih.get("cached_at"):
            ih["cached_at"] = str(ih["cached_at"])
        ih["single_instance"] = ih.get("single_instance", False)
        ih["type"] = ih.get("type", "")

    # Syslog rate chart data (24h hourly buckets)
    syslog_rate_data = {"labels": [], "errors": [], "warnings": [], "info": []}
    try:
        for e in syslog_stats.get("error_trend", []):
            syslog_rate_data["labels"].append(e["hour"])
            syslog_rate_data["errors"].append(e["errors"])
            syslog_rate_data["warnings"].append(e["warnings"])
            syslog_rate_data["info"].append(0)
    except Exception:
        pass

    ctx = {
        "host_stats": [_stat_dict(s) for s in host_stats],
        "online_count": online_count,
        "offline_count": offline_count,
        "total_count": len(active_stats),
        "integration_health": integration_health,
        "active_incidents": len(active_incidents),
        "syslog_stats": {
            "total_24h": syslog_stats.get("total", 0),
            "errors_24h": syslog_stats.get("error_rate_1h", 0),
            "rate_data": syslog_rate_data,
        },
        "layout": layout,
        "speedtest_data": speedtest_data,
        "storage_pools": storage_pools,
        "container_data": container_data,
        "ups_data": ups_data,
        "ssl_certs": ssl_certs,
        "recent_incidents": [_incident_dict(inc) for inc in recent_incidents],
        "incident_trend": incident_trend,
        "uptime_ranking": uptime_ranking,
        "heatmap_data": heatmap_data,
        "heatmap_days": heatmap_days,
        "top_latency": [{"id": s["host"].id, "name": s["host"].name, "value": s["avg_latency"]}
                        for s in top_latency],
        "anomalies": anomalies,
        "warnings": warnings,
        "nodeglow_uptime": nodeglow_uptime,
    }

    _cp("done")
    import logging; _log = logging.getLogger("uvicorn")
    sections = []
    prev = 0
    for label, t in _checkpoints:
        sections.append({"name": label, "ms": round(t - prev)})
        prev = t
    total_ms = round(_checkpoints[-1][1])
    _section_summary = " | ".join("{0}:+{1}".format(s["name"], s["ms"]) for s in sections)
    _log.warning(f"DASHBOARD_API {total_ms}ms total | {_section_summary}")
    global _last_perf
    _last_perf = {
        "total_ms": total_ms,
        "sections": sections,
        "timestamp": datetime.utcnow().isoformat(),
    }
    return JSONResponse(ctx, headers={"Cache-Control": "no-cache"})


@router.post("/api/dashboard-layout")
async def save_dashboard_layout(request: Request, db: AsyncSession = Depends(get_db)):
    body = await request.json()
    widgets = body.get("layout")
    if not isinstance(widgets, list):
        return JSONResponse({"ok": False, "error": "Invalid layout"}, status_code=400)
    cleaned = []
    for w in widgets:
        wid = w.get("id")
        if wid not in VALID_WIDGET_IDS:
            continue
        cleaned.append({
            "id": wid,
            "x": max(0, min(11, int(w.get("x", 0)))),
            "y": max(0, int(w.get("y", 0))),
            "w": max(1, min(12, int(w.get("w", 12)))),
            "h": max(1, min(24, int(w.get("h", 4)))),
        })
    await set_setting(db, "dashboard_layout", json.dumps(cleaned))
    await db.commit()
    return JSONResponse({"ok": True})


@router.post("/api/dashboard-layout/reset")
async def reset_dashboard_layout(request: Request, db: AsyncSession = Depends(get_db)):
    await set_setting(db, "dashboard_layout", json.dumps(DEFAULT_LAYOUT))
    await db.commit()
    return JSONResponse({"ok": True})
