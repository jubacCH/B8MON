"""
Background scheduler – generic integration collection + ping checks + cleanup.
"""
import asyncio
import logging
import os
import socket
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import delete, select

import config
from database import AsyncSessionLocal, PingHost
from models.integration import IntegrationConfig
from services import integration as int_svc
from services import shared_state
from services import snapshot as snap_svc
from services.metrics import instrument_job

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()

# ── Scheduler leadership (multi-worker / HA safety) ──────────────────────────
# Without a leader lock every uvicorn worker / replica would start its own
# scheduler and run correlation, ping, etc. concurrently → duplicate incidents
# and duplicate work. When REDIS_URL is set, all instances contend for a single
# Redis lease and only the holder runs jobs. Without Redis (the default,
# single-process deployment) there is exactly one process which is always the
# leader, so behaviour is unchanged.
_SCHEDULER_LEADER_LOCK = "nodeglow:scheduler:leader"
_LEADER_TTL_SECONDS = 30
_LEADER_RENEW_SECONDS = 10
_instance_id = f"{socket.gethostname()}:{os.getpid()}"
_leader_task: "asyncio.Task | None" = None
_is_leader = False


def _apply_leadership(leader: bool) -> None:
    """Resume or pause the scheduler on a leadership transition (idempotent)."""
    global _is_leader
    if leader and not _is_leader:
        scheduler.resume()
        _is_leader = True
        logger.info("Acquired scheduler leadership — jobs running (instance=%s)", _instance_id)
    elif not leader and _is_leader:
        scheduler.pause()
        _is_leader = False
        logger.warning("Lost scheduler leadership — jobs paused (instance=%s)", _instance_id)


async def _leadership_loop():
    """Periodically acquire/renew the Redis lease; resume jobs only while leader."""
    while True:
        try:
            leader = await shared_state.try_acquire_leader(
                _SCHEDULER_LEADER_LOCK, _instance_id, _LEADER_TTL_SECONDS
            )
            _apply_leadership(leader)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # Preserve current leadership state on transient errors: never grant
            # leadership to everyone (split-brain) nor stop a healthy leader. A
            # genuinely dead leader frees the lease via its TTL.
            logger.error("Scheduler leadership loop error: %s", e)
        await asyncio.sleep(_LEADER_RENEW_SECONDS)


# ── Generic integration collection ───────────────────────────────────────────


# Markers placed in the snapshot row's `error` field to indicate dedupe state.
# These are visible in the API/UI so the user can see "this integration is a
# standby for cluster X". They are not real errors.
_STANDBY_MARKER = "[standby:duplicate-of-primary]"


@instrument_job("integration_checks")
async def run_integration_checks():
    """
    Generic collector loop: for each registered integration, fetch all configs
    and run collect(). Stores results as Snapshots.

    Cluster grouping (Phase 8): when multiple integrations share the same
    `cluster_group`, only the FIRST one to successfully poll this cycle writes
    its data (snapshot, bandwidth, alerts, on_snapshot hook). The others still
    poll for failover but their writes are suppressed and they record a
    "standby" marker. If the active primary fails, the next member naturally
    takes over the next cycle. For Proxmox integrations the cluster_group is
    auto-populated from the cluster_name on first successful poll.
    """
    from integrations import get_registry

    registry = get_registry()
    if not registry:
        return

    async with AsyncSessionLocal() as db:
        # Pull only the IDs in id-order, then re-fetch each cfg fresh inside
        # the per-type loop. This avoids the stale-ORM-object trap where
        # cfg.cluster_group mutations would not be tracked by a different
        # session and silently fail to persist.
        id_rows = (await db.execute(
            select(IntegrationConfig.id, IntegrationConfig.type)
            .where(IntegrationConfig.enabled == True)
            .order_by(IntegrationConfig.id)
        )).all()

    if not id_rows:
        return

    by_type: dict[str, list[int]] = {}
    for cfg_id, cfg_type in id_rows:
        by_type.setdefault(cfg_type, []).append(cfg_id)

    from services.tracing import tracer

    # Tracks which (type, cluster_group) tuples have already been written this
    # cycle. First successful poll wins; subsequent members of the same group
    # are demoted to standby.
    cluster_owners: set[tuple[str, str]] = set()

    for integration_type, cfg_ids in by_type.items():
        integration_cls = registry.get(integration_type)
        if not integration_cls:
            continue

        async with AsyncSessionLocal() as db:
            for cfg_id in cfg_ids:
                cfg = await db.get(IntegrationConfig, cfg_id)
                if cfg is None or not cfg.enabled:
                    continue
                with tracer.start_as_current_span("integration.collect") as _span:
                    _span.set_attribute("integration.type", integration_type)
                    _span.set_attribute("integration.config_id", cfg.id)
                    _span.set_attribute("integration.config_name", cfg.name)
                    try:
                        try:
                            config_dict = int_svc.decrypt_config(cfg.config_json)
                        except Exception as dec_exc:
                            logger.error(
                                "Failed to decrypt config [%s/%s]: %s",
                                integration_type, cfg.name, dec_exc,
                            )
                            await snap_svc.save(
                                db, integration_type, cfg.id,
                                ok=False, error=f"Decryption failed: {dec_exc}",
                            )
                            _span.set_attribute("error", True)
                            continue
                        instance = integration_cls(config=config_dict)
                        result = await instance.collect()

                        if result.success:
                            # Auto-populate cluster_group from cluster_name for
                            # Proxmox integrations on first successful poll.
                            if (
                                integration_type == "proxmox"
                                and not cfg.cluster_group
                                and isinstance(result.data, dict)
                                and result.data.get("cluster_name")
                            ):
                                cfg.cluster_group = str(result.data["cluster_name"])
                                logger.info(
                                    "Auto-grouped %s/%s under cluster_group=%s",
                                    integration_type, cfg.name, cfg.cluster_group,
                                )

                            # Determine primary vs standby for this cluster_group.
                            group_key = (
                                (integration_type, cfg.cluster_group)
                                if cfg.cluster_group else None
                            )
                            is_standby = group_key is not None and group_key in cluster_owners

                            if is_standby:
                                # Standby: write a health-only snapshot so the
                                # health check passes but skip data, bandwidth,
                                # hooks, and alerts.
                                _span.set_attribute("integration.standby", True)
                                await snap_svc.save(
                                    db, integration_type, cfg.id,
                                    ok=True, error=_STANDBY_MARKER,
                                )
                                logger.debug(
                                    "Standby skip [%s/%s] — primary already wrote group=%s",
                                    integration_type, cfg.name, cfg.cluster_group,
                                )
                            else:
                                # Primary: write everything.
                                if group_key is not None:
                                    cluster_owners.add(group_key)
                                    _span.set_attribute("integration.cluster_group", cfg.cluster_group)

                                await snap_svc.save(
                                    db, integration_type, cfg.id,
                                    ok=True, data=result.data,
                                )
                                # Extract bandwidth data from supported integration types
                                try:
                                    from services.bandwidth import (
                                        extract_proxmox_bandwidth,
                                        extract_unifi_bandwidth,
                                    )
                                    if integration_type == "proxmox":
                                        await extract_proxmox_bandwidth(
                                            cfg.id, result.data, source_name=cfg.name,
                                        )
                                    elif integration_type == "unifi":
                                        await extract_unifi_bandwidth(
                                            cfg.id, result.data, source_name=cfg.name,
                                        )
                                except Exception as bw_exc:
                                    logger.warning(
                                        "Bandwidth extraction failed [%s/%s]: %s",
                                        integration_type, cfg.name, bw_exc,
                                    )
                                # Run post-snapshot hook (e.g., auto-import hosts)
                                try:
                                    await instance.on_snapshot(result.data, config_dict, db)
                                except Exception as hook_exc:
                                    logger.warning(
                                        "on_snapshot hook failed [%s/%s]: %s",
                                        integration_type, cfg.name, hook_exc,
                                    )
                        else:
                            await snap_svc.save(
                                db, integration_type, cfg.id,
                                ok=False, error=result.error,
                            )
                    except Exception as exc:
                        logger.error(
                            "Integration collect [%s/%s]: %s",
                            integration_type, cfg.name, exc,
                        )
                        _span.set_attribute("error", True)
                        _span.set_attribute("error.type", type(exc).__name__)
                        await snap_svc.save(
                            db, integration_type, cfg.id,
                            ok=False, error=str(exc),
                        )
            await db.commit()

    logger.debug(
        "Integration check done for %d type(s), %d config(s), %d active cluster groups",
        len(by_type), len(id_rows), len(cluster_owners),
    )


# ── Ping checks ──────────────────────────────────────────────────────────────


# Grace period tracker: host_id -> first_seen_offline timestamp
_offline_since: dict[int, datetime] = {}
_grace_notified: set[int] = set()  # host_ids already notified after grace

# Hysteresis for the host.port_error flag. The raw single-cycle result from
# check_host() flaps for borderline hosts (e.g. external HTTPS endpoints with
# transient TLS / rate-limit issues), which causes the correlation engine to
# fire and auto-resolve incidents in the same 60s cycle. Latch the flag:
# only set it True after N consecutive failed service checks, and only clear
# it after M consecutive passes.
PORT_ERROR_SET_THRESHOLD = 3
PORT_ERROR_CLEAR_THRESHOLD = 2
_port_fail_streak: dict[int, int] = {}
_port_pass_streak: dict[int, int] = {}


@instrument_job("ping_checks")
async def run_ping_checks():
    """Ping all enabled hosts concurrently and store results."""
    import asyncio as _asyncio
    from utils.ping import check_host

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(PingHost).where(PingHost.enabled == True))
        hosts = result.scalars().all()

        # Auto-clear expired maintenance windows
        now = datetime.utcnow()
        for h in hosts:
            if h.maintenance and h.maintenance_until and h.maintenance_until <= now:
                h.maintenance = False
                h.maintenance_until = None
        await db.commit()

    if not hosts:
        return

    active_hosts = [h for h in hosts if not h.maintenance]
    if not active_hosts:
        return

    # Separate agent-sourced hosts — they use agent heartbeat, not ICMP
    agent_hosts = [h for h in active_hosts if h.source == "agent"]
    ping_hosts = [h for h in active_hosts if h.source != "agent"]

    # Handle agent-sourced hosts via agent last_seen
    if agent_hosts:
        from models.agent import Agent
        from sqlalchemy import func as sa_func
        agent_ping_rows: list[dict] = []
        async with AsyncSessionLocal() as db:
            for host in agent_hosts:
                # Find matching agent by name (PingHost.name == Agent.hostname)
                agent_r = await db.execute(
                    select(Agent).where(sa_func.lower(Agent.hostname) == host.name.lower())
                )
                agent = agent_r.scalar_one_or_none()
                success = False
                if agent and agent.last_seen:
                    success = (datetime.utcnow() - agent.last_seen).total_seconds() < 120
                ts_now = datetime.utcnow()
                latency_val = 0 if success else None
                agent_ping_rows.append({
                    "timestamp": ts_now,
                    "host_id": host.id,
                    "host_name": host.name,
                    "success": success,
                    "latency_ms": latency_val,
                })
                from services.websocket import broadcast_ping_update
                _asyncio.create_task(broadcast_ping_update(host.id, host.name, success, 0 if success else None))

        from services.clickhouse_client import insert_ping_checks
        try:
            await insert_ping_checks(agent_ping_rows)
        except Exception as ch_exc:
            logger.error("ClickHouse insert (agent ping_checks) failed: %s", ch_exc)

    active_hosts = ping_hosts

    # Load previous success state per host from ClickHouse (for state-change detection)
    from services.clickhouse_client import get_latest_ping_per_host
    try:
        latest_map = await get_latest_ping_per_host([h.id for h in active_hosts])
        prev_success: dict[int, bool] = {
            hid: bool(rec.get("success")) for hid, rec in latest_map.items()
        }
    except Exception as ch_exc:
        logger.warning("ClickHouse previous-state lookup failed: %s", ch_exc)
        prev_success = {}

    # Run all checks concurrently with semaphore to limit parallelism
    sem = _asyncio.Semaphore(50)

    async def _check_one(host):
        async with sem:
            online, port_error, latency, detail = await check_host(host)
            return host, online, port_error, latency, detail

    results = await _asyncio.gather(*[_check_one(h) for h in active_hosts])

    # Update host port_error / detail state in Postgres (config table) and
    # collect the time-series rows for the ClickHouse insert.
    import json as _json
    from services.metrics import PING_CHECKS_TOTAL
    now = datetime.utcnow()
    went_offline: list[str] = []
    came_online: list[str] = []
    icmp_rows: list[dict] = []
    async with AsyncSessionLocal() as db:
        for host, online, port_error, latency, detail in results:
            icmp_rows.append({
                "timestamp": now,
                "host_id": host.id,
                "host_name": host.name,
                "success": online,
                "latency_ms": latency,
            })
            PING_CHECKS_TOTAL.labels(result="success" if online else "failure").inc()

            # Update port_error flag and check detail on the host. Apply
            # hysteresis on top of the raw single-cycle result so a flapping
            # endpoint doesn't trigger fire-then-resolve in the correlation
            # engine. Only count cycles where ICMP succeeded and a non-ICMP
            # check actually ran — otherwise we can't tell anything about
            # service health and must not adjust the streaks.
            host_obj = await db.get(PingHost, host.id)
            if host_obj:
                has_service_check = any(k != "icmp" for k in (detail or {}).keys())
                if online and has_service_check:
                    if port_error:
                        _port_pass_streak.pop(host.id, None)
                        _port_fail_streak[host.id] = _port_fail_streak.get(host.id, 0) + 1
                    else:
                        _port_fail_streak.pop(host.id, None)
                        _port_pass_streak[host.id] = _port_pass_streak.get(host.id, 0) + 1

                latched = host_obj.port_error
                if not latched and _port_fail_streak.get(host.id, 0) >= PORT_ERROR_SET_THRESHOLD:
                    latched = True
                elif latched and _port_pass_streak.get(host.id, 0) >= PORT_ERROR_CLEAR_THRESHOLD:
                    latched = False
                host_obj.port_error = latched
                host_obj.check_detail = _json.dumps(detail) if detail else None

            # Broadcast live update via WebSocket
            from services.websocket import broadcast_ping_update
            _asyncio.create_task(broadcast_ping_update(host.id, host.name, online, latency))

            # Track state changes for grace-period notification
            if not online:
                if host.id not in _offline_since:
                    _offline_since[host.id] = now
            else:
                # Host is back online — clear grace tracking
                was_tracked = host.id in _offline_since
                was_notified = host.id in _grace_notified
                _offline_since.pop(host.id, None)
                _grace_notified.discard(host.id)
                prev = prev_success.get(host.id)
                if prev is False and online and was_notified:
                    came_online.append(host.name)

        await db.commit()

    # ClickHouse is the authoritative store for ping check time-series.
    if icmp_rows:
        try:
            await insert_ping_checks(icmp_rows)
        except Exception as ch_exc:
            logger.error("ClickHouse insert (ping_checks) failed: %s", ch_exc)

    # Grace period: only notify after host has been offline for N minutes
    from models.settings import get_setting
    async with AsyncSessionLocal() as db:
        grace_str = await get_setting(db, "notify_grace_minutes", "5")
    try:
        grace_minutes = max(0, int(grace_str))
    except (ValueError, TypeError):
        grace_minutes = 5

    for host, online, port_error, latency, detail in results:
        if not online and host.id in _offline_since and host.id not in _grace_notified:
            elapsed = (now - _offline_since[host.id]).total_seconds() / 60
            if elapsed >= grace_minutes:
                went_offline.append(host.name)
                _grace_notified.add(host.id)

    # Send batched notifications (one message per direction)
    from notifications import notify
    if went_offline:
        names = ", ".join(went_offline)
        title = f"Host offline: {went_offline[0]}" if len(went_offline) == 1 \
            else f"{len(went_offline)} hosts went offline"
        _asyncio.create_task(notify(title, names, "critical"))
    if came_online:
        names = ", ".join(came_online)
        title = f"Host back online: {came_online[0]}" if len(came_online) == 1 \
            else f"{len(came_online)} hosts back online"
        _asyncio.create_task(notify(title, names, "info"))

    logger.debug("Ping check done for %d hosts (concurrent)", len(active_hosts))


# ── SSL expiry check ─────────────────────────────────────────────────────────


@instrument_job("ssl_expiry")
async def update_ssl_expiry():
    """Update ssl_expiry_days for all HTTPS hosts."""
    from utils.ping import get_ssl_expiry_days
    from sqlalchemy import update as sa_update

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(PingHost).where(PingHost.enabled == True, PingHost.check_type == "https")
        )
        hosts = result.scalars().all()

    if not hosts:
        return

    async with AsyncSessionLocal() as db:
        for host in hosts:
            hostname = host.hostname
            for prefix in ("https://", "http://"):
                if hostname.startswith(prefix):
                    hostname = hostname[len(prefix):]
                    break
            hostname = hostname.split("/")[0].split(":")[0]
            days = await get_ssl_expiry_days(hostname, port=host.port or 443)
            if days is not None:
                await db.execute(
                    sa_update(PingHost).where(PingHost.id == host.id).values(ssl_expiry_days=days)
                )
        await db.commit()


# ── Cleanup ───────────────────────────────────────────────────────────────────


@instrument_job("cleanup_old_results")
async def cleanup_old_results():
    """Delete old integration snapshots and SNMP results from Postgres.

    Time-series data (ping_checks, agent_metrics, bandwidth_metrics, syslog)
    lives in ClickHouse and is reaped by table-level TTL — no cleanup needed.
    """
    from database import get_setting

    async with AsyncSessionLocal() as db:
        int_ret = int(await get_setting(db, "integration_retention_days", "7"))
        ev_ret = int(await get_setting(db, "incident_event_retention_days", "30"))

    async with AsyncSessionLocal() as db:
        await snap_svc.cleanup_all(db, int_ret)
        # SNMP results: keep 7 days (still in Postgres)
        from models.snmp import SnmpResult
        snmp_cutoff = datetime.utcnow() - timedelta(days=7)
        await db.execute(delete(SnmpResult).where(SnmpResult.timestamp < snmp_cutoff))
        # Incident events accumulate one row per correlation cycle per open
        # incident — prune old ones (keeps newest meaningful event per incident).
        from services.correlation import cleanup_incident_events
        ev_deleted = await cleanup_incident_events(db, ev_ret)
        await db.commit()

    logger.info("Cleanup done (integrations: %dd, incident events: %dd, %d pruned)",
                int_ret, ev_ret, ev_deleted)


# ── Disk space health check ──────────────────────────────────────────────────

DISK_WARNING_PCT = 80
DISK_CRITICAL_PCT = 90
DISK_HISTORY_MAX_SAMPLES = 336  # ~7 days at 30min intervals


async def _record_disk_sample(used_gb: float, total_gb: float):
    """Store a disk usage sample in the settings table for trend analysis."""
    import json
    from database import get_setting, set_setting

    async with AsyncSessionLocal() as db:
        raw = await get_setting(db, "disk_usage_history", "[]")
        try:
            history = json.loads(raw)
        except Exception:
            history = []

        history.append({
            "ts": datetime.utcnow().isoformat(),
            "used_gb": used_gb,
            "total_gb": total_gb,
        })

        # Keep only last N samples
        if len(history) > DISK_HISTORY_MAX_SAMPLES:
            history = history[-DISK_HISTORY_MAX_SAMPLES:]

        await set_setting(db, "disk_usage_history", json.dumps(history))
        await db.commit()


def compute_disk_forecast(history: list[dict], total_gb: float) -> dict | None:
    """Linear regression on disk usage history to predict days until full."""
    if len(history) < 6:  # Need at least 3 hours of data
        return None

    from datetime import datetime as dt

    # Parse timestamps and usage values
    points = []
    for sample in history:
        try:
            ts = dt.fromisoformat(sample["ts"])
            points.append((ts.timestamp(), sample["used_gb"]))
        except Exception:
            continue

    if len(points) < 6:
        return None

    # Simple linear regression
    n = len(points)
    t0 = points[0][0]
    xs = [(p[0] - t0) / 86400 for p in points]  # days since first sample
    ys = [p[1] for p in points]

    sum_x = sum(xs)
    sum_y = sum(ys)
    sum_xy = sum(x * y for x, y in zip(xs, ys))
    sum_xx = sum(x * x for x in xs)

    denom = n * sum_xx - sum_x * sum_x
    if abs(denom) < 1e-10:
        return {"growth_gb_per_day": 0, "days_until_full": None, "trend": "stable"}

    slope = (n * sum_xy - sum_x * sum_y) / denom  # GB per day

    if slope <= 0.001:
        return {"growth_gb_per_day": round(slope, 3), "days_until_full": None, "trend": "stable"}

    current_used = ys[-1]
    remaining = total_gb - current_used
    days_until_full = round(remaining / slope, 1)

    trend = "critical" if days_until_full <= 7 else "warning" if days_until_full <= 30 else "normal"

    return {
        "growth_gb_per_day": round(slope, 3),
        "days_until_full": days_until_full,
        "trend": trend,
    }


@instrument_job("disk_space_check")
async def check_disk_space():
    """Monitor disk usage and create incidents when thresholds are exceeded."""
    import shutil
    from hashlib import sha256

    from models.incident import Incident, IncidentEvent

    try:
        usage = shutil.disk_usage("/")
    except Exception:
        logger.debug("disk_usage check failed", exc_info=True)
        return

    used_pct = round(usage.used / usage.total * 100, 1)
    free_gb = round(usage.free / (1024 ** 3), 1)
    used_gb = round(usage.used / (1024 ** 3), 2)
    total_gb = round(usage.total / (1024 ** 3), 2)

    # Record sample for trend analysis
    await _record_disk_sample(used_gb, total_gb)

    if used_pct < DISK_WARNING_PCT:
        # All clear — auto-resolve any existing disk incident
        async with AsyncSessionLocal() as db:
            existing = (await db.execute(
                select(Incident).where(
                    Incident.rule == "disk_space",
                    Incident.status.in_(["open", "acknowledged"]),
                )
            )).scalars().first()
            if existing:
                existing.status = "resolved"
                existing.resolved_at = datetime.utcnow()
                db.add(IncidentEvent(
                    incident_id=existing.id,
                    event_type="resolved",
                    summary=f"Disk usage back to {used_pct}% ({free_gb} GB free)",
                ))
                await db.commit()
                logger.info("Disk space incident resolved: %.1f%% used", used_pct)
        return

    severity = "critical" if used_pct >= DISK_CRITICAL_PCT else "warning"
    title = f"Disk usage at {used_pct}% ({free_gb} GB free)"

    async with AsyncSessionLocal() as db:
        # Dedup: only one open disk incident at a time
        existing = (await db.execute(
            select(Incident).where(
                Incident.rule == "disk_space",
                Incident.status.in_(["open", "acknowledged"]),
            )
        )).scalars().first()

        if existing:
            # Update severity if it escalated
            if severity == "critical" and existing.severity != "critical":
                existing.severity = "critical"
            existing.title = title
            existing.updated_at = datetime.utcnow()
            db.add(IncidentEvent(
                incident_id=existing.id,
                event_type="disk_warning",
                summary=title,
            ))
        else:
            inc = Incident(
                rule="disk_space",
                title=title,
                severity=severity,
                status="open",
                host_ids_hash=sha256(b"self-disk").hexdigest()[:16],
            )
            db.add(inc)
            await db.flush()
            db.add(IncidentEvent(
                incident_id=inc.id,
                event_type="created",
                summary=title,
            ))

        await db.commit()

    logger.warning("Disk space %s: %.1f%% used (%.1f GB free)", severity, used_pct, free_gb)


# ── ClickHouse maintenance ───────────────────────────────────────────────────


@instrument_job("clickhouse_log_cleanup")
async def cleanup_clickhouse_logs():
    """Truncate ClickHouse system log tables if they exceed size thresholds."""
    try:
        from services.clickhouse_client import query as ch_query

        SAFE_TABLES = {"query_log", "processors_profile_log", "trace_log",
                       "metric_log", "asynchronous_metric_log", "part_log"}

        rows = await ch_query(
            "SELECT table, sum(bytes_on_disk) AS bytes "
            "FROM system.parts WHERE active AND database = 'system' "
            "GROUP BY table ORDER BY bytes DESC"
        )
        for row in rows:
            size_mb = row["bytes"] / (1024 * 1024)
            if size_mb > 500 and row["table"] in SAFE_TABLES:
                await ch_query(f"TRUNCATE TABLE system.{row['table']}")
                logger.info("Truncated system.%s (was %.0f MB)", row["table"], size_mb)
    except Exception:
        logger.debug("ClickHouse log cleanup skipped", exc_info=True)


# ── Scheduler lifecycle ──────────────────────────────────────────────────────


@instrument_job("correlation")
async def run_correlation():
    """Run the correlation engine."""
    from services.correlation import run_correlation as _run
    await _run()


@instrument_job("log_intelligence")
async def run_log_intelligence():
    """Run the log intelligence engine (template flush, baselines, precursors)."""
    from services.log_intelligence import run_intelligence
    await run_intelligence()


@instrument_job("snmp_polls")
async def run_snmp_polls():
    """Run due SNMP polls."""
    from routers.snmp import run_snmp_polls as _run
    await _run()


@instrument_job("port_discovery")
async def run_port_discovery():
    """Discover open ports and SSL certs on monitored hosts."""
    from services.port_discovery import run_port_discovery as _run
    await _run()


async def run_weekly_digest():
    """Build and send weekly digest email."""
    from database import get_setting, decrypt_value
    from services.digest import build_weekly_digest, format_digest_html, format_digest_text
    from notifications import _send_email, _log_notification

    async with AsyncSessionLocal() as db:
        enabled = await get_setting(db, "digest_enabled", "0")
        if enabled != "1":
            return

        smtp_host = await get_setting(db, "smtp_host", "")
        smtp_user = await get_setting(db, "smtp_user", "")
        smtp_pw_enc = await get_setting(db, "smtp_password", "")
        smtp_to = await get_setting(db, "smtp_to", "")
        if not (smtp_host and smtp_user and smtp_pw_enc and smtp_to):
            logger.warning("Weekly digest skipped: SMTP not configured")
            return

        smtp_port = int(await get_setting(db, "smtp_port", "587"))
        smtp_from = await get_setting(db, "smtp_from", "") or smtp_user

        try:
            smtp_pw = decrypt_value(smtp_pw_enc)
        except Exception:
            smtp_pw = smtp_pw_enc

        digest = await build_weekly_digest(db)

    html_body = format_digest_html(digest)
    text_body = format_digest_text(digest)

    try:
        await _send_email(
            smtp_host, smtp_port, smtp_user, smtp_pw,
            smtp_from, smtp_to,
            "[Nodeglow] Weekly Digest", text_body, html_body,
        )
        await _log_notification("email", "Weekly Digest", "Automated weekly digest", "info", "sent")
        logger.info("Weekly digest email sent to %s", smtp_to)
    except Exception as exc:
        await _log_notification("email", "Weekly Digest", "Automated weekly digest", "info", "failed", str(exc))
        logger.error("Weekly digest email failed: %s", exc)


async def run_daily_ai_summary():
    """Build AI-powered daily summary and send via configured notification channels."""
    from database import get_setting, set_setting
    from services.digest import (
        build_daily_summary_data, format_daily_summary_prompt,
        _DAILY_SUMMARY_SYSTEM_PROMPT,
    )
    from services.ai_client import generate_completion
    from notifications import (
        _send_telegram, _send_discord, _log_notification,
        _send_webhook, _send_email, _build_html_email,
    )
    from models.ai_usage import AiUsageLog

    async with AsyncSessionLocal() as db:
        enabled = await get_setting(db, "daily_ai_summary_enabled", "0")
        if enabled != "1":
            return

        # ── Duplicate protection: skip if already sent today ──────────────
        last_sent_str = await get_setting(db, "daily_ai_summary_last_sent", "")
        if last_sent_str:
            try:
                last_sent = datetime.fromisoformat(last_sent_str)
                if (datetime.utcnow() - last_sent).total_seconds() < 20 * 3600:
                    logger.info("Daily AI summary skipped: already sent at %s", last_sent_str)
                    return
            except ValueError:
                pass  # corrupted value, proceed

        # Check if AI key is configured
        claude_key = await get_setting(db, "claude_api_key", "")
        if not claude_key:
            logger.warning("Daily AI summary skipped: no Claude API key configured")
            return

        # Collect 24h data
        data = await build_daily_summary_data(db)

    # Skip if nothing happened
    inc_total = data.get("incidents", {}).get("total", 0)
    down_hosts = len(data.get("hosts", {}).get("down", []))
    syslog_errors = data.get("syslog", {}).get("errors", 0)
    unhealthy_int = len(data.get("integrations", []))
    ssl_expiring = len(data.get("ssl_expiring", []))

    if inc_total == 0 and down_hosts == 0 and syslog_errors == 0 and unhealthy_int == 0 and ssl_expiring == 0:
        logger.info("Daily AI summary skipped: no events in last 24h")
        return

    # ── Generate AI analysis (with token tracking) ────────────────────────
    prompt = format_daily_summary_prompt(data)
    try:
        summary, usage = await generate_completion(
            _DAILY_SUMMARY_SYSTEM_PROMPT, prompt, max_tokens=1500,
            return_usage=True,
        )
    except Exception as exc:
        logger.error("Daily AI summary generation failed: %s", exc)
        await _log_notification("ai", "Daily AI Summary", "AI generation failed", "info", "failed", str(exc))
        return

    # ── Log token usage ───────────────────────────────────────────────────
    try:
        # Haiku pricing: $1/MTok input, $5/MTok output
        _COST_PER_INPUT = 1.0 / 1_000_000
        _COST_PER_OUTPUT = 5.0 / 1_000_000
        cost = (usage["input_tokens"] * _COST_PER_INPUT
                + usage["output_tokens"] * _COST_PER_OUTPUT)
        async with AsyncSessionLocal() as db:
            db.add(AiUsageLog(
                feature="daily_summary",
                model=usage.get("model", "unknown"),
                input_tokens=usage["input_tokens"],
                output_tokens=usage["output_tokens"],
                cost_usd=round(cost, 6),
            ))
            await db.commit()
        logger.info("Daily AI summary: %d in + %d out tokens ($%.4f)",
                     usage["input_tokens"], usage["output_tokens"], cost)
    except Exception as exc:
        logger.warning("Failed to log AI usage: %s", exc)

    # ── Read channel config + selected channels ───────────────────────────
    title = "Daily AI Summary"
    sent = False

    async with AsyncSessionLocal() as db:
        notify_enabled = await get_setting(db, "notify_enabled", "0")
        if notify_enabled != "1":
            logger.warning("Daily AI summary: notifications disabled")
            return

        # Which channels should receive the summary (default: all)
        channels_csv = await get_setting(db, "daily_ai_summary_channels", "telegram,discord,webhook,email")
        selected = {c.strip() for c in channels_csv.split(",") if c.strip()}

        tg_token = await get_setting(db, "telegram_bot_token", "")
        tg_chat = await get_setting(db, "telegram_chat_id", "")
        dc_webhook = await get_setting(db, "discord_webhook_url", "")
        wh_url = await get_setting(db, "webhook_url", "")
        wh_secret = await get_setting(db, "webhook_secret", "")
        smtp_host = await get_setting(db, "smtp_host", "")
        smtp_user = await get_setting(db, "smtp_user", "")
        smtp_pw_enc = await get_setting(db, "smtp_password", "")
        smtp_to = await get_setting(db, "smtp_to", "")
        smtp_port = int(await get_setting(db, "smtp_port", "587"))
        smtp_from = await get_setting(db, "smtp_from", "") or smtp_user

    # Telegram
    if "telegram" in selected and tg_token and tg_chat:
        try:
            tg_text = f"<b>🤖 {title}</b>\n\n{summary}"
            if len(tg_text) > 4096:
                tg_text = tg_text[:4090] + "\n[…]"
            await _send_telegram(tg_token, tg_chat, tg_text)
            await _log_notification("telegram", title, summary[:200], "info", "sent")
            sent = True
        except Exception as exc:
            logger.error("Daily AI summary Telegram failed: %s", exc)
            await _log_notification("telegram", title, summary[:200], "info", "failed", str(exc))

    # Discord
    if "discord" in selected and dc_webhook:
        try:
            desc = summary[:4090] if len(summary) > 4090 else summary
            await _send_discord(dc_webhook, f"🤖 {title}", desc, 0x8B5CF6)
            await _log_notification("discord", title, summary[:200], "info", "sent")
            sent = True
        except Exception as exc:
            logger.error("Daily AI summary Discord failed: %s", exc)
            await _log_notification("discord", title, summary[:200], "info", "failed", str(exc))

    # Webhook
    if "webhook" in selected and wh_url:
        try:
            await _send_webhook(wh_url, wh_secret, title, summary, "info")
            await _log_notification("webhook", title, summary[:200], "info", "sent")
            sent = True
        except Exception as exc:
            logger.error("Daily AI summary Webhook failed: %s", exc)
            await _log_notification("webhook", title, summary[:200], "info", "failed", str(exc))

    # Email
    if "email" in selected and smtp_host and smtp_user and smtp_pw_enc and smtp_to:
        try:
            from database import decrypt_value
            try:
                smtp_pw = decrypt_value(smtp_pw_enc)
            except Exception:
                smtp_pw = smtp_pw_enc
            html_body = _build_html_email(title, summary, "info")
            await _send_email(
                smtp_host, smtp_port, smtp_user, smtp_pw,
                smtp_from, smtp_to,
                f"[Nodeglow] {title}", f"{title}\n\n{summary}", html_body,
            )
            await _log_notification("email", title, summary[:200], "info", "sent")
            sent = True
        except Exception as exc:
            logger.error("Daily AI summary Email failed: %s", exc)
            await _log_notification("email", title, summary[:200], "info", "failed", str(exc))

    # ── Mark as sent (duplicate protection) ───────────────────────────────
    if sent:
        async with AsyncSessionLocal() as db:
            await set_setting(db, "daily_ai_summary_last_sent", datetime.utcnow().isoformat())
            await db.commit()
        logger.info("Daily AI summary sent successfully")
    else:
        logger.warning("Daily AI summary: no notification channel configured or selected")


async def update_geoip():
    """Weekly GeoIP database update (if enabled and license key is set)."""
    from database import get_setting, decrypt_value
    from services.geoip_updater import download_geolite2

    async with AsyncSessionLocal() as db:
        enabled = await get_setting(db, "geoip_enabled", "0")
        if enabled != "1":
            return

        key_enc = await get_setting(db, "geoip_license_key", "")
        if not key_enc:
            return

        try:
            license_key = decrypt_value(key_enc)
        except Exception:
            license_key = key_enc

    result = await download_geolite2(license_key)

    if result["success"]:
        from database import set_setting
        async with AsyncSessionLocal() as db:
            await set_setting(db, "geoip_last_updated", datetime.utcnow().isoformat())
            await db.commit()
        logger.info("GeoIP database updated (%.1f MB)", result.get("size_mb", 0))
    else:
        logger.error("GeoIP update failed: %s", result.get("message", "unknown error"))


@instrument_job("dns_resolution")
async def resolve_host_dns():
    """Resolve DNS for hosts: rDNS for IP-only hosts, forward DNS for hostname-only hosts.
    Also merges duplicate scanner hosts into existing Proxmox/UniFi/agent hosts."""
    import asyncio
    import ipaddress
    import socket

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(PingHost))
        hosts = result.scalars().all()

        # Build IP → host index (prefer non-scanner sources)
        ip_to_host: dict[str, PingHost] = {}
        for h in hosts:
            if h.ip_address and h.source != "scanner":
                ip_to_host[h.ip_address] = h

        loop = asyncio.get_running_loop()
        changed = False
        to_delete: list[int] = []

        for h in hosts:
            hostname = (h.hostname or "").strip()
            if not hostname:
                continue

            # Check if hostname is an IP address
            is_ip = False
            try:
                ipaddress.ip_address(hostname)
                is_ip = True
            except ValueError:
                pass

            if is_ip:
                # Scanner/manual hosts with IP as hostname: try rDNS
                if h.source in ("scanner", "manual"):
                    # Check if this IP already belongs to a non-scanner host → merge
                    check_ip = h.ip_address or hostname
                    if check_ip in ip_to_host and ip_to_host[check_ip].id != h.id:
                        logger.info("DNS merge: removing duplicate scanner host %s (covered by %s)",
                                    h.name, ip_to_host[check_ip].name)
                        to_delete.append(h.id)
                        changed = True
                        continue

                    # Try reverse DNS to get a proper name (only if name is still the raw IP)
                    try:
                        rdns_result = await asyncio.wait_for(
                            loop.run_in_executor(None, socket.gethostbyaddr, hostname),
                            timeout=3.0
                        )
                        fqdn = rdns_result[0]
                        short = fqdn.split(".")[0] if "." in fqdn else fqdn
                        if short and short != hostname:
                            h.name = short
                            changed = True
                            logger.info("DNS resolve: %s → %s", hostname, short)
                    except (socket.herror, socket.gaierror, asyncio.TimeoutError, OSError):
                        pass

                # Ensure ip_address is set for IP-based hosts
                if not h.ip_address:
                    h.ip_address = hostname
                    changed = True

            else:
                # Hostname-based hosts (e.g. Proxmox): resolve forward DNS to set ip_address
                if not h.ip_address:
                    try:
                        ip = await asyncio.wait_for(
                            loop.run_in_executor(None, socket.gethostbyname, hostname),
                            timeout=3.0
                        )
                        h.ip_address = ip
                        changed = True
                        logger.info("DNS resolve: %s → %s", hostname, ip)
                    except (socket.herror, socket.gaierror, asyncio.TimeoutError, OSError):
                        pass

        # Delete merged duplicate hosts. ClickHouse ping_checks rows for the
        # dead host_id are intentionally NOT reassigned — they'll expire via
        # TTL within 30 days. Reassigning would require an ALTER UPDATE which
        # is async/eventually-consistent in CH and not worth the complexity.
        if to_delete:
            for dead_id in to_delete:
                await db.execute(delete(PingHost).where(PingHost.id == dead_id))

        if changed or to_delete:
            await db.commit()
            logger.info("DNS resolution job: updated hosts, deleted %d duplicates", len(to_delete))


@instrument_job("alert_rules")
async def run_alert_rules():
    """Evaluate all user-defined alert rules."""
    from services.rules import evaluate_rules
    async with AsyncSessionLocal() as db:
        triggered = await evaluate_rules(db)
        if triggered:
            logger.info("Alert rules: %d rule(s) triggered", triggered)


@instrument_job("legacy_api_key_cleanup")
async def cleanup_legacy_api_keys():
    """Disable API keys that still use legacy (plain SHA256) hashing and have
    not been touched in 30 days. The `hash_algo` column is set to 'hmac' both
    when a new key is minted and when an old key is migrated on successful
    auth; any row that is still NULL after 30d is either abandoned or unused
    legacy material and should not be a valid credential in production."""
    from models.api_key import ApiKey
    from sqlalchemy import update as sa_update
    from models.audit import AuditLog

    cutoff = datetime.utcnow() - timedelta(days=30)
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(ApiKey).where(
                ApiKey.hash_algo.is_(None),
                ApiKey.enabled == True,
                ApiKey.created_at < cutoff,
            )
        )
        stale = result.scalars().all()
        if not stale:
            return
        names = [k.name for k in stale]
        await db.execute(
            sa_update(ApiKey)
            .where(ApiKey.id.in_([k.id for k in stale]))
            .values(enabled=False)
        )
        db.add(AuditLog(
            username="system",
            action="api_key.legacy_auto_disabled",
            target_type="api_key",
            target_name=", ".join(names)[:256],
            details=f"Auto-disabled {len(stale)} legacy-hash key(s) unused for 30+ days",
        ))
        await db.commit()
    logger.warning(
        "Disabled %d legacy-algo API key(s) untouched for 30+ days: %s",
        len(stale), ", ".join(names),
    )


@instrument_job("backup_compliance")
async def run_backup_compliance():
    """Check all backup jobs for compliance (overdue/failed) and log issues."""
    from services.backup_monitor import check_backup_compliance
    async with AsyncSessionLocal() as db:
        issues = await check_backup_compliance(db)
        if issues:
            logger.warning("Backup compliance: %d issue(s) found", len(issues))
            for issue in issues:
                logger.warning(
                    "  Backup %s [%s]: %s (target=%s)",
                    issue.get("job_name", "?"),
                    issue.get("source_type", "?"),
                    issue.get("issue", "?"),
                    issue.get("target_name", "?"),
                )


async def start_scheduler():
    """Read intervals from DB, then register and start all jobs."""
    from database import get_setting

    async with AsyncSessionLocal() as db:
        ping_interval = int(await get_setting(db, "ping_interval", "60"))
        integration_interval = int(await get_setting(db, "proxmox_interval", "60"))

    scheduler.add_job(run_ping_checks, "interval", seconds=ping_interval,
                      id="ping_checks", replace_existing=True)
    scheduler.add_job(run_integration_checks, "interval", seconds=integration_interval,
                      id="integration_checks", replace_existing=True)
    scheduler.add_job(run_correlation, "interval", seconds=60,
                      id="correlation", replace_existing=True)
    scheduler.add_job(update_ssl_expiry, "interval", hours=6,
                      id="ssl_expiry", replace_existing=True)
    scheduler.add_job(cleanup_old_results, "cron", hour=3, minute=0,
                      id="cleanup", replace_existing=True)
    scheduler.add_job(run_log_intelligence, "interval", seconds=30,
                      id="log_intelligence", replace_existing=True)
    scheduler.add_job(run_snmp_polls, "interval", seconds=30,
                      id="snmp_polls", replace_existing=True)

    # Auto-discover & schedule MonitoringSource subclasses (replaces manual
    # scheduler.add_job entries for newer poll-style jobs).
    from services import sources as _sources_pkg
    from services.monitoring_source import get_registry, run_source
    _sources_pkg.discover_all()
    for src_name, src_cls in get_registry().items():
        if src_cls.interval_seconds is None:
            continue
        async def _runner(_cls=src_cls):
            await run_source(_cls)
        scheduler.add_job(
            _runner, "interval", seconds=src_cls.interval_seconds,
            id=f"src:{src_name}", replace_existing=True,
        )
        logger.info("MonitoringSource registered: %s (every %ds)",
                    src_name, src_cls.interval_seconds)
    scheduler.add_job(run_alert_rules, "interval", seconds=60,
                      id="alert_rules", replace_existing=True)
    scheduler.add_job(run_port_discovery, "interval", hours=6,
                      id="port_discovery", replace_existing=True)
    scheduler.add_job(check_disk_space, "interval", minutes=30,
                      id="disk_space", replace_existing=True)
    scheduler.add_job(run_backup_compliance, "interval", hours=1,
                      id="backup_compliance", replace_existing=True)
    scheduler.add_job(cleanup_legacy_api_keys, "cron", hour=3, minute=30,
                      id="legacy_api_key_cleanup", replace_existing=True)
    scheduler.add_job(cleanup_clickhouse_logs, "cron", hour=4, minute=0,
                      id="ch_cleanup", replace_existing=True)

    # DNS resolution + host dedup (every 30 minutes)
    scheduler.add_job(resolve_host_dns, "interval", minutes=30,
                      id="dns_resolve", replace_existing=True)

    # GeoIP database update (Tuesdays at 03:00 — MaxMind updates weekly on Tuesdays)
    scheduler.add_job(update_geoip, "cron",
                      day_of_week="tue", hour=3, minute=0,
                      id="geoip_update", replace_existing=True)

    # Weekly digest email (default: Monday 9:00, configurable)
    digest_day = int(await get_setting(db, "digest_day", "0"))  # 0=Mon
    digest_hour = int(await get_setting(db, "digest_hour", "9"))
    scheduler.add_job(run_weekly_digest, "cron",
                      day_of_week=digest_day, hour=digest_hour, minute=0,
                      id="weekly_digest", replace_existing=True)

    # Daily AI summary (default: 08:00, configurable)
    ai_summary_hour = int(await get_setting(db, "daily_ai_summary_hour", "8"))
    scheduler.add_job(run_daily_ai_summary, "cron",
                      hour=ai_summary_hour, minute=0,
                      id="daily_ai_summary", replace_existing=True)

    if getattr(config, "REDIS_URL", ""):
        # HA mode: start paused; the leadership loop resumes jobs once this
        # instance wins the Redis lease, and pauses them if it loses it.
        global _leader_task
        scheduler.start(paused=True)
        _leader_task = asyncio.create_task(_leadership_loop())
        logger.info("Scheduler started in leader-election mode (instance=%s)", _instance_id)
    else:
        # Single-process deployment: always leader, run jobs immediately.
        scheduler.start()

    # Seed default SNMP OIDs
    try:
        from services.snmp import seed_default_oids
        async with AsyncSessionLocal() as db:
            await seed_default_oids(db)
    except Exception:
        pass
    logger.info("Scheduler started (ping=%ds, integrations=%ds)",
                ping_interval, integration_interval)


def stop_scheduler():
    global _leader_task
    if _leader_task is not None:
        _leader_task.cancel()
        _leader_task = None
    # The Redis lease (if any) is left to expire via its TTL, which hands
    # leadership to a standby within _LEADER_TTL_SECONDS.
    scheduler.shutdown(wait=False)
