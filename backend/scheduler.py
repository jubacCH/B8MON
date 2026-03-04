"""
Background scheduler – runs collection jobs periodically.
"""
import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import delete, select

from collectors.ping import ping_host
from database import AsyncSessionLocal, PingHost, PingResult

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()


async def run_ping_checks():
    """Ping all enabled hosts and store results."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(PingHost).where(PingHost.enabled == True))
        hosts = result.scalars().all()

    if not hosts:
        return

    async with AsyncSessionLocal() as db:
        for host in hosts:
            success, latency = await ping_host(host.hostname)
            db.add(PingResult(
                host_id=host.id,
                timestamp=datetime.utcnow(),
                success=success,
                latency_ms=latency,
            ))
        await db.commit()

    logger.debug("Ping check done for %d hosts", len(hosts))


async def cleanup_old_results():
    """Remove ping results older than 30 days."""
    cutoff = datetime.utcnow() - timedelta(days=30)
    async with AsyncSessionLocal() as db:
        await db.execute(delete(PingResult).where(PingResult.timestamp < cutoff))
        await db.commit()
    logger.info("Cleaned up old ping results")


def start_scheduler():
    scheduler.add_job(run_ping_checks, "interval", seconds=60, id="ping_checks", replace_existing=True)
    scheduler.add_job(cleanup_old_results, "cron", hour=3, minute=0, id="cleanup", replace_existing=True)
    scheduler.start()
    logger.info("Scheduler started")


def stop_scheduler():
    scheduler.shutdown(wait=False)
