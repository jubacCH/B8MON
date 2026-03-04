from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db, get_setting, set_setting

router = APIRouter(prefix="/settings")
templates = Jinja2Templates(directory="templates")


@router.get("", response_class=HTMLResponse)
async def settings_page(request: Request, db: AsyncSession = Depends(get_db)):
    site_name = await get_setting(db, "site_name", "Homelab Monitor")
    ping_interval = await get_setting(db, "ping_interval", "60")

    return templates.TemplateResponse("settings.html", {
        "request": request,
        "site_name": site_name,
        "ping_interval": ping_interval,
        "active_page": "settings",
        "saved": request.query_params.get("saved"),
    })


@router.post("/save")
async def save_settings(
    site_name: str = Form("Homelab Monitor"),
    ping_interval: str = Form("60"),
    db: AsyncSession = Depends(get_db),
):
    await set_setting(db, "site_name", site_name.strip())
    # Clamp interval
    try:
        interval = max(10, min(3600, int(ping_interval)))
    except ValueError:
        interval = 60
    await set_setting(db, "ping_interval", str(interval))

    # Update scheduler interval live
    from scheduler import scheduler
    job = scheduler.get_job("ping_checks")
    if job:
        job.reschedule(trigger="interval", seconds=interval)

    return RedirectResponse(url="/settings?saved=1", status_code=303)
