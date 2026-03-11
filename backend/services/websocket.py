"""
Global WebSocket hub — broadcasts events to all connected clients.
Used by dashboard, agents page, and any future live views.
"""
import asyncio
import json
import logging
from datetime import datetime
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)

_clients: list[WebSocket] = []


def get_client_count() -> int:
    return len(_clients)


async def register(ws: WebSocket):
    await ws.accept()
    _clients.append(ws)
    logger.debug("WebSocket client connected (%d total)", len(_clients))


def unregister(ws: WebSocket):
    try:
        _clients.remove(ws)
    except ValueError:
        pass
    logger.debug("WebSocket client disconnected (%d remaining)", len(_clients))


async def broadcast(event_type: str, data: dict):
    """Broadcast an event to all connected WebSocket clients."""
    if not _clients:
        return
    msg = json.dumps({"type": event_type, "ts": datetime.utcnow().isoformat(), **data}, default=str)
    for ws in _clients[:]:
        try:
            await ws.send_text(msg)
        except Exception:
            try:
                _clients.remove(ws)
            except ValueError:
                pass


# Convenience methods for common events

async def broadcast_ping_update(host_id: int, name: str, online: bool, latency_ms: float | None):
    await broadcast("ping_update", {
        "host_id": host_id,
        "name": name,
        "online": online,
        "latency_ms": latency_ms,
    })


async def broadcast_agent_metric(agent_id: int, agent_name: str, metrics: dict):
    await broadcast("agent_metric", {
        "agent_id": agent_id,
        "agent_name": agent_name,
        **metrics,
    })
