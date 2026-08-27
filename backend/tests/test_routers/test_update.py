"""Tests for the update proxy endpoints."""
from unittest.mock import AsyncMock, patch

import pytest

from services import shared_state


@pytest.fixture(autouse=True)
def _clear_rate_limits():
    """Rate-limit counters are process-global.

    /apply allows 2 requests per 300 s, so without this the third test in the
    module gets a 429 instead of exercising the endpoint. Isolating the counter
    is correct regardless of test order.
    """
    shared_state.reset()
    yield
    shared_state.reset()


class Viewer:
    id = 2
    username = "viewer"
    role = "viewer"


RUN_STATE = {
    "run_id": "2026-06-10T14-02-11",
    "status": "running",
    "step": "migrate",
    "steps": [
        {"name": "preflight", "status": "ok", "detail": "12.4 GB free"},
        {"name": "migrate", "status": "running", "detail": None},
    ],
    "started_at": "2026-06-10T14:02:11",
    "finished_at": None,
    "error": None,
}


async def test_status_proxies_sidecar_state(client):
    with patch("routers.update._sidecar_get", new_callable=AsyncMock,
               return_value=(200, RUN_STATE)):
        resp = await client.get("/api/update/status")

    assert resp.status_code == 200
    assert resp.json()["step"] == "migrate"


async def test_status_reports_unavailable_when_sidecar_down(client):
    with patch("routers.update._sidecar_get", new_callable=AsyncMock,
               side_effect=OSError("connection refused")):
        resp = await client.get("/api/update/status")

    body = resp.json()
    assert body["status"] == "unavailable"
    assert body["run_id"] is None
    assert body["error"]


async def test_backups_listed_for_admin(client):
    payload = {"backups": [{"name": "pre-update-x.dump.gz", "size": 42, "mtime": 1.0}]}
    with patch("routers.update._sidecar_get", new_callable=AsyncMock,
               return_value=(200, payload)):
        resp = await client.get("/api/update/backups")

    assert resp.status_code == 200
    assert resp.json()["backups"][0]["name"] == "pre-update-x.dump.gz"


async def test_backups_require_admin(client):
    with patch("database.get_current_user", new_callable=AsyncMock, return_value=Viewer()):
        resp = await client.get("/api/update/backups")
    assert resp.status_code == 403


async def test_apply_returns_run_id(client):
    with patch("routers.update._sidecar_post", new_callable=AsyncMock,
               return_value=(202, {"ok": True, "run_id": "2026-06-10T14-02-11"})):
        resp = await client.post("/api/update/apply")

    body = resp.json()
    assert body["ok"] is True
    assert body["run_id"] == "2026-06-10T14-02-11"


async def test_apply_passes_through_conflict(client):
    with patch("routers.update._sidecar_post", new_callable=AsyncMock,
               return_value=(409, {"ok": False, "error": "An update run is already active"})):
        resp = await client.post("/api/update/apply")

    assert resp.status_code == 409
    assert "already active" in resp.json()["error"]


async def test_apply_requires_admin(client):
    with patch("database.get_current_user", new_callable=AsyncMock, return_value=Viewer()):
        resp = await client.post("/api/update/apply")
    assert resp.status_code == 403
