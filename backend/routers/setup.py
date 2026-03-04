from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db, is_setup_complete, set_setting

router = APIRouter(prefix="/setup")
templates = Jinja2Templates(directory="templates")


@router.get("", response_class=HTMLResponse)
async def setup_page(request: Request, db: AsyncSession = Depends(get_db)):
    if await is_setup_complete(db):
        return RedirectResponse(url="/")
    return templates.TemplateResponse("setup.html", {"request": request})


@router.post("/complete")
async def complete_setup(
    site_name: str = Form("Homelab Monitor"),
    db: AsyncSession = Depends(get_db),
):
    await set_setting(db, "site_name", site_name.strip() or "Homelab Monitor")
    await set_setting(db, "setup_complete", "true")
    await set_setting(db, "ping_interval", "60")
    return RedirectResponse(url="/", status_code=303)
