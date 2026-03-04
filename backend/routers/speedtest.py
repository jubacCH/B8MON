"""Speedtest router – single-config internet speed monitor."""
from __future__ import annotations
import json
import logging
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from collectors.speedtest import run_speedtest, check_speedtest_available
from database import SpeedtestConfig, SpeedtestResult, get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/speedtest")
templates = Jinja2Templates(directory="templates")

# Simple in-memory flag to prevent concurrent tests
_test_running: bool = False


async def _get_or_create_config(db: AsyncSession) -> SpeedtestConfig:
    """Return the singleton SpeedtestConfig, creating it if it doesn't exist."""
    result = await db.execute(select(SpeedtestConfig).limit(1))
    cfg = result.scalar_one_or_none()
    if cfg is None:
        cfg = SpeedtestConfig(name="Speedtest", schedule_minutes=60)
        db.add(cfg)
        await db.commit()
        await db.refresh(cfg)
    return cfg


async def _run_and_store(config_id: int, server_id: str | None) -> None:
    """Background task: run speedtest and persist result."""
    global _test_running
    _test_running = True
    from database import AsyncSessionLocal
    try:
        data = await run_speedtest(server_id)
        async with AsyncSessionLocal() as db:
            db.add(SpeedtestResult(
                config_id     = config_id,
                timestamp     = datetime.utcnow(),
                ok            = True,
                download_mbps = data["download_mbps"],
                upload_mbps   = data["upload_mbps"],
                ping_ms       = data["ping_ms"],
                server_name   = data["server_name"],
                error         = None,
            ))
            await db.commit()
        logger.info("Speedtest complete: %.1f / %.1f Mbps, %.0f ms",
                    data["download_mbps"], data["upload_mbps"], data["ping_ms"])
    except Exception as exc:
        logger.exception("Speedtest failed: %s", exc)
        from database import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            db.add(SpeedtestResult(
                config_id = config_id,
                timestamp = datetime.utcnow(),
                ok        = False,
                error     = str(exc),
            ))
            await db.commit()
    finally:
        _test_running = False


@router.get("", response_class=HTMLResponse)
async def speedtest_page(request: Request, db: AsyncSession = Depends(get_db)):
    cfg = await _get_or_create_config(db)

    result = await db.execute(
        select(SpeedtestResult)
        .where(SpeedtestResult.config_id == cfg.id)
        .order_by(SpeedtestResult.timestamp.desc())
        .limit(30)
    )
    results = list(reversed(result.scalars().all()))

    latest = results[-1] if results else None
    available = await check_speedtest_available()

    # Build chart data (only successful results)
    chart_labels = []
    chart_dl     = []
    chart_ul     = []
    chart_ping   = []
    for r in results:
        if r.ok:
            chart_labels.append(r.timestamp.strftime("%m/%d %H:%M"))
            chart_dl.append(r.download_mbps)
            chart_ul.append(r.upload_mbps)
            chart_ping.append(r.ping_ms)

    return templates.TemplateResponse("speedtest_detail.html", {
        "request":       request,
        "config":        cfg,
        "results":       list(reversed(results)),  # newest first for table
        "latest":        latest,
        "available":     available,
        "test_running":  _test_running,
        "chart_labels":  json.dumps(chart_labels),
        "chart_dl":      json.dumps(chart_dl),
        "chart_ul":      json.dumps(chart_ul),
        "chart_ping":    json.dumps(chart_ping),
        "active_page":   "speedtest",
    })


@router.post("/configure")
async def speedtest_configure(
    name:             str = Form("Speedtest"),
    schedule_minutes: str = Form("60"),
    server_id:        str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    cfg = await _get_or_create_config(db)
    cfg.name = name.strip() or "Speedtest"
    try:
        cfg.schedule_minutes = max(5, min(1440, int(schedule_minutes)))
    except ValueError:
        cfg.schedule_minutes = 60
    cfg.server_id = server_id.strip() or None
    await db.commit()

    # Reschedule the speedtest job if scheduler is running
    try:
        from scheduler import scheduler
        job = scheduler.get_job("speedtest_checks")
        if job:
            job.reschedule(trigger="interval", minutes=cfg.schedule_minutes)
    except Exception:
        pass

    return RedirectResponse(url="/speedtest?saved=1", status_code=303)


@router.post("/run")
async def speedtest_run(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    global _test_running
    if _test_running:
        return JSONResponse({"status": "already_running"})

    cfg = await _get_or_create_config(db)
    background_tasks.add_task(_run_and_store, cfg.id, cfg.server_id)
    return RedirectResponse(url="/speedtest?running=1", status_code=303)


@router.get("/api/status")
async def speedtest_api_status():
    """Return whether a test is currently running."""
    return {"running": _test_running}


@router.post("/delete/{result_id}")
async def speedtest_delete_result(
    result_id: int,
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(SpeedtestResult, result_id)
    if row:
        await db.delete(row)
        await db.commit()
    return RedirectResponse(url="/speedtest", status_code=303)
