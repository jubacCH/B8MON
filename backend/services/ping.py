"""Ping helper queries used by API v1 and other routers.

Post-cutover: thin wrapper around `services.clickhouse_client` for the
ping_checks table. No Postgres session is needed.
"""
from __future__ import annotations

import asyncio

from services import clickhouse_client as ch


async def get_latest_by_host(host_ids: list[int]) -> dict[int, dict]:
    """Return {host_id: latest_record_dict} for the given host IDs.

    The dict shape is `{timestamp, success, latency_ms, host_name}`.
    """
    if not host_ids:
        return {}
    return await ch.get_latest_ping_per_host(host_ids)


async def get_uptime_map() -> dict[int, dict]:
    """Return {host_id: {h24, d7, d30}} uptime percentages over multiple
    rolling windows. Three CH queries — one per window, run in parallel."""
    windows = ((24, "h24"), (24 * 7, "d7"), (24 * 30, "d30"))
    results = await asyncio.gather(
        *(ch.get_ping_uptime(hours=hours) for hours, _ in windows)
    )
    out: dict[int, dict] = {}
    for (_, key), rows in zip(windows, results):
        for host_id, stats in rows.items():
            out.setdefault(host_id, {})[key] = stats["uptime_pct"]
    return out
