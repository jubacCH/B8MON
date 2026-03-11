"""Ping helper queries used by API v1 and other routers."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import Integer, func, select
from sqlalchemy.sql.expression import cast
from sqlalchemy.ext.asyncio import AsyncSession

from database import PingResult


async def get_latest_by_host(
    db: AsyncSession, host_ids: list[int]
) -> dict[int, PingResult]:
    """Return {host_id: latest PingResult} for the given host IDs."""
    if not host_ids:
        return {}
    latest_sub = (
        select(PingResult.host_id, func.max(PingResult.id).label("max_id"))
        .where(PingResult.host_id.in_(host_ids))
        .group_by(PingResult.host_id)
        .subquery()
    )
    rows = (await db.execute(
        select(PingResult).join(latest_sub, PingResult.id == latest_sub.c.max_id)
    )).scalars().all()
    return {r.host_id: r for r in rows}


async def get_uptime_map(db: AsyncSession) -> dict[int, dict]:
    """Return {host_id: {h24: pct, d7: pct, d30: pct}} for all hosts."""
    now = datetime.utcnow()
    uptime_map: dict[int, dict] = {}
    for window, key in [
        (timedelta(hours=24), "h24"),
        (timedelta(days=7), "d7"),
        (timedelta(days=30), "d30"),
    ]:
        rows = await db.execute(
            select(
                PingResult.host_id,
                func.count().label("total"),
                func.sum(cast(PingResult.success, Integer)).label("ok"),
            )
            .where(PingResult.timestamp >= now - window)
            .group_by(PingResult.host_id)
        )
        for host_id, total, ok in rows:
            if host_id not in uptime_map:
                uptime_map[host_id] = {}
            uptime_map[host_id][key] = round((ok or 0) / total * 100, 1) if total else None
    return uptime_map
