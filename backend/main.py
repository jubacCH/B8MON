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
from routers import dashboard, ping, proxmox, setup, settings, unifi, unas, pihole, adguard, portainer, truenas, synology, firewall, hass, gitea, phpipam, speedtest, nut, redfish, alerts


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="Homelab Monitor", lifespan=lifespan)

app.mount("/static", StaticFiles(directory="static"), name="static")


def _count(q):
    return q.scalar() or 0


@app.middleware("http")
async def inject_globals(request: Request, call_next):
    async with AsyncSessionLocal() as db:
        request.state.site_name = await get_setting(db, "site_name", "Homelab Monitor")
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
