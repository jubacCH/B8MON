import os
import time
from contextlib import asynccontextmanager
from urllib.parse import parse_qs

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from database import AsyncSessionLocal, get_setting, init_db
from models.integration import IntegrationConfig
from scheduler import start_scheduler, stop_scheduler
from routers import (
    auth, dashboard, ping, setup, settings, alerts, users,
    syslog as syslog_router,
    incidents as incidents_router,
    system,
    integrations as integrations_router,
    agents as agents_router,
    subnet_scanner,
    credentials,
    snmp as snmp_router,
    ssl_monitor,
    update,
    api_v1,
    rules as rules_router,
    digest as digest_router,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    from models import init_db as init_new_db
    await init_new_db()
    await start_scheduler()
    os.environ["NODEGLOW_START_TIME"] = str(time.time())
    from services.syslog import start_syslog_server, stop_syslog_server
    try:
        async with AsyncSessionLocal() as _db:
            syslog_port = int(await get_setting(_db, "syslog_port", ""))
    except (ValueError, TypeError):
        syslog_port = int(os.environ.get("SYSLOG_PORT", "1514"))
    await start_syslog_server(udp_port=syslog_port, tcp_port=syslog_port)
    yield
    await stop_syslog_server()
    stop_scheduler()


_debug = os.environ.get("DEBUG", "").lower() in ("1", "true", "yes")
app = FastAPI(
    title="NODEGLOW",
    version="1.0.0",
    description="Network monitoring and incident correlation platform",
    docs_url="/api/docs" if _debug else None,
    redoc_url="/api/redoc" if _debug else None,
    openapi_url="/api/openapi.json" if _debug else None,
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory="static"), name="static")

# ── CORS (development: Next.js on localhost:3000) ─────────────────────────────
from starlette.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/v2/nav-counts")
async def nav_counts_api():
    """Sidebar badge counts for the Next.js frontend."""
    from database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        counts = await _get_nav_counts(db)
    return counts


@app.post("/api/tasks/scan-all")
async def scan_all_hosts():
    """Trigger port discovery on all enabled hosts."""
    from services.port_discovery import run_port_discovery
    await run_port_discovery()
    return {"ok": True}


@app.get("/health")
async def health():
    from sqlalchemy import text as sa_text
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(sa_text("SELECT 1"))
        return {"status": "ok", "db": "connected"}
    except Exception as e:
        return {"status": "error", "db": str(e)}


# ── Nav counts cache (60s TTL, single GROUP BY query) ────────────────────────

_nav_cache: dict = {"counts": {}, "ts": 0.0}
_settings_cache: dict = {"site_name": "NODEGLOW", "timezone": "UTC", "ts": 0.0}
_NAV_CACHE_TTL = 60


def invalidate_settings_cache():
    """Call after saving settings to force refresh on next request."""
    _settings_cache["ts"] = 0.0


def invalidate_nav_cache():
    """Call after adding/removing integrations to force refresh on next request."""
    _nav_cache["ts"] = 0.0

_NAV_KEYS = (
    "proxmox", "unifi", "unas", "pihole", "adguard", "portainer",
    "truenas", "synology", "firewall", "hass", "gitea", "phpipam",
    "speedtest", "ups", "redfish",
)


async def _get_nav_counts(db) -> dict:
    now = time.time()
    if now - _nav_cache["ts"] < _NAV_CACHE_TTL and _nav_cache["counts"]:
        return _nav_cache["counts"]

    from services.integration import count_all_by_type
    from sqlalchemy import func
    from models.discovered_port import DiscoveredPort
    raw = await count_all_by_type(db)
    counts = {k: raw.get(k, 0) for k in _NAV_KEYS}

    # Count pending tasks (new ports + new SSL certs)
    new_ports = (await db.execute(
        select(func.count()).select_from(DiscoveredPort)
        .where(DiscoveredPort.last_open == True, DiscoveredPort.status == "new")
    )).scalar() or 0
    new_ssl = (await db.execute(
        select(func.count()).select_from(DiscoveredPort)
        .where(DiscoveredPort.last_open == True, DiscoveredPort.has_ssl == True, DiscoveredPort.ssl_status == "new")
    )).scalar() or 0
    counts["tasks"] = new_ports + new_ssl

    _nav_cache["counts"] = counts
    _nav_cache["ts"] = now
    return counts


@app.get("/api/tasks")
async def tasks_api():
    """Aggregate all pending admin tasks."""
    from sqlalchemy import func
    from models.discovered_port import DiscoveredPort
    from models.ping import PingHost

    async with AsyncSessionLocal() as db:
        # Discovered ports needing attention (new ports + new SSL)
        port_rows = (await db.execute(
            select(DiscoveredPort, PingHost.name.label("host_name"), PingHost.hostname.label("host_hostname"))
            .join(PingHost, PingHost.id == DiscoveredPort.host_id)
            .where(DiscoveredPort.last_open == True)
            .order_by(PingHost.name, DiscoveredPort.port)
        )).all()

        port_tasks = []
        ssl_tasks = []
        for row in port_rows:
            dp = row[0]
            host_name = row.host_name
            host_hostname = row.host_hostname
            base = {
                "id": dp.id, "host_id": dp.host_id,
                "host_name": host_name, "host_hostname": host_hostname,
                "port": dp.port, "protocol": dp.protocol,
                "service": dp.service,
                "first_seen": str(dp.first_seen) if dp.first_seen else None,
                "last_seen": str(dp.last_seen) if dp.last_seen else None,
            }
            port_tasks.append({
                **base,
                "status": dp.status,
            })
            if dp.has_ssl:
                ssl_tasks.append({
                    **base,
                    "ssl_issuer": dp.ssl_issuer,
                    "ssl_subject": dp.ssl_subject,
                    "ssl_expiry_days": dp.ssl_expiry_days,
                    "ssl_expiry_date": dp.ssl_expiry_date,
                    "ssl_status": dp.ssl_status,
                })

        # Summary counts
        new_ports = sum(1 for p in port_tasks if p["status"] == "new")
        new_ssl = sum(1 for s in ssl_tasks if s["ssl_status"] == "new")

        return {
            "port_tasks": port_tasks,
            "ssl_tasks": ssl_tasks,
            "summary": {
                "new_ports": new_ports,
                "new_ssl": new_ssl,
                "total_pending": new_ports + new_ssl,
            },
        }


@app.middleware("http")
async def inject_globals(request: Request, call_next):
    # Skip auth entirely for these paths
    _skip = (
        request.url.path.startswith("/static/") or request.url.path == "/health"
        or request.url.path.startswith("/api/agent/")
        or request.url.path.startswith("/api/v2/") or request.url.path.startswith("/api/auth/")
        or request.url.path.startswith("/api/docs") or request.url.path.startswith("/api/redoc")
        or request.url.path.startswith("/api/openapi")
        or request.url.path.startswith("/ws/")
        or request.url.path.startswith("/install/") or request.url.path.startswith("/agents/download/")
    )
    if _skip:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    is_api = request.url.path.startswith("/api/")

    # CSRF protection for state-changing methods (skip for API — frontend sends x-csrf-token header)
    if request.method in ("POST", "PUT", "DELETE", "PATCH") and not is_api:
        from csrf import validate_csrf, csrf_error_response
        content_type = request.headers.get("content-type", "")
        form_data = None
        if "form" in content_type:
            body = await request.body()
            parsed = parse_qs(body.decode("utf-8", errors="replace"))
            form_data = {k: v[0] for k, v in parsed.items()}
        if not validate_csrf(request, form_data):
            return csrf_error_response(request)

    is_public = request.url.path.startswith("/setup")

    if not is_public:
        from database import is_setup_complete as _is_setup, get_current_user, AsyncSessionLocal as _ASL
        from fastapi.responses import JSONResponse as _JSON
        async with _ASL() as check_db:
            if not await _is_setup(check_db):
                if is_api:
                    return _JSON({"error": "Setup not complete"}, status_code=503)
                from fastapi.responses import RedirectResponse as _RR
                return _RR(url="/setup", status_code=302)
        async with _ASL() as auth_db:
            user = await get_current_user(request, auth_db)
        if user is None:
            # /api/v1/ has its own auth (API key) — let it through
            if is_api and not request.url.path.startswith("/api/v1/"):
                return _JSON({"error": "Unauthorized"}, status_code=401)
            # Non-API HTML routes: redirect unauthenticated users to login
            if not is_api and request.url.path not in ("/login", "/favicon.ico"):
                from fastapi.responses import RedirectResponse as _RR
                return _RR(url="/login", status_code=302)
            request.state.current_user = None
            response = await call_next(request)
            return response
        request.state.current_user = user
        role = getattr(user, "role", "admin") or "admin"
        if is_api:
            if (request.url.path.startswith("/api/settings") or request.url.path.startswith("/api/users")) \
                    and role != "admin":
                return _JSON({"error": "Admin access required"}, status_code=403)
            if role == "readonly" and request.method in ("POST", "PUT", "DELETE", "PATCH"):
                return _JSON({"error": "Read-only access"}, status_code=403)
    else:
        request.state.current_user = None

    response = await call_next(request)

    # Security headers
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["X-XSS-Protection"] = "1; mode=block"

    return response


# ── Global WebSocket ─────────────────────────────────────────────────────────
@app.websocket("/ws/live")
async def ws_live(websocket: WebSocket):
    # Authenticate via session cookie before accepting
    from database import AsyncSessionLocal as _ASL
    from models.settings import Session as _Sess, User as _User
    from sqlalchemy import select as _sel
    from datetime import datetime as _dt
    token = websocket.cookies.get("nodeglow_session")
    if not token:
        await websocket.close(code=4401, reason="Unauthorized")
        return
    async with _ASL() as _db:
        row = (await _db.execute(
            _sel(_Sess).where(_Sess.token == token, _Sess.expires_at > _dt.utcnow())
        )).scalar_one_or_none()
        if not row:
            await websocket.close(code=4401, reason="Unauthorized")
            return

    from services.websocket import register, unregister
    await register(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        unregister(websocket)


# ── Legacy redirect: /ping → /hosts ──────────────────────────────────────────
from fastapi.responses import RedirectResponse

@app.get("/ping/{path:path}")
@app.get("/ping")
async def _ping_redirect(request: Request, path: str = ""):
    qs = str(request.query_params)
    target = f"/hosts/{path}" if path else "/hosts"
    if qs:
        target += f"?{qs}"
    return RedirectResponse(url=target, status_code=301)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(setup.router)
app.include_router(ping.router)
app.include_router(settings.router)
app.include_router(alerts.router)
app.include_router(syslog_router.router)
app.include_router(incidents_router.router)
app.include_router(users.router)
app.include_router(users.api_router)
app.include_router(system.router)
app.include_router(integrations_router.router)
app.include_router(agents_router.router)
app.include_router(subnet_scanner.router)
app.include_router(credentials.router)
app.include_router(snmp_router.router)
app.include_router(ssl_monitor.router)
app.include_router(update.router)
app.include_router(api_v1.router)
app.include_router(rules_router.router)
app.include_router(digest_router.router)
