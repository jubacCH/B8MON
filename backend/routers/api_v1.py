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
from sqlalchemy import func, select, update
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
            snap_q = await db.execute(
                select(AgentSnapshot)
                .where(AgentSnapshot.agent_id == agent.id)
                .order_by(AgentSnapshot.id.desc())
                .limit(1)
            )
            snap = snap_q.scalar_one_or_none()
            agent_metrics = {
                "agent_id": agent.id,
                "agent_name": agent.name,
                "platform": agent.platform,
                "arch": agent.arch,
                "agent_version": agent.agent_version,
                "last_seen": agent.last_seen.isoformat() if agent.last_seen else None,
                "cpu_pct": snap.cpu_pct if snap else None,
                "mem_pct": snap.mem_pct if snap else None,
                "mem_used_mb": snap.mem_used_mb if snap else None,
                "mem_total_mb": snap.mem_total_mb if snap else None,
                "disk_pct": snap.disk_pct if snap else None,
                "load_1": snap.load_1 if snap else None,
                "load_5": snap.load_5 if snap else None,
                "load_15": snap.load_15 if snap else None,
                "uptime_s": snap.uptime_s if snap else None,
                "rx_bytes": snap.rx_bytes if snap else None,
                "tx_bytes": snap.tx_bytes if snap else None,
                "snapshot_time": snap.timestamp.isoformat() if snap else None,
                "extra": json.loads(snap.data_json) if snap and snap.data_json else None,
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
        for cfg in configs_q.scalars().all():
            snap_q = await db.execute(
                select(Snapshot)
                .where(Snapshot.entity_type == int_type, Snapshot.entity_id == cfg.id)
                .order_by(Snapshot.timestamp.desc())
                .limit(1)
            )
            snap = snap_q.scalar_one_or_none()
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
    _online = lr.success if lr else None
    _lat = lr.latency_ms if lr else None
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
        uptime_24h = um.get("h24") or 100.0
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
            "online": lr.success if lr else None,
            "latency_ms": round(lr.latency_ms, 2) if lr and lr.latency_ms else None,
            "timestamp": lr.timestamp.isoformat() if lr else None,
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
    old_check_type = host.check_type
    for field in ("name", "hostname", "check_type", "port", "latency_threshold_ms",
                  "enabled", "maintenance"):
        if field in body:
            setattr(host, field, body[field])
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
            "cpu_pct": s.cpu_pct if s else None,
            "mem_pct": s.mem_pct if s else None,
            "disk_pct": s.disk_pct if s else None,
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
        "log_levels": agent.log_levels or "",
        "log_channels": agent.log_channels or "",
        "log_file_paths": agent.log_file_paths or "",
        "agent_log_level": agent.agent_log_level or "errors",
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

    # Clean up related PingHost
    if agent.hostname:
        from sqlalchemy import delete as sa_delete
        hn = agent.hostname.lower()
        ph = await db.execute(
            select(PingHost).where(
                ((func.lower(PingHost.hostname) == hn) | (func.lower(PingHost.name) == hn)),
                PingHost.source == "agent"
            )
        )
        ping_host = ph.scalar_one_or_none()
        if ping_host:
            await db.execute(sa_delete(PingResult).where(PingResult.host_id == ping_host.id))
            await db.execute(sa_delete(PingHost).where(PingHost.id == ping_host.id))

    from sqlalchemy import delete as sa_delete
    await db.execute(sa_delete(AgentSnapshot).where(AgentSnapshot.agent_id == agent_id))
    await db.execute(sa_delete(Agent).where(Agent.id == agent_id))
    await db.commit()
    return {"ok": True}


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
