"""
REST API v1 – External JSON API with API key authentication.

All endpoints are under /api/v1/ and require an API key via
the `X-API-Key` HTTP header. Query-string keys are NOT accepted
(would leak through logs, browser history and proxies).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database import PingHost
from models.agent import Agent
from models.api_key import ApiKey
from models.audit import AuditLog
from models.base import get_db
from models.incident import Incident, IncidentEvent
from models.integration import IntegrationConfig, Snapshot
from services.clickhouse_client import query as ch_query, _where_clauses as ch_where
from ratelimit import rate_limit
from services import ping as ping_svc
from services import snapshot as snap_svc
from services.audit import log_action

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["API v1"])


# ── Auth dependency ──────────────────────────────────────────────────────────


def _hash_key(key: str) -> str:
    """HMAC-SHA256 with SECRET_KEY as pepper (preferred)."""
    import hmac
    from config import SECRET_KEY
    return hmac.new(SECRET_KEY.encode(), key.encode(), hashlib.sha256).hexdigest()


def _hash_key_legacy(key: str) -> str:
    """Plain SHA256 — kept for backward compatibility with existing keys."""
    return hashlib.sha256(key.encode()).hexdigest()


async def require_api_key(request: Request, db: AsyncSession = Depends(get_db)) -> ApiKey:
    """Validate API key from the X-API-Key header, or fall back to session auth."""
    # Header only: query-string keys would leak via proxy/access logs + browser history.
    key = request.headers.get("X-API-Key")
    if not key and request.query_params.get("api_key"):
        logger.warning(
            "Rejected API request with key in query-string from %s (path=%s). "
            "Clients must use the X-API-Key header.",
            request.client.host if request.client else "?",
            request.url.path,
        )
        raise HTTPException(
            status_code=400,
            detail="API key must be sent via the X-API-Key header. "
                   "Query-string authentication was removed for security reasons.",
        )
    if key:
        # Try HMAC hash first, fall back to legacy SHA256
        for hasher in (_hash_key, _hash_key_legacy):
            hashed = hasher(key)
            result = await db.execute(
                select(ApiKey).where(ApiKey.key_hash == hashed, ApiKey.enabled == True)
            )
            api_key = result.scalar_one_or_none()
            if api_key:
                # Migrate legacy key to HMAC on successful auth
                hmac_hash = _hash_key(key)
                if api_key.key_hash != hmac_hash:
                    api_key.key_hash = hmac_hash
                    logger.warning("Migrated legacy SHA256 API key '%s' to HMAC. Consider rotating this key.", api_key.name)
                # Mark the algo even when the hash already matched — legacy
                # rows created before migration column existed are NULL.
                if api_key.hash_algo != "hmac":
                    api_key.hash_algo = "hmac"
                api_key.last_used = datetime.utcnow()
                await db.commit()
                return api_key
        raise HTTPException(status_code=401, detail="Invalid or disabled API key.")

    # Fall back to session auth (set by middleware)
    user = getattr(request.state, "current_user", None)
    if user:
        # Create a virtual ApiKey-like object from the session user
        role = getattr(user, "role", "admin") or "admin"
        return ApiKey(id=0, name=f"session:{user.username}", key_hash="", prefix="session",
                      role=role, enabled=True)

    raise HTTPException(status_code=401, detail="API key or session required.")


async def require_editor(api_key: ApiKey = Depends(require_api_key)) -> ApiKey:
    if api_key.role not in ("editor", "admin"):
        raise HTTPException(status_code=403, detail="Editor or admin role required.")
    return api_key


async def require_admin(api_key: ApiKey = Depends(require_api_key)) -> ApiKey:
    if api_key.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required.")
    return api_key


# ── Integration device extraction ────────────────────────────────────────────


def _extract_device_data(int_type: str, raw: dict, host: PingHost) -> dict | None:
    """Extract device-specific data from an integration snapshot for the given host."""
    ip = (host.hostname or "").lower().strip()
    mac = (host.mac_address or "").lower().strip()

    if int_type == "unifi":
        # Match by IP or MAC in the devices list
        for d in raw.get("devices", []):
            d_ip = (d.get("ip") or "").lower().strip()
            d_mac = (d.get("mac") or "").lower().strip()
            if (ip and d_ip == ip) or (mac and d_mac == mac):
                # Find clients connected to this device
                connected_clients = []
                for c in raw.get("clients", []):
                    # Wireless clients connected to this AP
                    if c.get("ap_mac", "").lower() == d_mac:
                        connected_clients.append(c)
                    # Wired clients connected to this switch
                    elif (c.get("sw_mac") or "").lower() == d_mac:
                        connected_clients.append(c)
                return {
                    **{k: v for k, v in d.items()},
                    "connected_clients": connected_clients,
                }
        return None

    if int_type == "proxmox":
        # Match by hostname in VMs/containers
        name_lower = (host.name or "").lower()
        for vm in raw.get("vms", []) + raw.get("containers", []):
            vm_name = (vm.get("name") or "").lower()
            if vm_name == name_lower or vm_name == ip:
                return vm
        return None

    return None


# ── API Key Management ───────────────────────────────────────────────────────


@router.post("/keys", summary="Create API key", dependencies=[Depends(require_admin)])
async def create_api_key(request: Request, db: AsyncSession = Depends(get_db)):
    body = await request.json()
    name = (body.get("name") or "").strip()
    role = body.get("role", "readonly")
    note = body.get("note", "")
    if not name:
        raise HTTPException(400, "name is required")
    if role not in ("readonly", "editor", "admin"):
        raise HTTPException(400, "role must be readonly, editor, or admin")

    raw_key = f"ng_{os.urandom(24).hex()}"
    prefix = raw_key[:8]
    key_hash = _hash_key(raw_key)

    api_key = ApiKey(
        name=name, key_hash=key_hash, prefix=prefix,
        role=role, note=note, hash_algo="hmac",
    )
    db.add(api_key)
    await db.commit()
    return {"id": api_key.id, "key": raw_key, "name": name, "role": role,
            "note": "Store this key securely — it cannot be retrieved again."}


@router.get("/keys", summary="List API keys", dependencies=[Depends(require_admin)])
async def list_api_keys(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ApiKey).order_by(ApiKey.created_at.desc()))
    return [
        {"id": k.id, "name": k.name, "prefix": k.prefix, "role": k.role,
         "enabled": k.enabled, "last_used": k.last_used.isoformat() if k.last_used else None,
         "created_at": k.created_at.isoformat() if k.created_at else None}
        for k in result.scalars().all()
    ]


@router.delete("/keys/{key_id}", summary="Delete API key", dependencies=[Depends(require_admin)])
async def delete_api_key(key_id: int, db: AsyncSession = Depends(get_db)):
    key = await db.get(ApiKey, key_id)
    if not key:
        raise HTTPException(404, "API key not found")
    await db.delete(key)
    await db.commit()
    return {"ok": True}


# ── System ───────────────────────────────────────────────────────────────────


@router.get("/status", summary="System status overview")
async def system_status(
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_api_key),
):
    hosts_q = await db.execute(select(func.count(PingHost.id)))
    hosts_total = hosts_q.scalar() or 0
    hosts_enabled_q = await db.execute(
        select(func.count(PingHost.id)).where(PingHost.enabled == True)
    )
    hosts_enabled = hosts_enabled_q.scalar() or 0

    agents_q = await db.execute(select(func.count(Agent.id)))
    agents_total = agents_q.scalar() or 0

    integrations_q = await db.execute(select(func.count(IntegrationConfig.id)))
    integrations_total = integrations_q.scalar() or 0

    incidents_q = await db.execute(
        select(func.count(Incident.id)).where(Incident.status == "open")
    )
    open_incidents = incidents_q.scalar() or 0

    return {
        "hosts": {"total": hosts_total, "enabled": hosts_enabled},
        "agents": {"total": agents_total},
        "integrations": {"total": integrations_total},
        "incidents": {"open": open_incidents},
    }


# ── Hosts ────────────────────────────────────────────────────────────────────


@router.get("/hosts", summary="List all hosts with current status")
async def list_hosts(
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_api_key),
    status: str = Query(None, description="Filter: online, offline, maintenance, disabled"),
    source: str = Query(None, description="Filter by source: manual, proxmox, agent, phpipam"),
    enabled: bool = Query(None, description="Filter by enabled status"),
):
    q = select(PingHost).order_by(PingHost.name)
    if enabled is not None:
        q = q.where(PingHost.enabled == enabled)
    if source:
        q = q.where(PingHost.source == source)
    result = await db.execute(q)
    hosts = result.scalars().all()

    latest_map = await ping_svc.get_latest_by_host([h.id for h in hosts])
    uptime_map = await ping_svc.get_uptime_map()

    out = []
    for h in hosts:
        lr = latest_map.get(h.id)
        is_online = bool(lr.get("success")) if lr else None
        host_status = (
            "disabled" if not h.enabled
            else "maintenance" if h.maintenance
            else "online" if is_online
            else "offline" if is_online is False
            else "unknown"
        )
        if status and host_status != status:
            continue
        um = uptime_map.get(h.id, {})
        _lat = lr.get("latency_ms") if lr else None
        _ts = lr.get("timestamp") if lr else None
        out.append({
            "id": h.id,
            "name": h.name,
            "hostname": h.hostname,
            "status": host_status,
            "check_type": h.check_type or "icmp",
            "port": h.port,
            "source": h.source or "manual",
            "source_detail": h.source_detail,
            "latency_ms": round(float(_lat), 2) if _lat is not None else None,
            "last_check": _ts.isoformat() if _ts is not None else None,
            "uptime": {
                "h24": um.get("h24"),
                "d7": um.get("d7"),
                "d30": um.get("d30"),
            },
            "maintenance": h.maintenance or False,
            "enabled": h.enabled,
        })
    return out


@router.get("/hosts/{host_id}", summary="Host detail with latest metrics")
async def get_host(
    host_id: int,
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_api_key),
):
    host = await db.get(PingHost, host_id)
    if not host:
        raise HTTPException(404, "Host not found")

    from services.clickhouse_client import get_latest_ping_per_host, get_ping_uptime
    latest_map = await get_latest_ping_per_host([host_id])
    lr = latest_map.get(host_id)  # dict or None

    um = (await get_ping_uptime([host_id], hours=24)).get(host_id, {})

    # Agent metrics (if host is agent-sourced)
    agent_metrics = None
    if host.source == "agent":
        # Try matching by hostname (IP), name, or source_detail containing the agent hostname
        candidates = [s.lower() for s in [host.hostname or "", host.name or ""] if s]
        agent = None
        for cand in candidates:
            if not cand:
                continue
            agent_q = await db.execute(
                select(Agent).where(
                    (func.lower(Agent.hostname) == cand) | (func.lower(Agent.name) == cand)
                ).limit(1)
            )
            agent = agent_q.scalar_one_or_none()
            if agent:
                break
        # Fallback: match via source_detail (e.g. "auto-enrolled agent (PC-JULIAN)")
        if not agent and host.source_detail:
            import re
            m = re.search(r"\((.+?)\)", host.source_detail or "")
            if m:
                agent_name = m.group(1).lower()
                agent_q = await db.execute(
                    select(Agent).where(
                        (func.lower(Agent.hostname) == agent_name) | (func.lower(Agent.name) == agent_name)
                    ).limit(1)
                )
                agent = agent_q.scalar_one_or_none()
        if agent:
            from services.clickhouse_client import get_latest_agent_metrics
            snaps = await get_latest_agent_metrics([agent.id])
            snap = snaps.get(agent.id)
            agent_metrics = {
                "agent_id": agent.id,
                "agent_name": agent.name,
                "platform": agent.platform,
                "arch": agent.arch,
                "agent_version": agent.agent_version,
                "last_seen": agent.last_seen.isoformat() if agent.last_seen else None,
                "cpu_pct": snap.get("cpu_pct") if snap else None,
                "mem_pct": snap.get("mem_pct") if snap else None,
                "mem_used_mb": snap.get("mem_used_mb") if snap else None,
                "mem_total_mb": snap.get("mem_total_mb") if snap else None,
                "disk_pct": snap.get("disk_pct") if snap else None,
                "load_1": snap.get("load_1") if snap else None,
                "load_5": snap.get("load_5") if snap else None,
                "load_15": snap.get("load_15") if snap else None,
                "uptime_s": snap.get("uptime_s") if snap else None,
                "rx_bytes": snap.get("rx_bytes") if snap else None,
                "tx_bytes": snap.get("tx_bytes") if snap else None,
                "snapshot_time": snap["timestamp"].isoformat() if snap and snap.get("timestamp") else None,
                "extra": json.loads(snap["data_json"]) if snap and snap.get("data_json") else None,
            }

    # Integration snapshots (for non-agent hosts with integration data)
    integration_data = None
    if host.source and host.source not in ("manual", "agent"):
        int_type = host.source
        # Find config by type + match hostname in latest snapshot
        configs_q = await db.execute(
            select(IntegrationConfig).where(
                IntegrationConfig.type == int_type,
                IntegrationConfig.enabled == True,
            )
        )
        type_snaps = await snap_svc.get_latest_batch(db, int_type)
        for cfg in configs_q.scalars().all():
            snap = type_snaps.get(cfg.id)
            if snap and snap.data_json:
                raw = json.loads(snap.data_json) if isinstance(snap.data_json, str) else snap.data_json
                device_data = _extract_device_data(int_type, raw, host)
                integration_data = {
                    "type": int_type,
                    "config_id": cfg.id,
                    "config_name": cfg.name,
                    "ok": snap.ok,
                    "timestamp": snap.timestamp.isoformat(),
                    "data": device_data if device_data else raw,
                    "device": device_data,
                }
                break

    # ── Health score (0.0 = healthy, 1.0 = critical) ──────────────────────
    _online = bool(lr.get("success")) if lr else None
    _lat = lr.get("latency_ms") if lr else None
    _thr = host.latency_threshold_ms
    if _online is False:
        health_score = 1.0
    elif host.maintenance:
        health_score = 0.5
    elif _online is None:
        health_score = 0.8
    else:
        _hs = 0.0
        if _lat is not None and _thr:
            r = _lat / _thr
            if r <= 0.5: _hs += r * 0.05
            elif r <= 0.8: _hs += 0.025 + (r - 0.5) / 0.3 * 0.075
            elif r <= 1.0: _hs += 0.10 + (r - 0.8) / 0.2 * 0.10
            else: _hs += 0.20
        elif _lat is not None:
            _hs += min(_lat / 200.0, 0.20)
        uptime_24h = um.get("uptime_pct") or 100.0
        deficit = 1 - uptime_24h / 100.0
        if deficit > 0:
            _hs += min((deficit ** 0.5) * 0.15, 0.15)
        health_score = round(min(_hs, 1.0), 3)
    health_pct = round((1 - health_score) * 100)

    return {
        "id": host.id,
        "name": host.name,
        "hostname": host.hostname,
        "check_type": host.check_type or "icmp",
        "port": host.port,
        "enabled": host.enabled,
        "maintenance": host.maintenance or False,
        "maintenance_until": host.maintenance_until.isoformat() if host.maintenance_until else None,
        "source": host.source or "manual",
        "source_detail": host.source_detail,
        "latency_threshold_ms": host.latency_threshold_ms,
        "ssl_expiry_days": host.ssl_expiry_days,
        "mac_address": host.mac_address,
        "parent_id": host.parent_id,
        "port_error": host.port_error or False,
        "check_detail": json.loads(host.check_detail) if host.check_detail else None,
        "created_at": host.created_at.isoformat() if host.created_at else None,
        "latest": {
            "online": bool(lr.get("success")) if lr else None,
            "latency_ms": round(float(lr["latency_ms"]), 2) if lr and lr.get("latency_ms") is not None else None,
            "timestamp": lr["timestamp"].isoformat() if lr and lr.get("timestamp") else None,
        },
        "uptime": {"h24": um.get("h24"), "d7": um.get("d7"), "d30": um.get("d30")},
        "health_score": health_score,
        "health_pct": health_pct,
        "agent": agent_metrics,
        "integration": integration_data,
    }


@router.get("/hosts/{host_id}/history", summary="Ping history for a host")
async def host_history(
    host_id: int,
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_api_key),
    hours: int = Query(24, ge=1, le=720, description="Hours of history (max 720 = 30d)"),
    limit: int = Query(500, ge=1, le=5000),
):
    host = await db.get(PingHost, host_id)
    if not host:
        raise HTTPException(404, "Host not found")

    from services.clickhouse_client import get_ping_history
    rows = await get_ping_history(host_id, hours=hours)
    if limit and len(rows) > limit:
        rows = rows[-limit:]
    return {
        "host_id": host_id,
        "count": len(rows),
        "results": [
            {
                "timestamp": r["timestamp"].isoformat() if isinstance(r["timestamp"], datetime) else r["timestamp"],
                "success": bool(r["success"]),
                "latency_ms": round(float(r["latency_ms"]), 2) if r.get("latency_ms") is not None else None,
            }
            for r in rows
        ],
    }


@router.get("/hosts/{host_id}/timeline", summary="Merged event timeline for a host")
async def host_timeline(
    host_id: int,
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_api_key),
    hours: int = Query(24, ge=1, le=720, description="Lookback window in hours"),
    sources: str = Query(
        "status,incident,syslog",
        description="Comma-separated: status, incident, syslog",
    ),
    severity_max: int = Query(
        4, ge=0, le=7,
        description="Syslog max severity (4=warning, 3=error, 2=critical)",
    ),
    limit: int = Query(200, ge=10, le=1000),
):
    """Return a time-sorted merged event stream for one host.

    Merges three sources:
    - `status`  — ping success transitions (online↔offline flips) from CH ping_checks
    - `incident` — correlation incidents whose events reference this host name
    - `syslog` — syslog messages ≤ severity_max matching host_id/hostname/source_ip

    Response rows share a common shape: {ts, type, severity, title, summary,
    details{}}. Newest first. Useful for root-cause analysis — scan the few
    minutes before an offline event for the syslog line that explains it.
    """
    host = await db.get(PingHost, host_id)
    if not host:
        raise HTTPException(404, "Host not found")

    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    wanted = {s.strip() for s in sources.split(",") if s.strip()}
    events: list[dict] = []

    # ── Ping status transitions (CH) ────────────────────────────────────────
    if "status" in wanted:
        from services.clickhouse_client import get_ping_status_transitions
        try:
            transitions = await get_ping_status_transitions(host_id, hours=hours)
        except Exception:
            transitions = []
        for t in transitions:
            success = bool(t.get("success"))
            prev = t.get("prev_success")
            latency = t.get("latency_ms")
            if prev is None:
                title = "Initial state: online" if success else "Initial state: offline"
                severity = "info" if success else "critical"
            elif success:
                title = "Host came back online"
                severity = "info"
            else:
                title = "Host went offline"
                severity = "critical"
            events.append({
                "ts": t["timestamp"].isoformat() if isinstance(t["timestamp"], datetime) else t["timestamp"],
                "type": "status",
                "severity": severity,
                "title": title,
                "summary": (
                    f"Latency {round(float(latency), 1)}ms" if success and latency is not None
                    else "No response" if not success
                    else None
                ),
                "details": {
                    "success": success,
                    "latency_ms": round(float(latency), 2) if latency is not None else None,
                },
            })

    # ── Correlation incidents (Postgres) ────────────────────────────────────
    if "incident" in wanted and host.name:
        # Match incidents whose events mention this host's name. Mirrors
        # the pattern used by /api/v1/incidents?host_name=…
        iq = await db.execute(
            select(Incident)
            .join(IncidentEvent, IncidentEvent.incident_id == Incident.id)
            .where(
                Incident.created_at >= since,
                IncidentEvent.summary.ilike(f"%{host.name}%"),
            )
            .order_by(Incident.created_at.desc())
            .distinct()
            .limit(limit)
        )
        for inc in iq.scalars().all():
            events.append({
                "ts": inc.created_at.isoformat() if inc.created_at else None,
                "type": "incident",
                "severity": inc.severity or "info",
                "title": inc.title or inc.rule or "Incident",
                "summary": (
                    f"Resolved {inc.resolved_at.strftime('%H:%M')}"
                    if inc.resolved_at else f"Status: {inc.status or 'open'}"
                ),
                "details": {
                    "incident_id": inc.id,
                    "rule": inc.rule,
                    "status": inc.status,
                    "resolved_at": inc.resolved_at.isoformat() if inc.resolved_at else None,
                },
            })

    # ── Syslog matches (CH) ─────────────────────────────────────────────────
    if "syslog" in wanted:
        from services.clickhouse_client import get_syslog_events_for_host
        try:
            rows = await get_syslog_events_for_host(
                host_id=host_id,
                host_name=host.name or "",
                host_source_ip=host.hostname or "",  # hostname col holds IP/DNS target
                since=since,
                max_severity=severity_max,
                limit=limit,
            )
        except Exception:
            rows = []
        # syslog severity int → label
        sev_map = {
            0: "critical", 1: "critical", 2: "critical",
            3: "error", 4: "warning", 5: "info", 6: "info", 7: "info",
        }
        for r in rows:
            sev_int = int(r.get("severity") or 6)
            events.append({
                "ts": r["received_at"].isoformat() if isinstance(r["received_at"], datetime) else r["received_at"],
                "type": "syslog",
                "severity": sev_map.get(sev_int, "info"),
                "title": (r.get("app_name") or "syslog") + (f": {r.get('message', '')[:80]}" if r.get("message") else ""),
                "summary": f"{r.get('hostname') or ''} · sev {sev_int}",
                "details": {
                    "severity_int": sev_int,
                    "facility": r.get("facility"),
                    "hostname": r.get("hostname"),
                    "app_name": r.get("app_name"),
                    "message": r.get("message"),
                },
            })

    # Sort merged stream newest first and cap
    events.sort(key=lambda e: e.get("ts") or "", reverse=True)
    if len(events) > limit:
        events = events[:limit]

    return {
        "host_id": host_id,
        "host_name": host.name,
        "hours": hours,
        "since": since.isoformat(),
        "sources": sorted(wanted),
        "total": len(events),
        "events": events,
    }


@router.post("/hosts", summary="Create a new host")
async def create_host(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_editor),
):
    body = await request.json()
    name = (body.get("name") or "").strip()
    hostname = (body.get("hostname") or "").strip()
    if not name or not hostname:
        raise HTTPException(400, "name and hostname are required")

    host = PingHost(
        name=name,
        hostname=hostname,
        check_type=body.get("check_type", "icmp"),
        port=body.get("port"),
        latency_threshold_ms=body.get("latency_threshold_ms"),
        enabled=body.get("enabled", True),
    )
    db.add(host)
    await db.commit()
    await log_action(db, request, "host.create", "host", host.id, host.name)
    await db.commit()
    return {"id": host.id, "name": host.name, "hostname": host.hostname}


@router.patch("/hosts/{host_id}", summary="Update a host")
async def update_host(
    host_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_editor),
):
    host = await db.get(PingHost, host_id)
    if not host:
        raise HTTPException(404, "Host not found")
    body = await request.json()
    old_check_type = host.check_type
    for field in ("name", "hostname", "check_type", "port", "latency_threshold_ms",
                  "enabled", "maintenance", "maintenance_until"):
        if field in body:
            val = body[field]
            if field == "maintenance_until" and isinstance(val, str):
                val = datetime.fromisoformat(val.replace("Z", "+00:00")).replace(tzinfo=None)
            setattr(host, field, val)
    # Reset port_error state when check types change
    if "check_type" in body and body["check_type"] != old_check_type:
        host.port_error = False
        host.check_detail = None
    await db.commit()
    return {"ok": True, "id": host.id}


@router.patch("/hosts/bulk", summary="Bulk update hosts")
async def bulk_update_hosts(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_editor),
):
    body = await request.json()
    host_ids = body.get("ids", [])
    updates = body.get("updates", {})
    if not host_ids or not updates:
        raise HTTPException(400, "ids and updates required")

    allowed_fields = {"check_type", "enabled", "latency_threshold_ms"}
    for field, value in updates.items():
        if field not in allowed_fields:
            continue
        await db.execute(
            update(PingHost)
            .where(PingHost.id.in_(host_ids))
            .values(**{field: value})
        )
    await db.commit()
    return {"ok": True, "updated": len(host_ids)}


@router.delete("/hosts/{host_id}", summary="Delete a host")
async def delete_host(
    host_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_editor),
):
    host = await db.get(PingHost, host_id)
    if not host:
        raise HTTPException(404, "Host not found")
    host_name = host.name
    await db.delete(host)
    await log_action(db, request, "host.delete", "host", host_id, host_name)
    await db.commit()
    return {"ok": True}


# ── Agents ───────────────────────────────────────────────────────────────────


@router.get("/agents", summary="List all agents")
async def list_agents(
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_api_key),
):
    result = await db.execute(select(Agent).order_by(Agent.name))
    agents = result.scalars().all()
    now = datetime.utcnow()

    # Fetch latest snapshot per agent from ClickHouse
    from services.clickhouse_client import get_latest_agent_metrics
    snaps_by_agent = await get_latest_agent_metrics([a.id for a in agents])

    # Lookup PingHost ids for agent-sourced hosts (to link agent → host detail)
    host_by_name: dict[str, int] = {}
    if agents:
        ph_rows = (await db.execute(
            select(PingHost.id, PingHost.hostname, PingHost.name)
            .where(PingHost.source == "agent")
        )).all()
        for ph in ph_rows:
            host_by_name[ph.hostname.lower()] = ph.id
            host_by_name[ph.name.lower()] = ph.id

    out = []
    for a in agents:
        s = snaps_by_agent.get(a.id)
        # Find linked host by agent hostname
        host_id = None
        if a.hostname:
            host_id = host_by_name.get(a.hostname.lower())
        out.append({
            "id": a.id,
            "name": a.name,
            "hostname": a.hostname,
            "platform": a.platform,
            "arch": a.arch,
            "agent_version": a.agent_version,
            "online": a.last_seen is not None and (now - a.last_seen).total_seconds() < 120,
            "last_seen": a.last_seen.isoformat() if a.last_seen else None,
            "enabled": a.enabled,
            "cpu_pct": s.get("cpu_pct") if s else None,
            "mem_pct": s.get("mem_pct") if s else None,
            "disk_pct": s.get("disk_pct") if s else None,
            "host_id": host_id,
        })
    return out


@router.get("/agents/{agent_id}", summary="Agent detail with latest metrics")
async def get_agent(
    agent_id: int,
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_api_key),
):
    agent = await db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    from services.clickhouse_client import get_agent_history
    snaps = await get_agent_history(agent_id, limit=60)

    now = datetime.utcnow()
    return {
        "id": agent.id,
        "name": agent.name,
        "hostname": agent.hostname,
        "platform": agent.platform,
        "arch": agent.arch,
        "agent_version": agent.agent_version,
        "online": agent.last_seen is not None and (now - agent.last_seen).total_seconds() < 120,
        "last_seen": agent.last_seen.isoformat() if agent.last_seen else None,
        "enabled": agent.enabled,
        "log_levels": agent.log_levels or "",
        "log_channels": agent.log_channels or "",
        "log_file_paths": agent.log_file_paths or "",
        "agent_log_level": agent.agent_log_level or "errors",
        "snapshots": [
            {
                "agent_id": agent_id,
                "timestamp": s["timestamp"].isoformat() if isinstance(s.get("timestamp"), datetime) else s.get("timestamp"),
                "cpu_pct": s.get("cpu_pct"),
                "mem_pct": s.get("mem_pct"),
                "mem_used_mb": s.get("mem_used_mb"),
                "mem_total_mb": s.get("mem_total_mb"),
                "disk_pct": s.get("disk_pct"),
                "uptime_s": s.get("uptime_s"),
                "data_json": json.loads(s["data_json"]) if s.get("data_json") else None,
            }
            for s in snaps
        ],
    }


@router.patch("/agents/{agent_id}", summary="Update agent log settings")
async def patch_agent(
    agent_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_api_key),
):
    agent = await db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    body = await request.json()

    allowed = {"log_levels", "log_channels", "log_file_paths", "agent_log_level", "enabled"}
    updates = {k: v for k, v in body.items() if k in allowed}

    if "agent_log_level" in updates and updates["agent_log_level"] not in ("off", "errors", "all"):
        raise HTTPException(400, "agent_log_level must be 'off', 'errors', or 'all'")

    if updates:
        await db.execute(update(Agent).where(Agent.id == agent_id).values(**updates))
        await db.commit()

    await db.refresh(agent)
    return {
        "ok": True,
        "log_levels": agent.log_levels or "",
        "log_channels": agent.log_channels or "",
        "log_file_paths": agent.log_file_paths or "",
        "agent_log_level": agent.agent_log_level or "errors",
    }


@router.delete("/agents/{agent_id}", summary="Delete / decommission an agent")
async def delete_agent(
    agent_id: int,
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_api_key),
):
    agent = await db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    # Clean up related PingHost (ClickHouse time-series rows expire via TTL)
    from sqlalchemy import delete as sa_delete
    if agent.hostname:
        hn = agent.hostname.lower()
        ph = await db.execute(
            select(PingHost).where(
                ((func.lower(PingHost.hostname) == hn) | (func.lower(PingHost.name) == hn)),
                PingHost.source == "agent"
            )
        )
        ping_host = ph.scalar_one_or_none()
        if ping_host:
            await db.execute(sa_delete(PingHost).where(PingHost.id == ping_host.id))

    await db.execute(sa_delete(Agent).where(Agent.id == agent_id))
    await db.commit()
    return {"ok": True}


@router.post("/agents/{agent_id}/uninstall", summary="Queue remote uninstall command for an agent")
async def uninstall_agent(
    agent_id: int,
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_api_key),
):
    agent = await db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    agent.pending_command = "uninstall"
    await db.commit()
    return {"ok": True, "message": f"Uninstall command queued for {agent.name}"}


# ── Integrations ─────────────────────────────────────────────────────────────


@router.get("/integrations", summary="List all integration instances with latest status")
async def list_integrations(
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_api_key),
    type: str = Query(None, description="Filter by type: proxmox, unifi, pihole, etc."),
):
    q = select(IntegrationConfig).order_by(IntegrationConfig.type, IntegrationConfig.name)
    if type:
        q = q.where(IntegrationConfig.type == type)
    result = await db.execute(q)
    configs = result.scalars().all()

    # Get latest snapshot for each (single query instead of one per config)
    all_snaps = await snap_svc.get_latest_batch_all(db)

    from scheduler import _STANDBY_MARKER
    out = []
    for cfg in configs:
        snap = all_snaps.get(cfg.type, {}).get(cfg.id)
        # Standby members write ok=True with the standby marker in `error`.
        # Surface that as a separate UI state — not "error", not "ok primary".
        is_standby = bool(snap and snap.ok and snap.error == _STANDBY_MARKER)
        if is_standby:
            status = "standby"
            display_error = None
        elif snap and snap.ok:
            status = "ok"
            display_error = None
        elif snap:
            status = "error"
            display_error = snap.error
        else:
            status = "no_data"
            display_error = None

        out.append({
            "id": cfg.id,
            "type": cfg.type,
            "name": cfg.name,
            "enabled": cfg.enabled,
            "cluster_group": cfg.cluster_group,
            "is_standby": is_standby,
            "created_at": cfg.created_at.isoformat() if hasattr(cfg, "created_at") and cfg.created_at else None,
            "status": status,
            "last_check": snap.timestamp.isoformat() if snap else None,
            "error": display_error,
        })
    return out


@router.get("/integrations/{config_id}", summary="Integration detail with snapshot data")
async def get_integration(
    config_id: int,
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_api_key),
):
    cfg = await db.get(IntegrationConfig, config_id)
    if not cfg:
        raise HTTPException(404, "Integration not found")
    snap = await snap_svc.get_latest(db, cfg.type, cfg.id)
    data = json.loads(snap.data_json) if snap and snap.data_json else None

    from scheduler import _STANDBY_MARKER
    is_standby = bool(snap and snap.ok and snap.error == _STANDBY_MARKER)
    return {
        "id": cfg.id,
        "entity_type": cfg.type,
        "entity_id": cfg.id,
        "name": cfg.name,
        "enabled": cfg.enabled,
        "cluster_group": cfg.cluster_group,
        "is_standby": is_standby,
        "ok": snap.ok if snap else False,
        "timestamp": snap.timestamp.isoformat() if snap else None,
        "error": (snap.error if snap and not snap.ok else None) if not is_standby else None,
        "data_json": data,
    }


# ── Incidents ────────────────────────────────────────────────────────────────


@router.get("/incidents", summary="List incidents")
async def list_incidents(
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_api_key),
    status: str = Query(None, description="Filter: open, acknowledged, resolved"),
    severity: str = Query(None, description="Filter: critical, warning, info"),
    search: str = Query(None, description="Search in title"),
    host_name: str = Query(None, description="Filter by host name in event summaries"),
    limit: int = Query(50, ge=1, le=500),
):
    if host_name:
        # Join with events and find incidents that mention this host
        q = (
            select(Incident)
            .join(IncidentEvent, IncidentEvent.incident_id == Incident.id)
            .where(IncidentEvent.summary.ilike(f"%{host_name}%"))
            .group_by(Incident.id)
            .order_by(Incident.updated_at.desc())
            .limit(limit)
        )
    else:
        q = select(Incident).order_by(Incident.updated_at.desc()).limit(limit)
    if status:
        q = q.where(Incident.status == status)
    if severity:
        q = q.where(Incident.severity == severity)
    if search:
        q = q.where(Incident.title.ilike(f"%{search}%"))
    result = await db.execute(q)
    incidents = result.scalars().all()

    # Fetch latest non-system event summary per incident (explains the WHY)
    incident_ids = [i.id for i in incidents]
    summary_map: dict[int, str] = {}
    if incident_ids:
        # Get the most recent event with a meaningful event_type (not "acknowledged"/"resolved")
        events_q = await db.execute(
            select(IncidentEvent)
            .where(
                IncidentEvent.incident_id.in_(incident_ids),
                IncidentEvent.event_type.notin_(["acknowledged", "resolved"]),
            )
            .order_by(IncidentEvent.timestamp.desc())
        )
        for ev in events_q.scalars().all():
            if ev.incident_id not in summary_map:
                summary_map[ev.incident_id] = ev.summary

    return [
        {
            "id": i.id,
            "rule": i.rule,
            "title": i.title,
            "severity": i.severity,
            "status": i.status,
            "summary": summary_map.get(i.id),
            "created_at": i.created_at.isoformat(),
            "updated_at": i.updated_at.isoformat(),
            "resolved_at": i.resolved_at.isoformat() if i.resolved_at else None,
            "acknowledged_by": i.acknowledged_by,
        }
        for i in incidents
    ]


SEVERITY_NAMES = {0: "Emergency", 1: "Alert", 2: "Critical", 3: "Error"}


async def _analyze_incident_logs(
    db: AsyncSession, logs: list[dict], window_start, window_end,
) -> dict:
    """Analyze related syslog messages and produce a structured summary."""
    from collections import Counter

    from models.log_template import LogTemplate, PrecursorPattern

    total = len(logs)

    # ── Group by template hash ───────────────────────────────────────
    by_hash: dict[str, list[dict]] = {}
    for log in logs:
        h = log.get("template_hash", "") or "unknown"
        by_hash.setdefault(h, []).append(log)

    # Look up known templates from PostgreSQL
    known_hashes = [h for h in by_hash if h != "unknown"]
    tpl_map: dict[str, LogTemplate] = {}
    if known_hashes:
        tpl_rows = (await db.execute(
            select(LogTemplate).where(LogTemplate.template_hash.in_(known_hashes))
        )).scalars().all()
        tpl_map = {t.template_hash: t for t in tpl_rows}

    # ── Build pattern groups ─────────────────────────────────────────
    patterns = []
    for h, group in sorted(by_hash.items(), key=lambda x: -len(x[1])):
        tpl = tpl_map.get(h)
        hostnames = Counter(l["hostname"] for l in group)
        severities = Counter(l["severity"] for l in group)
        apps = Counter(l["app_name"] for l in group if l.get("app_name"))

        first_ts = min(l["timestamp"] for l in group)
        last_ts = max(l["timestamp"] for l in group)

        pattern: dict = {
            "count": len(group),
            "template": tpl.template if tpl else group[0]["message"][:120],
            "example": group[0]["message"],
            "hosts": [{"name": name, "count": cnt} for name, cnt in hostnames.most_common(5)],
            "apps": [{"name": name, "count": cnt} for name, cnt in apps.most_common(3)],
            "severity_breakdown": {SEVERITY_NAMES.get(s, f"sev{s}"): c for s, c in severities.items()},
            "first_seen": first_ts,
            "last_seen": last_ts,
            "is_known": tpl is not None,
            "noise_score": tpl.noise_score if tpl else None,
            "tags": tpl.tags.split(",") if tpl and tpl.tags else [],
            "avg_rate_per_hour": tpl.avg_rate_per_hour if tpl else None,
        }
        patterns.append(pattern)

    # ── Host distribution ────────────────────────────────────────────
    host_counts = Counter(l["hostname"] for l in logs)
    top_hosts = [{"name": name, "count": cnt} for name, cnt in host_counts.most_common(10)]
    single_source = len(host_counts) == 1

    # ── Severity distribution ────────────────────────────────────────
    sev_counts = Counter(l["severity"] for l in logs)
    worst_severity = min(sev_counts.keys()) if sev_counts else 3

    # ── Generate summary text ────────────────────────────────────────
    summary_parts = []

    # Headline
    n_patterns = len(patterns)
    n_hosts = len(host_counts)
    summary_parts.append(
        f"{total} error messages matching {n_patterns} distinct pattern{'s' if n_patterns != 1 else ''} "
        f"from {n_hosts} host{'s' if n_hosts != 1 else ''}."
    )

    # Single source?
    if single_source:
        host = list(host_counts.keys())[0]
        summary_parts.append(f"All errors originate from {host} — likely a localized issue on this host.")

    # Dominant pattern?
    if patterns:
        top = patterns[0]
        pct = round(top["count"] / total * 100)
        if pct >= 60:
            summary_parts.append(
                f"Dominant pattern ({pct}% of errors): \"{top['template'][:80]}\"."
            )
            if top["noise_score"] is not None and top["noise_score"] >= 70:
                summary_parts.append(
                    "This pattern has a high noise score — it may be a known recurring issue rather than a new problem."
                )
            if top["avg_rate_per_hour"] is not None and top["avg_rate_per_hour"] > 0:
                current_rate = top["count"] * 12  # extrapolate from 5min to 1h
                ratio = current_rate / top["avg_rate_per_hour"] if top["avg_rate_per_hour"] > 0 else 0
                if ratio > 5:
                    summary_parts.append(
                        f"Current rate is ~{ratio:.0f}x the normal baseline ({top['avg_rate_per_hour']:.0f}/hr) — this is a significant spike."
                    )

        # New/unknown patterns?
        new_patterns = [p for p in patterns if not p["is_known"]]
        if new_patterns:
            summary_parts.append(
                f"{len(new_patterns)} pattern{'s are' if len(new_patterns) != 1 else ' is'} "
                f"previously unseen — may indicate a new failure mode."
            )

    # Critical severity?
    if worst_severity <= 1:
        summary_parts.append(
            f"Contains {SEVERITY_NAMES[worst_severity]}-level messages — immediate attention recommended."
        )

    # Precursor check
    precursor_hints = []
    try:
        if known_hashes:
            tpl_ids = [t.id for t in tpl_map.values()]
            if tpl_ids:
                prec_rows = (await db.execute(
                    select(PrecursorPattern, LogTemplate)
                    .join(LogTemplate, PrecursorPattern.template_id == LogTemplate.id)
                    .where(
                        PrecursorPattern.template_id.in_(tpl_ids),
                        PrecursorPattern.confidence >= 0.3,
                    )
                    .order_by(PrecursorPattern.confidence.desc())
                    .limit(3)
                )).all()
                for pp, lt in prec_rows:
                    precursor_hints.append({
                        "template": lt.template,
                        "precedes": pp.precedes_event,
                        "confidence": round(pp.confidence * 100),
                        "lead_time_min": round(pp.avg_lead_time_sec / 60, 1) if pp.avg_lead_time_sec else None,
                    })
                if precursor_hints:
                    summary_parts.append(
                        f"These patterns have historically preceded {precursor_hints[0]['precedes']} events "
                        f"({precursor_hints[0]['confidence']}% confidence)."
                    )
    except Exception:
        pass

    return {
        "summary": " ".join(summary_parts),
        "total_messages": total,
        "unique_patterns": n_patterns,
        "affected_hosts": n_hosts,
        "single_source": single_source,
        "worst_severity": SEVERITY_NAMES.get(worst_severity, f"sev{worst_severity}"),
        "patterns": patterns,
        "top_hosts": top_hosts,
        "precursor_hints": precursor_hints,
    }


@router.get("/incidents/{incident_id}", summary="Incident detail with events timeline")
async def get_incident(
    incident_id: int,
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_api_key),
):
    incident = await db.get(Incident, incident_id)
    if not incident:
        raise HTTPException(404, "Incident not found")

    events_q = await db.execute(
        select(IncidentEvent).where(IncidentEvent.incident_id == incident_id)
        .order_by(IncidentEvent.timestamp.asc())
    )
    events = events_q.scalars().all()

    # Fetch related syslog entries around the incident timeframe
    related_logs = []
    log_analysis = None
    try:
        from services.clickhouse_client import query as ch_query
        start = incident.created_at - timedelta(minutes=5)
        end = (incident.resolved_at or datetime.utcnow()) + timedelta(minutes=5)
        rows = await ch_query(
            "SELECT timestamp, hostname, severity, app_name, message, template_hash "
            "FROM syslog_messages "
            "WHERE timestamp >= {t0:DateTime64(3)} AND timestamp <= {t1:DateTime64(3)} "
            "AND severity <= 3 "
            "ORDER BY timestamp DESC LIMIT 100",
            {"t0": start, "t1": end},
        )
        for r in rows:
            ts = r.get("timestamp")
            related_logs.append({
                "timestamp": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
                "hostname": r.get("hostname", ""),
                "severity": r.get("severity", 0),
                "app_name": r.get("app_name", ""),
                "message": r.get("message", ""),
                "template_hash": r.get("template_hash", ""),
            })

        # Build automatic analysis
        if related_logs:
            log_analysis = await _analyze_incident_logs(db, related_logs, start, end)
    except Exception:
        pass

    return {
        "id": incident.id,
        "rule": incident.rule,
        "title": incident.title,
        "severity": incident.severity,
        "status": incident.status,
        "created_at": incident.created_at.isoformat(),
        "updated_at": incident.updated_at.isoformat(),
        "resolved_at": incident.resolved_at.isoformat() if incident.resolved_at else None,
        "acknowledged_by": incident.acknowledged_by,
        "postmortem": incident.postmortem,
        "postmortem_generated_at": incident.postmortem_generated_at.isoformat() if incident.postmortem_generated_at else None,
        "feedback": incident.feedback,
        "feedback_at": incident.feedback_at.isoformat() if incident.feedback_at else None,
        "feedback_by": incident.feedback_by,
        "events": [
            {
                "timestamp": e.timestamp.isoformat(),
                "type": e.event_type,
                "summary": e.summary,
                "detail": e.detail,
            }
            for e in events
        ],
        "related_logs": related_logs,
        "log_analysis": log_analysis,
    }


@router.post("/incidents/{incident_id}/acknowledge", summary="Acknowledge an incident")
async def acknowledge_incident(
    incident_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_editor),
):
    incident = await db.get(Incident, incident_id)
    if not incident:
        raise HTTPException(404, "Incident not found")
    if incident.status == "resolved":
        raise HTTPException(400, "Incident is already resolved")

    body = await request.json()
    incident.status = "acknowledged"
    incident.acknowledged_by = body.get("by", _key.name)
    incident.updated_at = datetime.utcnow()

    db.add(IncidentEvent(
        incident_id=incident_id,
        event_type="acknowledged",
        summary=f"Acknowledged by {incident.acknowledged_by}",
    ))
    await log_action(db, request, "incident.acknowledge", "incident", incident_id, incident.title)
    await db.commit()
    return {"ok": True, "status": "acknowledged"}


@router.post("/incidents/{incident_id}/resolve", summary="Resolve an incident")
async def resolve_incident(
    incident_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_editor),
):
    incident = await db.get(Incident, incident_id)
    if not incident:
        raise HTTPException(404, "Incident not found")

    incident.status = "resolved"
    incident.resolved_at = datetime.utcnow()
    incident.updated_at = datetime.utcnow()

    db.add(IncidentEvent(
        incident_id=incident_id,
        event_type="resolved",
        summary=f"Resolved via API by {_key.name}",
    ))
    await log_action(db, request, "incident.resolve", "incident", incident_id, incident.title)
    await db.commit()
    try:
        from services.postmortem import generate_postmortem
        asyncio.create_task(generate_postmortem(incident.id))
    except Exception:
        pass
    return {"ok": True, "status": "resolved"}


@router.post("/incidents/{incident_id}/postmortem", summary="Generate or regenerate postmortem")
async def regenerate_postmortem(
    incident_id: int,
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_editor),
):
    incident = await db.get(Incident, incident_id)
    if not incident:
        raise HTTPException(404, "Incident not found")
    if incident.status != "resolved":
        raise HTTPException(400, "Postmortem can only be generated for resolved incidents")

    try:
        from services.postmortem import generate_postmortem
        asyncio.create_task(generate_postmortem(incident.id))
    except Exception:
        pass
    return {"ok": True, "message": "Postmortem generation started"}


@router.post("/incidents/{incident_id}/feedback", summary="Label an incident real or noise")
async def incident_feedback(
    incident_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_editor),
):
    """Record an operator verdict on an incident (closed feedback loop).

    A 'noise' verdict on a learned_precursor incident that carries a
    ``precursor_template`` adds that template (regex-escaped) to the predictor
    blacklist so the noisy pattern stops firing.
    """
    import re as _re

    body = await request.json()
    verdict = (body or {}).get("verdict")
    if verdict not in ("real", "noise"):
        raise HTTPException(400, "verdict must be 'real' or 'noise'")

    incident = await db.get(Incident, incident_id)
    if not incident:
        raise HTTPException(404, "Incident not found")

    incident.feedback = verdict
    incident.feedback_at = datetime.utcnow()
    incident.feedback_by = _key.name
    incident.updated_at = datetime.utcnow()

    db.add(IncidentEvent(
        incident_id=incident_id,
        event_type="feedback",
        summary=f"Labeled '{verdict}' by {_key.name}",
    ))

    blacklisted = False
    if (
        verdict == "noise"
        and (incident.rule or "").startswith("learned_precursor")
        and incident.precursor_template
    ):
        from services import predictor_config
        try:
            await predictor_config.add_to_blacklist(
                db, _re.escape(incident.precursor_template)
            )
            blacklisted = True
        except Exception as exc:
            logger.warning("Failed to blacklist precursor template: %s", exc)

    await log_action(db, request, "incident.feedback", "incident", incident_id, incident.title)
    await db.commit()
    return {
        "id": incident.id,
        "rule": incident.rule,
        "title": incident.title,
        "status": incident.status,
        "feedback": incident.feedback,
        "feedback_at": incident.feedback_at.isoformat() if incident.feedback_at else None,
        "feedback_by": incident.feedback_by,
        "blacklisted": blacklisted,
    }


@router.get("/predictor/eval", summary="Predictor quality metrics from operator feedback")
async def predictor_eval(
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_api_key),
):
    from services.predictor_eval import compute_eval
    return await compute_eval(db)


# ── Syslog ───────────────────────────────────────────────────────────────────


@router.get("/syslog/stats", summary="Syslog dashboard statistics")
async def syslog_stats(
    _key: ApiKey = Depends(require_api_key),
    hours: int = Query(24, ge=1, le=720, description="Lookback window in hours"),
):
    """Aggregated syslog statistics for dashboard charts."""
    from models.syslog import SEVERITY_LABELS
    from services.clickhouse_client import query as ch_query, query_scalar as ch_scalar

    since = datetime.utcnow() - timedelta(hours=hours)

    result = {
        "total": 0,
        "severity_distribution": [],
        "top_hosts": [],
        "top_apps": [],
        "message_rate": [],
        "geo_distribution": [],
    }

    # Message rate — choose bucket size based on hours
    if hours <= 6:
        bucket_fn = "toStartOfFiveMinutes"
    elif hours <= 24:
        bucket_fn = "toStartOfFifteenMinutes"
    else:
        bucket_fn = "toStartOfHour"

    try:
        # All six aggregations are independent — run them in parallel.
        params = {"t": since}
        total, sev_rows, host_rows, app_rows, rate_rows, geo_rows = await asyncio.gather(
            ch_scalar(
                "SELECT count() FROM syslog_messages WHERE timestamp >= {t:DateTime64(3)}",
                params,
            ),
            ch_query(
                "SELECT severity, count() AS cnt FROM syslog_messages "
                "WHERE timestamp >= {t:DateTime64(3)} GROUP BY severity ORDER BY severity",
                params,
            ),
            ch_query(
                "SELECT coalesce(nullIf(hostname, ''), source_ip) AS host, "
                "source_ip, count() AS cnt FROM syslog_messages "
                "WHERE timestamp >= {t:DateTime64(3)} "
                "GROUP BY host, source_ip ORDER BY cnt DESC LIMIT 10",
                params,
            ),
            ch_query(
                "SELECT app_name, count() AS cnt FROM syslog_messages "
                "WHERE timestamp >= {t:DateTime64(3)} AND app_name != '' "
                "GROUP BY app_name ORDER BY cnt DESC LIMIT 10",
                params,
            ),
            ch_query(
                f"SELECT {bucket_fn}(timestamp) AS bucket, "
                "count() AS cnt, countIf(severity <= 3) AS errors "
                "FROM syslog_messages WHERE timestamp >= {t:DateTime64(3)} "
                "GROUP BY bucket ORDER BY bucket",
                params,
            ),
            ch_query(
                "SELECT geo_country, count() AS cnt FROM syslog_messages "
                "WHERE timestamp >= {t:DateTime64(3)} AND geo_country != '' "
                "GROUP BY geo_country ORDER BY cnt DESC LIMIT 20",
                params,
            ),
        )

        result["total"] = int(total or 0)
        result["severity_distribution"] = [
            {"severity": r["severity"], "label": SEVERITY_LABELS.get(r["severity"], f"Sev {r['severity']}"), "count": r["cnt"]}
            for r in sev_rows if r["severity"] is not None
        ]
        result["top_hosts"] = [
            {"hostname": r["host"], "source_ip": r["source_ip"], "count": r["cnt"]}
            for r in host_rows
        ]
        result["top_apps"] = [
            {"app_name": r["app_name"], "count": r["cnt"]}
            for r in app_rows
        ]
        result["message_rate"] = [
            {
                "bucket": r["bucket"].isoformat() if hasattr(r["bucket"], "isoformat") else str(r["bucket"]),
                "count": r["cnt"],
                "errors": r["errors"],
            }
            for r in rate_rows
        ]
        result["geo_distribution"] = [
            {"country": r["geo_country"], "count": r["cnt"]}
            for r in geo_rows
        ]
    except Exception:
        pass

    return result


@router.get("/syslog", summary="Query syslog messages")
async def query_syslog(
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_api_key),
    host_id: int = Query(None, description="Filter by host ID"),
    severity: int = Query(None, ge=0, le=7, description="Max severity (0=emergency, 7=debug)"),
    search: str = Query(None, description="Full-text search"),
    hours: int = Query(24, ge=1, le=720),
    limit: int = Query(100, ge=1, le=1000),
):
    since = datetime.utcnow() - timedelta(hours=hours)
    where, params = ch_where(
        since,
        sev=None,
        host_id=host_id,
        q=search or "",
    )
    extra = ""
    if severity is not None:
        extra = f" AND severity <= {int(severity)}"

    rows = await ch_query(
        f"""SELECT timestamp, received_at, source_ip, hostname, facility, severity,
                   app_name, message, host_id, tags, noise_score,
                   extracted_fields, geo_country, geo_city
            FROM syslog_messages
            WHERE {where}{extra}
            ORDER BY received_at DESC
            LIMIT {int(limit)}""",
        params,
    )
    return [
        {
            "timestamp": (r["received_at"].isoformat() + "Z") if hasattr(r["received_at"], "isoformat") else str(r["received_at"]),
            "original_timestamp": (r["timestamp"].isoformat() + "Z") if hasattr(r["timestamp"], "isoformat") else str(r["timestamp"]),
            "source_ip": r["source_ip"],
            "hostname": r["hostname"],
            "facility": r["facility"],
            "severity": r["severity"],
            "app_name": r["app_name"],
            "message": r["message"],
            "host_id": r["host_id"],
            "tags": r["tags"],
            "noise_score": r["noise_score"],
            "extracted_fields": dict(r["extracted_fields"]) if r.get("extracted_fields") else {},
            "geo_country": r.get("geo_country") or "",
            "geo_city": r.get("geo_city") or "",
        }
        for r in rows
    ]


@router.get("/syslog/intelligence", summary="Syslog intelligence summary")
async def syslog_intelligence(
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_api_key),
):
    """Returns active anomalies, fleet patterns, severity trends, and precursors."""
    from models.log_template import LogTemplate, PrecursorPattern, FleetPattern
    from services.log_intelligence import detect_baseline_anomalies, get_active_bursts

    result = {
        "anomalies": [],
        "bursts": [],
        "fleet_patterns": [],
        "trends": [],
        "precursors": [],
    }

    try:
        result["anomalies"] = await detect_baseline_anomalies(db)
    except Exception:
        pass
    result["bursts"] = get_active_bursts()

    try:
        fleet = (await db.execute(
            select(FleetPattern).where(FleetPattern.status == "active")
            .order_by(FleetPattern.host_count.desc()).limit(10)
        )).scalars().all()
        result["fleet_patterns"] = [
            {"template_hash": fp.template_hash, "host_count": fp.host_count,
             "source_ips": fp.source_ips.split(",") if fp.source_ips else []}
            for fp in fleet
        ]
    except Exception:
        pass

    try:
        rising = (await db.execute(
            select(LogTemplate).where(
                LogTemplate.trend_direction == "rising",
                LogTemplate.trend_score > 0.05,
            ).order_by(LogTemplate.trend_score.desc()).limit(10)
        )).scalars().all()
        result["trends"] = [
            {"template_hash": t.template_hash, "template": t.template[:120],
             "trend_score": round(t.trend_score * 100, 1),
             "severity_mode": t.severity_mode, "count": t.count}
            for t in rising
        ]
    except Exception:
        pass

    try:
        precs = (await db.execute(
            select(PrecursorPattern, LogTemplate)
            .join(LogTemplate, PrecursorPattern.template_id == LogTemplate.id)
            .where(PrecursorPattern.confidence >= 0.3)
            .order_by(PrecursorPattern.confidence.desc())
            .limit(10)
        )).all()
        result["precursors"] = [
            {"template_hash": tpl.template_hash, "template": tpl.template[:100],
             "event": p.precedes_event, "confidence": round(p.confidence * 100),
             "avg_lead_time": p.avg_lead_time_sec,
             "min_lead_time": p.min_lead_time_sec,
             "max_lead_time": p.max_lead_time_sec}
            for p, tpl in precs
        ]
    except Exception:
        pass

    return result


# ── Topology ──────────────────────────────────────────────────────────────────


@router.get("/topology", summary="Network topology graph data")
async def get_topology(
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_api_key),
):
    from services.topology import build_topology

    topo = await build_topology(db)

    # Load hosts for metadata
    result = await db.execute(select(PingHost))
    hosts = result.scalars().all()
    now = datetime.utcnow()

    nodes = []
    edges = []
    for h in hosts:
        online = h.status == "up" if hasattr(h, "status") else not getattr(h, "is_down", True)
        nodes.append({
            "id": h.id,
            "name": h.name,
            "hostname": h.hostname,
            "status": "up" if online else "down",
            "check_type": h.check_type,
            "source": h.source,
            "maintenance": h.maintenance or False,
        })
        parent = topo.get(h.id)
        if parent is not None:
            edges.append({"source": parent, "target": h.id})

    return {"nodes": nodes, "edges": edges}


# ── Audit Log ─────────────────────────────────────────────────────────────────


@router.get("/audit", summary="Query audit log")
async def query_audit(
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_admin),
    action: str = Query(None, description="Filter by action"),
    user: str = Query(None, description="Filter by username"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    q = select(AuditLog).order_by(AuditLog.timestamp.desc())
    if action:
        q = q.where(AuditLog.action == action)
    if user:
        q = q.where(AuditLog.username == user)
    q = q.offset(offset).limit(limit)

    result = await db.execute(q)
    logs = result.scalars().all()

    # Total count for pagination
    count_q = select(func.count(AuditLog.id))
    if action:
        count_q = count_q.where(AuditLog.action == action)
    if user:
        count_q = count_q.where(AuditLog.username == user)
    total = (await db.execute(count_q)).scalar() or 0

    return {
        "total": total,
        "logs": [
            {
                "id": l.id,
                "timestamp": l.timestamp.isoformat() if l.timestamp else None,
                "user_id": l.user_id,
                "username": l.username,
                "action": l.action,
                "target_type": l.target_type,
                "target_id": l.target_id,
                "target_name": l.target_name,
                "details": json.loads(l.details) if l.details else None,
                "ip_address": l.ip_address,
            }
            for l in logs
        ],
    }


# ── Backup / Restore ─────────────────────────────────────────────────────────


@router.get("/backup/info", summary="Backup info (table counts, DB size)")
async def backup_info(
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_admin),
):
    from services.backup import get_backup_info
    return await get_backup_info(db)


@router.get("/backup", summary="Download full database backup as JSON")
@rate_limit(max_requests=3, window_seconds=60)
async def download_backup(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_admin),
):
    from services.backup import export_backup
    data = await export_backup(db)
    await log_action(db, request, "backup.export", details={"tables": len(data.get("tables", {}))})
    await db.commit()
    return JSONResponse(
        content=data,
        headers={"Content-Disposition": f"attachment; filename=nodeglow-backup-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.json"},
    )


@router.post("/backup/restore", summary="Restore database from backup JSON")
@rate_limit(max_requests=3, window_seconds=60)
async def restore_backup(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_admin),
):
    from services.backup import import_backup
    body = await request.json()
    result = await import_backup(db, body)
    await log_action(db, request, "backup.restore", details={"rows": result.get("total_rows", 0)})
    await db.commit()
    return result


# ── Maintenance Scheduling ────────────────────────────────────────────────────


@router.post("/hosts/{host_id}/maintenance", summary="Schedule maintenance window")
async def schedule_maintenance(
    host_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_editor),
):
    host = await db.get(PingHost, host_id)
    if not host:
        raise HTTPException(404, "Host not found")
    body = await request.json()
    action = body.get("action", "toggle")

    if action == "off":
        host.maintenance = False
        host.maintenance_until = None
    elif action == "schedule":
        until = body.get("until")
        if not until:
            raise HTTPException(400, "until (ISO datetime) is required for scheduling")
        host.maintenance = True
        host.maintenance_until = datetime.fromisoformat(until.replace("Z", "+00:00")).replace(tzinfo=None)
    else:
        # Toggle with optional duration
        if host.maintenance:
            host.maintenance = False
            host.maintenance_until = None
        else:
            host.maintenance = True
            duration = body.get("duration")
            hours_map = {"1h": 1, "2h": 2, "4h": 4, "8h": 8, "12h": 12, "24h": 24}
            if duration in hours_map:
                host.maintenance_until = datetime.utcnow() + timedelta(hours=hours_map[duration])
            elif duration == "custom" and body.get("until"):
                host.maintenance_until = datetime.fromisoformat(body["until"].replace("Z", "+00:00")).replace(tzinfo=None)
            else:
                host.maintenance_until = None

    await log_action(db, request, "maintenance.toggle", "host", host_id, host.name,
                     {"maintenance": host.maintenance, "until": host.maintenance_until.isoformat() if host.maintenance_until else None})
    await db.commit()
    return {
        "ok": True,
        "maintenance": host.maintenance,
        "maintenance_until": host.maintenance_until.isoformat() if host.maintenance_until else None,
    }


# ── Watched Services (Agent) ─────────────────────────────────────────────────


@router.get("/agents/{agent_id}/services", summary="Get watched services for an agent")
async def get_watched_services(
    agent_id: int,
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_api_key),
):
    agent = await db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    services = json.loads(agent.watched_services) if agent.watched_services else []
    return {"agent_id": agent_id, "services": services}


@router.put("/agents/{agent_id}/services", summary="Set watched services for an agent")
async def set_watched_services(
    agent_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_editor),
):
    agent = await db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    body = await request.json()
    services = body.get("services", [])
    agent.watched_services = json.dumps(services) if services else None
    await db.commit()
    return {"ok": True, "services": services}


# ── Glow (AI Assistant) ───────────────────────────────────────────────────────

_glow_log = logging.getLogger("glow")

GLOW_SYSTEM_PROMPT = """Nodeglow Glow assistant. Analyse the infrastructure data below and answer concisely.
Rules: be specific, use bullet points, reference host/incident names. Never invent data not in context.

{context}"""

_MAX_HISTORY = 10  # keep last N messages to limit token usage


@router.post("/glow/chat", summary="Glow AI chat (streaming SSE)")
async def glow_chat(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_api_key),
):
    """Stream an AI copilot response as SSE events."""
    from services.ai_client import stream_completion
    from services.ai_context import gather_infrastructure_context

    body = await request.json()
    user_message = (body.get("message") or "").strip()
    history = body.get("history") or []

    if not user_message:
        raise HTTPException(400, "message is required")

    # Gather live infrastructure context
    try:
        context = await gather_infrastructure_context(db)
    except Exception as e:
        _glow_log.warning("Failed to gather context: %s", e)
        context = "Infrastructure context unavailable."

    system_prompt = GLOW_SYSTEM_PROMPT.format(context=context)

    # Build messages: keep only last N history entries to limit tokens
    messages = []
    for h in history[-_MAX_HISTORY:]:
        role = h.get("role")
        content = h.get("content", "")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_message})

    async def event_stream():
        try:
            async for delta in stream_completion(system_prompt, messages):
                yield f"data: {json.dumps({'delta': delta})}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"
        except RuntimeError as e:
            if "not configured" in str(e):
                yield f"data: {json.dumps({'error': 'Claude API key not configured. Go to Settings > AI to add your key.', 'done': True})}\n\n"
            else:
                _glow_log.exception("Glow stream error")
                yield f"data: {json.dumps({'error': str(e), 'done': True})}\n\n"
        except Exception as e:
            _glow_log.exception("Glow stream error")
            yield f"data: {json.dumps({'error': 'An unexpected error occurred.', 'done': True})}\n\n"

    # Check API key availability before starting the stream
    from services.ai_client import _get_api_key
    api_key = await _get_api_key()
    if not api_key:
        return JSONResponse(
            {"error": "Claude API key not configured. Go to Settings > AI to add your key."},
            status_code=503,
        )

    return StreamingResponse(event_stream(), media_type="text/event-stream")
