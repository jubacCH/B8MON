import os
import time
from contextlib import asynccontextmanager

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
    api_v1,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    from models import init_db as init_new_db
    await init_new_db()
    await start_scheduler()
    os.environ["VIGIL_START_TIME"] = str(time.time())
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


app = FastAPI(
    title="NODEGLOW",
    version="1.0.0",
    description="Network monitoring and incident correlation platform",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory="static"), name="static")


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
    raw = await count_all_by_type(db)
    counts = {k: raw.get(k, 0) for k in _NAV_KEYS}

    _nav_cache["counts"] = counts
    _nav_cache["ts"] = now
    return counts


@app.middleware("http")
async def inject_globals(request: Request, call_next):
    if request.url.path.startswith("/static/") or request.url.path == "/health" \
            or request.url.path.startswith("/api/agent/") or request.url.path.startswith("/api/v1/") \
            or request.url.path.startswith("/api/docs") or request.url.path.startswith("/api/redoc") \
            or request.url.path.startswith("/api/openapi") \
            or request.url.path.startswith("/ws/") \
            or request.url.path.startswith("/install/") or "/download/" in request.url.path:
        return await call_next(request)

    PUBLIC_PATHS = {"/login", "/logout"}
    is_public = request.url.path in PUBLIC_PATHS or request.url.path.startswith("/setup")

    # Redirect to setup wizard when no setup has been completed yet
    if not is_public:
        from database import is_setup_complete as _is_setup, get_current_user, AsyncSessionLocal as _ASL
        async with _ASL() as check_db:
            if not await _is_setup(check_db):
                from fastapi.responses import RedirectResponse as _RR
                return _RR(url="/setup", status_code=302)
        async with _ASL() as auth_db:
            user = await get_current_user(request, auth_db)
        if user is None:
            from fastapi.responses import RedirectResponse as _RR
            return _RR(url="/login", status_code=302)
        request.state.current_user = user
        role = getattr(user, "role", "admin") or "admin"
        if (request.url.path.startswith("/settings") or request.url.path.startswith("/users")) \
                and role != "admin":
            from fastapi.responses import HTMLResponse as _HTML
            return _HTML(
                "<html><body style='background:#0b0d14;color:#e2e8f0;font-family:sans-serif;"
                "display:flex;align-items:center;justify-content:center;height:100vh;'>"
                "<div style='text-align:center'><p style='font-size:3rem;margin:0'>403</p>"
                "<p style='color:#94a3b8'>Admin access required.</p>"
                "<a href='/' style='color:#3b82f6;font-size:.875rem'>← Back</a></div></body></html>",
                status_code=403,
            )
        if role == "readonly" and request.method in ("POST", "PUT", "DELETE", "PATCH"):
            from fastapi.responses import HTMLResponse as _HTML
            return _HTML(
                "<html><body style='background:#0b0d14;color:#e2e8f0;font-family:sans-serif;"
                "display:flex;align-items:center;justify-content:center;height:100vh;'>"
                "<div style='text-align:center'><p style='font-size:3rem;margin:0'>403</p>"
                "<p style='color:#94a3b8'>Read-only access — no changes allowed.</p>"
                "<a href='/' style='color:#3b82f6;font-size:.875rem'>← Back</a></div></body></html>",
                status_code=403,
            )
    else:
        request.state.current_user = None

    from templating import current_tz
    now = time.time()
    if now - _settings_cache["ts"] < _NAV_CACHE_TTL:
        request.state.site_name = _settings_cache["site_name"]
        tz_name = _settings_cache["timezone"]
    else:
        async with AsyncSessionLocal() as db:
            _settings_cache["site_name"] = await get_setting(db, "site_name", "NODEGLOW")
            _settings_cache["timezone"] = await get_setting(db, "timezone", "UTC")
            _settings_cache["ts"] = now
        request.state.site_name = _settings_cache["site_name"]
        tz_name = _settings_cache["timezone"]

    async with AsyncSessionLocal() as db:
        request.state.nav_counts = await _get_nav_counts(db)
    current_tz.set(tz_name)
    return await call_next(request)


# ── Global WebSocket ─────────────────────────────────────────────────────────
@app.websocket("/ws/live")
async def ws_live(websocket: WebSocket):
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
app.include_router(system.router)
app.include_router(integrations_router.router)
app.include_router(agents_router.router)
app.include_router(subnet_scanner.router)
app.include_router(credentials.router)
app.include_router(snmp_router.router)
app.include_router(ssl_monitor.router)
app.include_router(api_v1.router)
