from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from sqlalchemy import func, select

from database import (
    AsyncSessionLocal, get_setting, init_db,
    ProxmoxCluster, UnifiController, UnasServer,
    PiholeInstance, AdguardInstance, PortainerInstance, TruenasServer,
    SynologyServer, FirewallInstance, HassInstance, GiteaInstance,
    PhpipamServer, SpeedtestConfig, NutInstance, RedfishServer,
)
from scheduler import start_scheduler, stop_scheduler
from routers import auth, dashboard, ping, proxmox, setup, settings, unifi, unas, pihole, adguard, portainer, truenas, synology, firewall, hass, gitea, phpipam, speedtest, nut, redfish, alerts, users


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="Vigil", lifespan=lifespan)

app.mount("/static", StaticFiles(directory="static"), name="static")


def _count(q):
    return q.scalar() or 0


@app.middleware("http")
async def inject_globals(request: Request, call_next):
    # Auth check – skip for public paths
    PUBLIC_PATHS = {"/login", "/logout"}
    is_public = request.url.path in PUBLIC_PATHS or request.url.path.startswith("/setup")
    if not is_public:
        from database import get_current_user, AsyncSessionLocal as _ASL
        async with _ASL() as auth_db:
            user = await get_current_user(request, auth_db)
        if user is None:
            from fastapi.responses import RedirectResponse as _RR
            return _RR(url="/login", status_code=302)
        request.state.current_user = user
        role = getattr(user, "role", "admin") or "admin"
        # Admin-only paths
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
        # Read-only users cannot mutate
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

    async with AsyncSessionLocal() as db:
        request.state.site_name = await get_setting(db, "site_name", "Vigil")
        # Count configured integrations so nav links are only shown when set up
        counts = {}
        for key, model in [
            ("proxmox",   ProxmoxCluster),
            ("unifi",     UnifiController),
            ("unas",      UnasServer),
            ("pihole",    PiholeInstance),
            ("adguard",   AdguardInstance),
            ("portainer", PortainerInstance),
            ("truenas",   TruenasServer),
            ("synology",  SynologyServer),
            ("firewall",  FirewallInstance),
            ("hass",      HassInstance),
            ("gitea",     GiteaInstance),
            ("phpipam",   PhpipamServer),
            ("speedtest", SpeedtestConfig),
            ("ups",       NutInstance),
            ("redfish",   RedfishServer),
        ]:
            counts[key] = _count(await db.execute(select(func.count()).select_from(model)))
        request.state.nav_counts = counts
    return await call_next(request)


app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(setup.router)
app.include_router(ping.router)
app.include_router(settings.router)
app.include_router(proxmox.router)
app.include_router(unifi.router)
app.include_router(unas.router)
app.include_router(pihole.router)
app.include_router(adguard.router)
app.include_router(portainer.router)
app.include_router(truenas.router)
app.include_router(synology.router)
app.include_router(firewall.router)
app.include_router(hass.router)
app.include_router(gitea.router)
app.include_router(phpipam.router)
app.include_router(speedtest.router)
app.include_router(nut.router)
app.include_router(redfish.router)
app.include_router(alerts.router)
app.include_router(users.router)
