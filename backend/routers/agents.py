"""
Agent router — register agents, receive metrics, serve UI + WebSocket live feed.
"""
import hashlib
import hmac
import json
import logging
import os
import secrets
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import select

from database import AsyncSessionLocal, PingHost, get_setting, set_setting
from ratelimit import rate_limit
from models.agent import Agent
from models.agent_install_token import AgentInstallToken
from services import agent_signing
from services.websocket import broadcast_agent_metric


def _hash_agent_token(token: str) -> str:
    """HMAC-SHA256 hash an agent token for secure storage."""
    from config import SECRET_KEY
    return hmac.new(SECRET_KEY.encode(), token.encode(), hashlib.sha256).hexdigest()


def _hash_agent_token_legacy(token: str) -> str:
    """Legacy plain SHA256 — for migration from pre-HMAC tokens."""
    return hashlib.sha256(token.encode()).hexdigest()


def _hash_install_token(token: str) -> str:
    """Same pepper as agent tokens so we share the key-derivation pattern."""
    from config import SECRET_KEY
    return hmac.new(SECRET_KEY.encode(), token.encode(), hashlib.sha256).hexdigest()


def _shared_enrollment_allowed() -> bool:
    """Opt-in escape hatch for existing installs still using the shared key."""
    return os.environ.get("NODEGLOW_ALLOW_SHARED_ENROLLMENT", "").strip() in ("1", "true", "yes")


def _require_admin(request: Request):
    """Return None on admin, or a 403 JSONResponse otherwise."""
    user = getattr(request.state, "current_user", None)
    role = getattr(user, "role", None)
    if not user or role != "admin":
        return JSONResponse({"error": "Admin access required"}, status_code=403)
    return None


logger = logging.getLogger(__name__)
router = APIRouter()


async def resolve_server_url(request: Request) -> str:
    """The address an agent out on the network should call back on.

    Every browser request reaches this app through the frontend's proxy, which
    rewrites ``Host`` to its upstream target and puts the real one in
    ``X-Forwarded-Host``. Reading ``request.url.netloc`` therefore yielded the
    internal service name, and the installers handed customers a
    ``http://nodeglow:8000`` that resolves nowhere outside the container
    network — the agent could not enrol at all.

    Order: the operator's explicit setting, then what the proxy reports, then
    the request's own host as a last resort.
    """
    async with AsyncSessionLocal() as db:
        configured = (await get_setting(db, "agent_server_url", "") or "").strip()
    if configured:
        return configured.rstrip("/")

    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host") or request.url.netloc
    # A proxy chain sends a comma-separated list; the client-facing one is first.
    host = host.split(",")[0].strip()
    return f"{scheme}://{host}"


async def _get_enrollment_key() -> str:
    """Legacy shared enrollment key. Only used when
    NODEGLOW_ALLOW_SHARED_ENROLLMENT=1 is set on the backend container.
    New deployments must use per-install tokens (AgentInstallToken)."""
    async with AsyncSessionLocal() as db:
        key = await get_setting(db, "agent_enrollment_key")
        if not key:
            key = secrets.token_hex(16)
            await set_setting(db, "agent_enrollment_key", key)
            await db.commit()
        return key


async def _consume_install_token(db, raw_token: str, hostname: str) -> tuple[bool, str]:
    """Validate and consume an install token. The `reason` in the return value
    is for server-side logging ONLY — the caller must not surface it to the
    client, otherwise the distinct error cases enable token-existence
    enumeration via timing/response-body differences."""
    import fnmatch
    token_hash = _hash_install_token(raw_token)
    result = await db.execute(
        select(AgentInstallToken).where(AgentInstallToken.token_hash == token_hash)
    )
    row = result.scalar_one_or_none()
    if not row:
        return False, "unknown"
    if row.revoked:
        return False, "revoked"
    if row.expires_at <= datetime.utcnow():
        return False, "expired"
    if row.hostname_pattern:
        # fnmatch is case-sensitive by default; lower() both sides so patterns
        # like "web-*.b8n.ch" are host-case-insensitive but still anchored
        # (substring matching was replaced because ".b8n.ch" would otherwise
        # accept evil.b8n.ch.attacker.com).
        pat = row.hostname_pattern.strip().lower()
        if pat and not fnmatch.fnmatchcase(hostname.lower(), pat):
            return False, "hostname_mismatch"
    row.used_count = (row.used_count or 0) + 1
    row.last_used_at = datetime.utcnow()
    return True, "ok"


# ── API: Agent self-enrollment ────────────────────────────────────────────────

@router.post("/api/agent/enroll")
@rate_limit(max_requests=5, window_seconds=60)
async def agent_enroll(request: Request):
    """Agent self-registers using an install token (preferred) or the legacy
    shared enrollment key when NODEGLOW_ALLOW_SHARED_ENROLLMENT=1 is set.
    Returns a permanent bearer token."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    enroll_key = body.get("enrollment_key", "")
    hostname = body.get("hostname", "").strip()
    plat = body.get("platform", "")
    arch = body.get("arch", "")

    if not enroll_key or not hostname:
        return JSONResponse({"error": "enrollment_key and hostname required"}, status_code=400)

    import re
    if len(hostname) > 253 or not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9.\-]*$', hostname):
        return JSONResponse({"error": "Invalid hostname"}, status_code=400)

    client_ip_log = request.client.host if request.client else "unknown"
    logger.info("Agent enrollment attempt: hostname=%s, ip=%s", hostname, client_ip_log)

    # Preferred path: per-install token (scoped, expiring, revocable).
    async with AsyncSessionLocal() as token_db:
        token_ok, token_err = await _consume_install_token(token_db, enroll_key, hostname)
        if token_ok:
            await token_db.commit()
        else:
            await token_db.rollback()

    if not token_ok:
        # Legacy fallback — only when explicitly enabled by the operator.
        # In all failure paths we return the SAME generic error so the client
        # cannot distinguish "unknown token" from "revoked/expired/hostname
        # mismatch" via the response body. Detailed reasons go to logs.
        generic_403 = JSONResponse(
            {"error": "Invalid enrollment credential"}, status_code=403,
        )
        if not _shared_enrollment_allowed():
            logger.warning(
                "Rejected enrollment from %s (hostname=%s): token %s; shared-key flow disabled",
                client_ip_log, hostname, token_err,
            )
            return generic_403
        expected_key = await _get_enrollment_key()
        if not hmac.compare_digest(enroll_key, expected_key):
            logger.warning(
                "Rejected enrollment from %s (hostname=%s): token %s; legacy shared-key mismatch",
                client_ip_log, hostname, token_err,
            )
            return generic_403
        logger.warning(
            "Legacy shared-key enrollment accepted for %s — migrate to install tokens",
            hostname,
        )

    # Use the client's real IP for PingHost (hostname may not be resolvable from server)
    client_ip = request.client.host if request.client else None

    async with AsyncSessionLocal() as db:
        # Check if agent with this hostname already exists → return existing token (case-insensitive)
        from sqlalchemy import func as sa_func
        result = await db.execute(select(Agent).where(sa_func.lower(Agent.hostname) == hostname.lower()))
        existing = result.scalars().first()
        if existing:
            # Rotate token on re-enrollment for security
            raw_token = secrets.token_hex(24)
            existing.token = _hash_agent_token(raw_token)
            existing.platform = plat or existing.platform
            existing.arch = arch or existing.arch
            existing.last_seen = datetime.utcnow()
            await db.commit()
            logger.info("Agent re-enrolled successfully: hostname=%s, id=%d", hostname, existing.id)
            return {"ok": True, "token": raw_token, "agent_id": existing.id}

        # Create new agent
        raw_token = secrets.token_hex(24)
        token_hash = _hash_agent_token(raw_token)
        agent = Agent(name=hostname, hostname=hostname, token=token_hash, platform=plat, arch=arch)
        db.add(agent)

        # Auto-create PingHost — try reverse DNS for FQDN, store IP separately
        agent_ip = client_ip or ""
        fqdn = hostname
        if agent_ip:
            try:
                import socket as _socket
                rev = _socket.gethostbyaddr(agent_ip)[0]
                if rev and "." in rev:
                    fqdn = rev
            except Exception:
                pass
        ping_result = await db.execute(
            select(PingHost).where(
                sa_func.lower(PingHost.hostname).in_([hostname.lower(), fqdn.lower(), agent_ip.lower()])
            )
        )
        if not ping_result.scalars().first():
            db.add(PingHost(
                name=hostname,
                hostname=fqdn,
                ip_address=agent_ip or None,
                check_type="icmp",
                source="agent",
                source_detail=f"auto-enrolled agent ({hostname})",
            ))

        await db.commit()
        await db.refresh(agent)
        logger.info("Agent auto-enrolled: %s (id=%d)", hostname, agent.id)
        return {"ok": True, "token": raw_token, "agent_id": agent.id}


# ── API: Agent reports metrics ───────────────────────────────────────────────

@router.post("/api/agent/report")
async def agent_report(request: Request):
    """Receive metrics from a Nodeglow agent."""
    # Auth via Bearer token
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return JSONResponse({"error": "Missing token"}, status_code=401)
    token = auth[7:].strip()
    if not token:
        return JSONResponse({"error": "Empty token"}, status_code=401)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    async with AsyncSessionLocal() as db:
        token_hash = _hash_agent_token(token)
        result = await db.execute(select(Agent).where(Agent.token == token_hash, Agent.enabled == True))
        agent = result.scalar_one_or_none()
        if not agent:
            # Fallback: try legacy plain SHA256 hash and migrate if found
            legacy_hash = _hash_agent_token_legacy(token)
            result = await db.execute(select(Agent).where(Agent.token == legacy_hash, Agent.enabled == True))
            agent = result.scalar_one_or_none()
            if agent:
                agent.token = token_hash  # migrate to HMAC
            else:
                return JSONResponse({"error": "Invalid or disabled token"}, status_code=403)

        # Update agent metadata
        agent.last_seen = datetime.utcnow()
        agent.hostname = body.get("hostname", agent.hostname)
        agent.platform = body.get("platform", agent.platform)
        agent.arch = body.get("arch", agent.arch)
        agent.agent_version = body.get("agent_version", agent.agent_version)

        # Extract primary disk (highest usage or root)
        disks = body.get("disks", [])
        primary_disk_pct = None
        if disks:
            root = next((d for d in disks if d.get("mount") == "/"), None)
            primary_disk_pct = root["pct"] if root else disks[0].get("pct")

        mem = body.get("memory", {})
        load = body.get("load", {})

        snap_ts = datetime.utcnow()
        snap_data_json = json.dumps(body)

        # Extract bandwidth samples from agent network data (writes to ClickHouse)
        try:
            from services.bandwidth import extract_agent_bandwidth
            await extract_agent_bandwidth(agent.id, body, snap_ts, source_name=agent.name)
        except Exception as bw_exc:
            logger.warning("Bandwidth extraction failed for agent %d: %s", agent.id, bw_exc)

        # Consume pending command (deliver once, then clear)
        command = agent.pending_command
        if command:
            agent.pending_command = None

        await db.commit()

    # Time-series snapshot lives in ClickHouse only (post-cutover).
    try:
        from services.clickhouse_client import insert_agent_metrics
        await insert_agent_metrics([{
            "timestamp": snap_ts,
            "agent_id": agent.id,
            "agent_name": agent.name,
            "cpu_pct": body.get("cpu_pct"),
            "mem_pct": mem.get("pct"),
            "mem_used_mb": mem.get("used_mb"),
            "mem_total_mb": mem.get("total_mb"),
            "disk_pct": primary_disk_pct,
            "load_1": load.get("load_1"),
            "load_5": load.get("load_5"),
            "load_15": load.get("load_15"),
            "uptime_s": body.get("uptime_s"),
            "rx_bytes": body.get("network", {}).get("rx_bytes"),
            "tx_bytes": body.get("network", {}).get("tx_bytes"),
            "data_json": snap_data_json,
        }])
    except Exception as ch_exc:
        logger.error("ClickHouse insert (agent_metrics) failed: %s", ch_exc)

    # Broadcast to all WebSocket clients (global hub)
    await broadcast_agent_metric(agent.id, agent.name, {
        "hostname": body.get("hostname"),
        "cpu_pct": body.get("cpu_pct"),
        "mem_pct": mem.get("pct"),
        "disk_pct": primary_disk_pct,
        "load_1": load.get("load_1"),
        "uptime_s": body.get("uptime_s"),
    })

    # Return config + optional command to agent
    resp = {"ok": True, "config": {
        "log_levels": agent.log_levels or "1,2,3",
        "log_channels": agent.log_channels or "System,Application",
        "log_file_paths": agent.log_file_paths or "",
        "agent_log_level": agent.agent_log_level or "errors",
    }}
    if command:
        resp["command"] = command
    return resp


# ── API: Agent log submission ────────────────────────────────────────────────

@router.post("/api/agent/logs")
async def agent_logs(request: Request):
    """Receive log entries from a Nodeglow agent and feed into syslog system."""
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return JSONResponse({"error": "Missing token"}, status_code=401)
    token = auth[7:].strip()
    if not token:
        return JSONResponse({"error": "Empty token"}, status_code=401)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    async with AsyncSessionLocal() as db:
        token_hash = _hash_agent_token(token)
        result = await db.execute(select(Agent).where(Agent.token == token_hash, Agent.enabled == True))
        agent = result.scalar_one_or_none()
        if not agent:
            # Fallback: try legacy plain SHA256 hash and migrate if found
            legacy_hash = _hash_agent_token_legacy(token)
            result = await db.execute(select(Agent).where(Agent.token == legacy_hash, Agent.enabled == True))
            agent = result.scalar_one_or_none()
            if agent:
                agent.token = token_hash  # migrate to HMAC
                await db.commit()
            else:
                return JSONResponse({"error": "Invalid or disabled token"}, status_code=403)

    hostname = body.get("hostname", agent.hostname or "unknown")
    logs = body.get("logs", [])
    if not logs:
        return {"ok": True, "count": 0}

    # Get client IP for source_ip
    source_ip = request.client.host if request.client else "0.0.0.0"

    # Resolve host_id via syslog host cache
    try:
        from services.syslog import _resolve_host_id, _refresh_host_cache
        await _refresh_host_cache()
        host_id = _resolve_host_id(source_ip, hostname)
    except Exception:
        host_id = None

    # Feed logs through the syslog pipeline (_enqueue handles ClickHouse write + live tail)
    from services.syslog import _enqueue
    count = 0
    for entry in logs[:500]:
        ts_str = entry.get("timestamp", "")
        try:
            ts = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            ts = datetime.utcnow()

        parsed = {
            "timestamp": ts,
            "received_at": datetime.utcnow(),
            "source_ip": source_ip,
            "hostname": hostname,
            "facility": entry.get("facility"),
            "severity": entry.get("severity", 6),
            "app_name": entry.get("app_name", ""),
            "message": entry.get("message", "")[:2000],
            "host_id": host_id,
        }
        await _enqueue(parsed)
        count += 1

    logger.debug("Agent %s sent %d log entries", hostname, count)
    return {"ok": True, "count": count}


# ── Install-token CRUD (admin) ────────────────────────────────────────────────

@router.post("/api/agents/install-tokens")
@rate_limit(max_requests=10, window_seconds=60)
async def create_install_token(request: Request):
    """Issue a new per-install enrollment token. Admin only. Returns the
    plaintext token once — it is stored only as an HMAC hash afterwards."""
    if err := _require_admin(request):
        return err
    try:
        body = await request.json()
    except Exception:
        body = {}
    note = (body.get("note") or "").strip()[:256]
    hostname_pattern = (body.get("hostname_pattern") or "").strip()[:256] or None
    try:
        ttl_hours = int(body.get("ttl_hours", 24))
    except (TypeError, ValueError):
        ttl_hours = 24
    ttl_hours = max(1, min(ttl_hours, 24 * 30))  # clamp 1h..30d

    raw = f"nt_{secrets.token_hex(24)}"
    token_row = AgentInstallToken(
        token_hash=_hash_install_token(raw),
        prefix=raw[:8],
        note=note or None,
        hostname_pattern=hostname_pattern,
        expires_at=datetime.utcnow() + timedelta(hours=ttl_hours),
        created_by=(getattr(request.state, "current_user", None).username
                    if getattr(request.state, "current_user", None) else None),
    )
    async with AsyncSessionLocal() as db:
        db.add(token_row)
        await db.commit()
        await db.refresh(token_row)

    server_url = await resolve_server_url(request)

    return {
        "ok": True,
        "id": token_row.id,
        "token": raw,
        "prefix": token_row.prefix,
        "expires_at": token_row.expires_at.isoformat(),
        "install_linux": f"curl -sSL '{server_url}/install/linux?token={raw}' | sudo bash",
        "install_windows": f"irm '{server_url}/install/windows?token={raw}' | iex",
    }


@router.get("/api/agents/install-tokens")
async def list_install_tokens(request: Request):
    """List active install tokens (prefix + metadata only, no secrets)."""
    if err := _require_admin(request):
        return err
    now = datetime.utcnow()
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(AgentInstallToken).order_by(AgentInstallToken.created_at.desc())
        )).scalars().all()
    return [
        {
            "id": r.id,
            "prefix": r.prefix,
            "note": r.note,
            "hostname_pattern": r.hostname_pattern,
            "expires_at": r.expires_at.isoformat(),
            "expired": r.expires_at <= now,
            "revoked": bool(r.revoked),
            "used_count": r.used_count or 0,
            "last_used_at": r.last_used_at.isoformat() if r.last_used_at else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "created_by": r.created_by,
        }
        for r in rows
    ]


@router.delete("/api/agents/install-tokens/{token_id}")
@rate_limit(max_requests=10, window_seconds=60)
async def revoke_install_token(token_id: int, request: Request):
    """Revoke an install token without deleting its audit record."""
    if err := _require_admin(request):
        return err
    async with AsyncSessionLocal() as db:
        row = await db.get(AgentInstallToken, token_id)
        if not row:
            return JSONResponse({"error": "Not found"}, status_code=404)
        row.revoked = True
        await db.commit()
    return {"ok": True}


# ── Enrollment info (JSON API for SPA) ────────────────────────────────────────

@router.get("/api/enrollment-info")
async def enrollment_info(request: Request):
    """Return install hints for the SPA. Admin only. Does NOT leak any
    enrollment secret — the SPA must call POST /api/agents/install-tokens
    to mint one."""
    if err := _require_admin(request):
        return err
    server_url = await resolve_server_url(request)
    return {
        "server_url": server_url,
        "install_linux_template": f"curl -sSL '{server_url}/install/linux?token=<INSTALL_TOKEN>' | sudo bash",
        "install_windows_template": f"irm '{server_url}/install/windows?token=<INSTALL_TOKEN>' | iex",
        "shared_enrollment_enabled": _shared_enrollment_allowed(),
    }


async def _authorize_install_request(request: Request) -> tuple[bool, str | None]:
    """Authorize a /install/* request by validating a ?token=<raw> query
    parameter against AgentInstallToken. Returns (ok, raw_token_or_None).
    NOTE: /install/* is on the auth-middleware skip list in main.py, so we
    cannot rely on session state here; the query-param token is the single
    source of authentication."""
    qp_token = (request.query_params.get("token") or "").strip()
    if not qp_token:
        return False, None
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(AgentInstallToken).where(
                AgentInstallToken.token_hash == _hash_install_token(qp_token)
            )
        )
        row = result.scalar_one_or_none()
    if not row or row.revoked or row.expires_at <= datetime.utcnow():
        return False, None
    return True, qp_token


# ── Install scripts (universal one-liner endpoints) ──────────────────────────

@router.get("/install/linux")
async def install_linux(request: Request):
    """Universal Linux installer. Usage:
        curl -sSL '<url>/install/linux?token=<INSTALL_TOKEN>' | sudo bash
    Requires a valid per-install token generated via
    POST /api/agents/install-tokens (admin)."""
    ok, install_token = await _authorize_install_request(request)
    if not ok:
        return JSONResponse({"error": "Valid install token required"}, status_code=401)
    server_url = await resolve_server_url(request)
    # Script needs a value for the shell variable. For admin-session access
    # without a token query-param we emit a placeholder that will visibly
    # fail at enrollment time instead of silently using something valid.
    enrollment_key = install_token or "NO_INSTALL_TOKEN_PROVIDED"
    # Embed the update-signing public key so the agent verifies signed updates.
    update_public_key = agent_signing.public_key_hex()

    script = f'''#!/bin/bash
set -e

# ── Nodeglow Agent Installer for Linux ──────────────────────────────────────
SERVER="{server_url}"
ENROLLMENT_KEY="{enrollment_key}"
INSTALL_DIR="/opt/nodeglow"
SERVICE_NAME="nodeglow-agent"
CONFIG_FILE="$INSTALL_DIR/config.json"

echo ""
echo "  ╔══════════════════════════════════════════╗"
echo "  ║       Nodeglow Agent Installer           ║"
echo "  ╚══════════════════════════════════════════╝"
echo ""

# Check root
if [ "$(id -u)" -ne 0 ]; then
    echo "  Error: Please run as root (sudo)"
    exit 1
fi

# Check dependencies
if ! command -v curl &>/dev/null; then
    echo "  Error: curl is required but not installed."
    exit 1
fi

HOSTNAME=$(hostname)

echo "  [1/5] Creating install directory..."
mkdir -p "$INSTALL_DIR"

echo "  [2/5] Enrolling agent ($HOSTNAME)..."
ENROLL_RESPONSE=$(curl -sSL -X POST "$SERVER/api/agent/enroll" \\
    -H "Content-Type: application/json" \\
    -d "{{\\"enrollment_key\\": \\"$ENROLLMENT_KEY\\", \\"hostname\\": \\"$HOSTNAME\\", \\"platform\\": \\"Linux\\", \\"arch\\": \\"$(uname -m)\\"}}")

# Extract token from JSON response (try python3 first, then grep fallback)
TOKEN=$(echo "$ENROLL_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('token',''))" 2>/dev/null || \\
    echo "$ENROLL_RESPONSE" | grep -o '"token":"[^"]*"' | cut -d'"' -f4)
if [ -z "$TOKEN" ]; then
    echo "  Error: Enrollment failed. Response: $ENROLL_RESPONSE"
    exit 1
fi
echo "  Enrolled successfully."

echo "  [3/5] Downloading agent..."
curl -sSL "$SERVER/agents/download/linux" -o "$INSTALL_DIR/nodeglow-agent.tmp"
# Detect if binary or Python script
if head -c 2 "$INSTALL_DIR/nodeglow-agent.tmp" | grep -q '#!'; then
  mv "$INSTALL_DIR/nodeglow-agent.tmp" "$INSTALL_DIR/nodeglow-agent.py"
  chmod +x "$INSTALL_DIR/nodeglow-agent.py"
  AGENT_EXEC="python3 /opt/nodeglow/nodeglow-agent.py"
else
  mv "$INSTALL_DIR/nodeglow-agent.tmp" "$INSTALL_DIR/nodeglow-agent"
  chmod +x "$INSTALL_DIR/nodeglow-agent"
  AGENT_EXEC="/opt/nodeglow/nodeglow-agent"
fi

echo "  [4/5] Writing configuration..."
cat > "$CONFIG_FILE" << CONF
{{
  "server": "$SERVER",
  "token": "$TOKEN",
  "interval": 30,
  "update_public_key": "{update_public_key}"
}}
CONF

echo "  [5/5] Creating systemd service..."
cat > /etc/systemd/system/${{SERVICE_NAME}}.service << UNIT
[Unit]
Description=Nodeglow Monitoring Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/nodeglow
ExecStart=$AGENT_EXEC
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT

echo "  [6/6] Testing connection..."
TEST_RESP=$(curl -sSL -X POST "$SERVER/api/agent/report" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $TOKEN" \
    -d "{{\\"hostname\\": \\"$HOSTNAME\\", \\"platform\\": \\"Linux\\", \\"agent_version\\": \\"test\\"}}" 2>&1)
if echo "$TEST_RESP" | grep -q '"ok"'; then
    echo "  Connection test: SUCCESS"
else
    echo "  Connection test: FAILED"
    echo "  $TEST_RESP" | head -3 | sed 's/^/  /'
    echo ""
    echo "  The agent will retry when the service starts."
fi

systemctl daemon-reload
systemctl enable ${{SERVICE_NAME}} --quiet
systemctl restart ${{SERVICE_NAME}}

echo ""
echo "  Done! Agent '$HOSTNAME' is running."
echo ""
echo "  Status:  systemctl status ${{SERVICE_NAME}}"
echo "  Logs:    journalctl -u ${{SERVICE_NAME}} -f"
echo "  Remove:  systemctl disable ${{SERVICE_NAME}} && rm /etc/systemd/system/${{SERVICE_NAME}}.service && rm -rf $INSTALL_DIR"
echo ""
'''

    from fastapi.responses import Response
    return Response(content=script, media_type="text/plain")


@router.get("/install/windows")
async def install_windows(request: Request):
    """Universal Windows installer. Usage:
        irm '<url>/install/windows?token=<INSTALL_TOKEN>' | iex
    Requires a valid per-install token generated via
    POST /api/agents/install-tokens (admin)."""
    ok, install_token = await _authorize_install_request(request)
    if not ok:
        return JSONResponse({"error": "Valid install token required"}, status_code=401)
    server_url = await resolve_server_url(request)
    enrollment_key = install_token or "NO_INSTALL_TOKEN_PROVIDED"
    # Embed the update-signing public key so the agent verifies signed updates.
    update_public_key = agent_signing.public_key_hex()

    script = f'''# ── Nodeglow Agent Installer for Windows ─────────────────────────────────────
$ErrorActionPreference = "Stop"
$Server = "{server_url}"
$EnrollmentKey = "{enrollment_key}"
$InstallDir = "$env:ProgramFiles\\Nodeglow"
$TaskName = "NodeglowAgent"
$AgentVersion = "1.0.0"
$UninstallKey = "HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\NodeglowAgent"

Write-Host ""
Write-Host "  === Nodeglow Agent Installer ===" -ForegroundColor Cyan
Write-Host ""

# Check admin
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {{
    Write-Host "  Error: Please run as Administrator" -ForegroundColor Red
    exit 1
}}

$Hostname = $env:COMPUTERNAME

Write-Host "  [1/8] Stopping existing agent..."
# Stop running agent process
Get-Process | Where-Object {{ $_.Path -like "*nodeglow*" }} | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
# Also stop via scheduled task
Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

Write-Host "  [2/8] Creating install directory..."
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

# Migrate from old ProgramData location if exists
$OldDir = "$env:ProgramData\\Nodeglow"
if (Test-Path "$OldDir\\config.json") {{
    Write-Host "  Migrating from old location..."
    Copy-Item "$OldDir\\config.json" "$InstallDir\\config.json" -Force -ErrorAction SilentlyContinue
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Remove-Item -Path $OldDir -Recurse -Force -ErrorAction SilentlyContinue
}}

# Clean up old files
Remove-Item -Path "$InstallDir\\nodeglow-agent.exe.old" -Force -ErrorAction SilentlyContinue
Remove-Item -Path "$InstallDir\\nodeglow-agent.exe.new" -Force -ErrorAction SilentlyContinue

Write-Host "  [3/8] Enrolling agent ($Hostname)..."
$body = @{{
    enrollment_key = $EnrollmentKey
    hostname = $Hostname
    platform = "Windows"
    arch = $env:PROCESSOR_ARCHITECTURE
}} | ConvertTo-Json

$response = Invoke-RestMethod -Uri "$Server/api/agent/enroll" -Method Post -Body $body -ContentType "application/json"
if (-not $response.token) {{
    Write-Host "  Error: Enrollment failed." -ForegroundColor Red
    exit 1
}}
$Token = $response.token
Write-Host "  Enrolled successfully."

Write-Host "  [4/8] Downloading agent..."
Invoke-WebRequest -Uri "$Server/agents/download/windows" -OutFile "$InstallDir\\nodeglow-agent.exe" -UseBasicParsing

Write-Host "  [5/8] Writing configuration..."
@"
{{
  "server": "$Server",
  "token": "$Token",
  "interval": 30,
  "update_public_key": "{update_public_key}"
}}
"@ | Set-Content -Path "$InstallDir\\config.json" -Encoding ASCII -Force

Write-Host "  [6/8] Creating restart wrapper..."
$WrapperContent = @"
@echo off
title Nodeglow Agent
cd /d "$InstallDir"

:loop
rem Apply staged update if present
if exist "nodeglow-agent.exe.new" (
    echo Applying staged update...
    del /f "nodeglow-agent.exe.old" 2>nul
    ren "nodeglow-agent.exe" "nodeglow-agent.exe.old"
    ren "nodeglow-agent.exe.new" "nodeglow-agent.exe"
)

echo Starting Nodeglow Agent...
"nodeglow-agent.exe"
set EXIT_CODE=%ERRORLEVEL%

del /f "nodeglow-agent.exe.old" 2>nul

if %EXIT_CODE%==42 (
    echo Agent requested restart for update...
    timeout /t 2 /nobreak >nul
    goto loop
)

echo Agent exited with code %EXIT_CODE%, restarting in 10s...
timeout /t 10 /nobreak >nul
goto loop
"@
$WrapperContent | Set-Content -Path "$InstallDir\\nodeglow-wrapper.bat" -Encoding ASCII -Force

Write-Host "  [7/8] Creating scheduled task..."
$Action = New-ScheduledTaskAction -Execute "$InstallDir\\nodeglow-wrapper.bat" -WorkingDirectory $InstallDir
$Trigger = New-ScheduledTaskTrigger -AtStartup
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Days 365)
$Principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal -Description "Nodeglow Monitoring Agent" | Out-Null

Write-Host "  [8/8] Registering in Windows Apps..."
# Create PowerShell uninstall script with admin elevation
$UninstallPS = @'
$ErrorActionPreference = "SilentlyContinue"
# Self-elevate if not admin
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {{
    Start-Process powershell -ArgumentList "-ExecutionPolicy Bypass -File `"$PSCommandPath`"" -Verb RunAs
    exit
}}
Write-Host "Uninstalling Nodeglow Agent..." -ForegroundColor Cyan
Write-Host ""
Write-Host "  Stopping agent..."
Stop-ScheduledTask -TaskName "NodeglowAgent" -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1
Unregister-ScheduledTask -TaskName "NodeglowAgent" -Confirm:$false -ErrorAction SilentlyContinue
Get-Process | Where-Object {{ $_.Path -like "*nodeglow*" }} | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
Write-Host "  Removing registry entry..."
Remove-Item -Path "HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\NodeglowAgent" -Force -ErrorAction SilentlyContinue
Write-Host "  Removing files..."
$dir = Split-Path -Parent $PSCommandPath
Start-Process cmd -ArgumentList "/c timeout /t 3 /nobreak >nul & rmdir /s /q `"$dir`"" -WindowStyle Hidden
Write-Host ""
Write-Host "  Nodeglow Agent has been uninstalled." -ForegroundColor Green
Write-Host ""
Start-Sleep -Seconds 2
'@
$UninstallPS | Set-Content -Path "$InstallDir\\uninstall.ps1" -Encoding UTF8 -Force

# Register in Apps & Features (Add/Remove Programs)
if (-not (Test-Path $UninstallKey)) {{
    New-Item -Path $UninstallKey -Force | Out-Null
}}
$ExeSize = (Get-Item "$InstallDir\\nodeglow-agent.exe").Length / 1024
$UninstallCmd = "powershell -ExecutionPolicy Bypass -File `"$InstallDir\\uninstall.ps1`""
Set-ItemProperty -Path $UninstallKey -Name "DisplayName" -Value "Nodeglow Agent"
Set-ItemProperty -Path $UninstallKey -Name "DisplayVersion" -Value $AgentVersion
Set-ItemProperty -Path $UninstallKey -Name "Publisher" -Value "Nodeglow"
Set-ItemProperty -Path $UninstallKey -Name "InstallLocation" -Value $InstallDir
Set-ItemProperty -Path $UninstallKey -Name "UninstallString" -Value $UninstallCmd
Set-ItemProperty -Path $UninstallKey -Name "DisplayIcon" -Value "$InstallDir\\nodeglow-agent.exe"
Set-ItemProperty -Path $UninstallKey -Name "EstimatedSize" -Value ([int]$ExeSize)
Set-ItemProperty -Path $UninstallKey -Name "NoModify" -Value 1 -Type DWord
Set-ItemProperty -Path $UninstallKey -Name "NoRepair" -Value 1 -Type DWord
Set-ItemProperty -Path $UninstallKey -Name "InstallDate" -Value (Get-Date -Format "yyyyMMdd")

Write-Host "  Testing connection..."
try {{
    $testBody = @{{ hostname = $Hostname; platform = "Windows"; agent_version = $AgentVersion }} | ConvertTo-Json
    $testResp = Invoke-RestMethod -Uri "$Server/api/agent/report" -Method Post -Body $testBody -ContentType "application/json" -Headers @{{ Authorization = "Bearer $Token" }} -TimeoutSec 10
    if ($testResp.ok) {{
        Write-Host "  Connection test: SUCCESS" -ForegroundColor Green
    }} else {{
        Write-Host "  Connection test: FAILED (unexpected response)" -ForegroundColor Yellow
    }}
}} catch {{
    Write-Host "  Connection test: FAILED - $($_.Exception.Message)" -ForegroundColor Yellow
    Write-Host "  The agent will retry when the task starts." -ForegroundColor Yellow
}}

Start-ScheduledTask -TaskName $TaskName

Write-Host ""
Write-Host "  Done! Agent '$Hostname' is running." -ForegroundColor Green
Write-Host "  Installed to: $InstallDir" -ForegroundColor Gray
Write-Host "  Visible in: Settings > Apps > Installed apps" -ForegroundColor Gray
Write-Host ""
Write-Host "  Status:     Get-ScheduledTask -TaskName $TaskName"
Write-Host "  Stop:       Stop-ScheduledTask -TaskName $TaskName"
Write-Host "  Uninstall:  & '$InstallDir\\uninstall.bat'"
Write-Host ""
'''

    from fastapi.responses import Response
    return Response(content=script, media_type="text/plain")


# ── Download: Agent files (used by install scripts) ──────────────────────────

@router.get("/agents/download/{platform}")
async def agent_download(request: Request, platform: str):
    """Download agent binary for the given platform."""
    if platform == "windows":
        return FileResponse("static/nodeglow-agent.exe",
                            filename="nodeglow-agent.exe", media_type="application/octet-stream")
    # Rust binary for Linux (fallback to old Python script if not available)
    rust_path = Path("static/nodeglow-agent-linux")
    if rust_path.exists():
        return FileResponse(str(rust_path),
                            filename="nodeglow-agent-linux", media_type="application/octet-stream")
    return FileResponse("static/nodeglow-agent-linux.py",
                        filename="nodeglow-agent-linux.py", media_type="text/x-python")


# ── API: Agent version check (for auto-update) ────────────────────────────────

_agent_file_cache: dict[str, tuple[str, float]] = {}  # platform -> (hash, mtime)
_agent_sig_cache: dict[str, tuple[str, float]] = {}    # platform -> (signature, mtime)


def _agent_binary_path(platform: str) -> Path | None:
    """Resolve the file served by agent_download for a platform, or None."""
    if platform == "windows":
        path = Path("static/nodeglow-agent.exe")
    else:
        # Prefer Rust binary, fall back to Python script
        path = Path("static/nodeglow-agent-linux")
        if not path.exists():
            path = Path("static/nodeglow-agent-linux.py")
    return path if path.exists() else None


def _get_agent_hash(platform: str) -> str:
    """Get SHA256 hash of the current agent binary/script. Cached by mtime."""
    path = _agent_binary_path(platform)
    if path is None:
        return ""

    mtime = path.stat().st_mtime
    cached = _agent_file_cache.get(platform)
    if cached and cached[1] == mtime:
        return cached[0]

    h = hashlib.sha256(path.read_bytes()).hexdigest()
    _agent_file_cache[platform] = (h, mtime)
    return h


def _get_agent_signature(platform: str) -> str:
    """Detached ed25519 signature (hex) over the served binary. Cached by mtime.

    Signs the exact bytes agent_download returns so the agent can verify the
    download against its embedded public key. Empty string if no binary exists.
    """
    path = _agent_binary_path(platform)
    if path is None:
        return ""

    mtime = path.stat().st_mtime
    cached = _agent_sig_cache.get(platform)
    if cached and cached[1] == mtime:
        return cached[0]

    sig = agent_signing.sign_bytes(path.read_bytes())
    _agent_sig_cache[platform] = (sig, mtime)
    return sig


@router.get("/api/agent/version/{platform}")
async def agent_version(platform: str):
    """Returns the current agent version hash + ed25519 signature.

    Agents poll this to check for updates; the signature lets agents that have
    a configured update_public_key verify the binary before applying it.
    """
    if platform not in ("windows", "linux"):
        return JSONResponse({"error": "Invalid platform"}, status_code=400)
    return {
        "hash": _get_agent_hash(platform),
        "signature": _get_agent_signature(platform),
    }


@router.get("/api/agent/update-public-key")
async def agent_update_public_key():
    """Public half of the agent-update signing key (hex), for agents to embed."""
    return {"public_key": agent_signing.public_key_hex()}


# ── API: List agents (JSON) ─────────────────────────────────────────────────

@router.get("/api/agents")
async def api_agents_list(request: Request):
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Agent).order_by(Agent.name))
        agents = result.scalars().all()
        out = []
        for a in agents:
            online = False
            if a.last_seen:
                online = (datetime.utcnow() - a.last_seen).total_seconds() < 120
            out.append({
                "id": a.id,
                "name": a.name,
                "hostname": a.hostname,
                "platform": a.platform,
                "online": online,
                "last_seen": a.last_seen.isoformat() if a.last_seen else None,
            })
    return out
