"""
REST API v1 – External JSON API with API key authentication.

All endpoints are under /api/v1/ and require an API key via
X-API-Key header or ?api_key= query parameter.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import PingHost, PingResult, get_setting
from models.agent import Agent, AgentSnapshot
from models.api_key import ApiKey
from models.base import get_db
from models.incident import Incident, IncidentEvent
from models.integration import IntegrationConfig, Snapshot
from services.clickhouse_client import query as ch_query, _where_clauses as ch_where
from services import ping as ping_svc
from services import snapshot as snap_svc

router = APIRouter(prefix="/api/v1", tags=["API v1"])


# ── Auth dependency ──────────────────────────────────────────────────────────


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


async def require_api_key(request: Request, db: AsyncSession = Depends(get_db)) -> ApiKey:
    """Validate API key from header/query param, or fall back to session auth."""
    key = request.headers.get("X-API-Key") or request.query_params.get("api_key")
    if key:
        hashed = _hash_key(key)
        result = await db.execute(
            select(ApiKey).where(ApiKey.key_hash == hashed, ApiKey.enabled == True)
        )
        api_key = result.scalar_one_or_none()
        if not api_key:
            raise HTTPException(status_code=401, detail="Invalid or disabled API key.")
        api_key.last_used = datetime.utcnow()
        await db.commit()
        return api_key

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
        role=role, note=note,
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

    latest_map = await ping_svc.get_latest_by_host(db, [h.id for h in hosts])
    uptime_map = await ping_svc.get_uptime_map(db)

    out = []
    for h in hosts:
        lr = latest_map.get(h.id)
        is_online = lr.success if lr else None
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
        out.append({
            "id": h.id,
            "name": h.name,
            "hostname": h.hostname,
            "status": host_status,
            "check_type": h.check_type or "icmp",
            "port": h.port,
            "source": h.source or "manual",
            "source_detail": h.source_detail,
            "latency_ms": round(lr.latency_ms, 2) if lr and lr.latency_ms else None,
            "last_check": lr.timestamp.isoformat() if lr else None,
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

    lr_q = await db.execute(
        select(PingResult).where(PingResult.host_id == host_id)
        .order_by(PingResult.timestamp.desc()).limit(1)
    )
    lr = lr_q.scalar_one_or_none()

    um = (await ping_svc.get_uptime_map(db)).get(host_id, {})

    return {
        "id": host.id,
        "name": host.name,
        "hostname": host.hostname,
        "check_type": host.check_type or "icmp",
        "port": host.port,
        "enabled": host.enabled,
        "maintenance": host.maintenance or False,
        "source": host.source or "manual",
        "source_detail": host.source_detail,
        "latency_threshold_ms": host.latency_threshold_ms,
        "ssl_expiry_days": host.ssl_expiry_days,
        "mac_address": host.mac_address,
        "parent_id": host.parent_id,
        "created_at": host.created_at.isoformat() if host.created_at else None,
        "latest": {
            "online": lr.success if lr else None,
            "latency_ms": round(lr.latency_ms, 2) if lr and lr.latency_ms else None,
            "timestamp": lr.timestamp.isoformat() if lr else None,
        },
        "uptime": {"h24": um.get("h24"), "d7": um.get("d7"), "d30": um.get("d30")},
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

    since = datetime.utcnow() - timedelta(hours=hours)
    q = await db.execute(
        select(PingResult)
        .where(PingResult.host_id == host_id, PingResult.timestamp >= since)
        .order_by(PingResult.timestamp.desc())
        .limit(limit)
    )
    results = list(reversed(q.scalars().all()))
    return {
        "host_id": host_id,
        "count": len(results),
        "results": [
            {
                "timestamp": r.timestamp.isoformat(),
                "success": r.success,
                "latency_ms": round(r.latency_ms, 2) if r.latency_ms else None,
            }
            for r in results
        ],
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
    for field in ("name", "hostname", "check_type", "port", "latency_threshold_ms",
                  "enabled", "maintenance"):
        if field in body:
            setattr(host, field, body[field])
    await db.commit()
    return {"ok": True, "id": host.id}


@router.delete("/hosts/{host_id}", summary="Delete a host")
async def delete_host(
    host_id: int,
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_editor),
):
    host = await db.get(PingHost, host_id)
    if not host:
        raise HTTPException(404, "Host not found")
    await db.delete(host)
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

    # Fetch latest snapshot per agent in one query
    from sqlalchemy.orm import aliased
    latest_sub = (
        select(
            AgentSnapshot.agent_id,
            func.max(AgentSnapshot.id).label("max_id"),
        )
        .group_by(AgentSnapshot.agent_id)
        .subquery()
    )
    snap_q = await db.execute(
        select(AgentSnapshot)
        .join(latest_sub, AgentSnapshot.id == latest_sub.c.max_id)
    )
    snaps_by_agent = {s.agent_id: s for s in snap_q.scalars().all()}

    out = []
    for a in agents:
        s = snaps_by_agent.get(a.id)
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
            "cpu_pct": s.cpu_pct if s else None,
            "mem_pct": s.mem_pct if s else None,
            "disk_pct": s.disk_pct if s else None,
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

    snap_q = await db.execute(
        select(AgentSnapshot).where(AgentSnapshot.agent_id == agent_id)
        .order_by(AgentSnapshot.timestamp.desc()).limit(60)
    )
    snaps = snap_q.scalars().all()

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
        "snapshots": [
            {
                "agent_id": s.agent_id,
                "timestamp": s.timestamp.isoformat() if s.timestamp else None,
                "cpu_pct": s.cpu_pct,
                "mem_pct": s.mem_pct,
                "mem_used_mb": s.mem_used_mb,
                "mem_total_mb": s.mem_total_mb,
                "disk_pct": s.disk_pct,
                "uptime_s": s.uptime_s,
                "data_json": json.loads(s.data_json) if s.data_json else None,
            }
            for s in snaps
        ],
    }


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

    # Get latest snapshot for each
    latest = {}
    for cfg in configs:
        snap = await snap_svc.get_latest(db, cfg.type, cfg.id)
        if snap:
            latest[cfg.id] = snap

    out = []
    for cfg in configs:
        snap = latest.get(cfg.id)
        out.append({
            "id": cfg.id,
            "type": cfg.type,
            "name": cfg.name,
            "enabled": cfg.enabled,
            "created_at": cfg.created_at.isoformat() if hasattr(cfg, "created_at") and cfg.created_at else None,
            "status": "ok" if snap and snap.ok else ("error" if snap else "no_data"),
            "last_check": snap.timestamp.isoformat() if snap else None,
            "error": snap.error if snap and not snap.ok else None,
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

    return {
        "id": cfg.id,
        "entity_type": cfg.type,
        "entity_id": cfg.id,
        "name": cfg.name,
        "enabled": cfg.enabled,
        "ok": snap.ok if snap else False,
        "timestamp": snap.timestamp.isoformat() if snap else None,
        "error": snap.error if snap and not snap.ok else None,
        "data_json": data,
    }


# ── Incidents ────────────────────────────────────────────────────────────────


@router.get("/incidents", summary="List incidents")
async def list_incidents(
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_api_key),
    status: str = Query(None, description="Filter: open, acknowledged, resolved"),
    limit: int = Query(50, ge=1, le=500),
):
    q = select(Incident).order_by(Incident.updated_at.desc()).limit(limit)
    if status:
        q = q.where(Incident.status == status)
    result = await db.execute(q)

    return [
        {
            "id": i.id,
            "rule": i.rule,
            "title": i.title,
            "severity": i.severity,
            "status": i.status,
            "created_at": i.created_at.isoformat(),
            "updated_at": i.updated_at.isoformat(),
            "resolved_at": i.resolved_at.isoformat() if i.resolved_at else None,
            "acknowledged_by": i.acknowledged_by,
        }
        for i in result.scalars().all()
    ]


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
        "events": [
            {
                "timestamp": e.timestamp.isoformat(),
                "type": e.event_type,
                "summary": e.summary,
                "detail": e.detail,
            }
            for e in events
        ],
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
    await db.commit()
    return {"ok": True, "status": "acknowledged"}


@router.post("/incidents/{incident_id}/resolve", summary="Resolve an incident")
async def resolve_incident(
    incident_id: int,
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
    await db.commit()
    return {"ok": True, "status": "resolved"}


# ── Syslog ───────────────────────────────────────────────────────────────────


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
        f"""SELECT timestamp, source_ip, hostname, facility, severity,
                   app_name, message, host_id, tags, noise_score
            FROM syslog_messages
            WHERE {where}{extra}
            ORDER BY timestamp DESC
            LIMIT {int(limit)}""",
        params,
    )
    return [
        {
            "timestamp": r["timestamp"].isoformat() if hasattr(r["timestamp"], "isoformat") else str(r["timestamp"]),
            "source_ip": r["source_ip"],
            "hostname": r["hostname"],
            "facility": r["facility"],
            "severity": r["severity"],
            "app_name": r["app_name"],
            "message": r["message"],
            "host_id": r["host_id"],
            "tags": r["tags"],
            "noise_score": r["noise_score"],
        }
        for r in rows
    ]
