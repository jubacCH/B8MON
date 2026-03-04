from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from database import AsyncSessionLocal, get_setting, init_db
from scheduler import start_scheduler, stop_scheduler
from routers import dashboard, ping, proxmox, setup, settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="Homelab Monitor", lifespan=lifespan)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.middleware("http")
async def inject_site_name(request: Request, call_next):
    async with AsyncSessionLocal() as db:
        request.state.site_name = await get_setting(db, "site_name", "Homelab Monitor")
    return await call_next(request)


app.include_router(dashboard.router)
app.include_router(setup.router)
app.include_router(ping.router)
app.include_router(settings.router)
app.include_router(proxmox.router)
