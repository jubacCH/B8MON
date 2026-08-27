"""Self-update: check GitHub for new commits and apply updates via sidecar."""
import logging
import os

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ratelimit import rate_limit

router = APIRouter(prefix="/api/update")
log = logging.getLogger(__name__)

SIDECAR_URL = os.environ.get("UPDATE_SIDECAR_URL", "http://updater:9100")
# Shared secret for authenticating to the updater sidecar. Must match the
# UPDATE_SIDECAR_TOKEN env var on the `updater` container.
_SIDECAR_TOKEN = os.environ.get("UPDATE_SIDECAR_TOKEN", "").strip()


def _sidecar_headers() -> dict[str, str]:
    if not _SIDECAR_TOKEN:
        # Fail-closed: refuse to even contact the sidecar without a token.
        raise RuntimeError(
            "UPDATE_SIDECAR_TOKEN is not configured on the backend container."
        )
    return {"Authorization": f"Bearer {_SIDECAR_TOKEN}"}


async def _sidecar_get(path: str, timeout: float = 15.0) -> tuple[int, dict]:
    """GET a sidecar endpoint, returning (status_code, decoded body)."""
    headers = _sidecar_headers()
    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        resp = await client.get(f"{SIDECAR_URL}{path}")
        try:
            data = resp.json()
        except ValueError:
            data = {}
        return resp.status_code, data


async def _sidecar_post(path: str, timeout: float = 30.0) -> tuple[int, dict]:
    """POST to a sidecar endpoint, returning (status_code, decoded body)."""
    headers = _sidecar_headers()
    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        resp = await client.post(f"{SIDECAR_URL}{path}")
        try:
            data = resp.json()
        except ValueError:
            data = {}
        return resp.status_code, data


def _unavailable_state(message: str) -> dict:
    return {
        "run_id": None,
        "status": "unavailable",
        "step": None,
        "steps": [],
        "started_at": None,
        "finished_at": None,
        "error": message,
    }



@router.get("/check")
@rate_limit(max_requests=5, window_seconds=60)
async def check_for_updates(request: Request):
    """Check for available updates via the update sidecar."""
    try:
        headers = _sidecar_headers()
        async with httpx.AsyncClient(timeout=35.0, headers=headers) as client:
            ver_resp = await client.get(f"{SIDECAR_URL}/version")
            local = ver_resp.json() if ver_resp.status_code == 200 else {"commit": "unknown"}

            check_resp = await client.get(f"{SIDECAR_URL}/check")
            check = check_resp.json() if check_resp.status_code == 200 else {}
    except RuntimeError as e:
        log.error("Update sidecar misconfigured: %s", e)
        return JSONResponse({
            "local": {"commit": "unknown"},
            "update_available": False,
            "error": "Update service is not configured (missing UPDATE_SIDECAR_TOKEN).",
        }, status_code=503)
    except Exception as e:
        log.error("Update sidecar unreachable: %s", e)
        return JSONResponse({
            "local": {"commit": "unknown"},
            "update_available": False,
            "error": "Update service unavailable. Is the updater sidecar running?",
        })

    return JSONResponse({
        "local": local,
        "remote_commit": check.get("changelog", [{}])[0].get("hash", "") if check.get("changelog") else "",
        "remote_version": "",
        "commits_behind": check.get("commits_behind", 0),
        "update_available": check.get("update_available", False),
        "changelog": check.get("changelog", []),
    })


@router.post("/apply")
@rate_limit(max_requests=2, window_seconds=300)
async def apply_update(request: Request):
    """Apply update via the sidecar (which has Docker socket access)."""
    user = getattr(request.state, "current_user", None)
    if not user or getattr(user, "role", "admin") != "admin":
        return JSONResponse({"error": "Admin access required"}, status_code=403)

    try:
        code, result = await _sidecar_post("/apply")
    except RuntimeError as e:
        log.error("Update sidecar misconfigured: %s", e)
        return JSONResponse(
            {"ok": False, "error": "Update service is not configured (missing UPDATE_SIDECAR_TOKEN)."},
            status_code=503,
        )
    except Exception as e:
        log.error("Update sidecar error: %s", e)
        return JSONResponse({"ok": False, "error": f"Update service unavailable: {e}"})

    if code == 409:
        return JSONResponse(
            {"ok": False, "error": result.get("error", "An update run is already active")},
            status_code=409,
        )
    if not result.get("ok"):
        return JSONResponse({"ok": False, "error": result.get("error", "Update failed")})

    return JSONResponse({
        "ok": True,
        "run_id": result.get("run_id"),
        "message": "Update started.",
    })


@router.get("/status")
# Polled every 2 s by the System -> Update view while a run is active.
@rate_limit(max_requests=120, window_seconds=60)
async def update_status(request: Request):
    """Live state of the current (or last) update run."""
    try:
        _, data = await _sidecar_get("/status")
    except RuntimeError as e:
        log.error("Update sidecar misconfigured: %s", e)
        return JSONResponse(
            _unavailable_state("Update service is not configured (missing UPDATE_SIDECAR_TOKEN)."),
            status_code=503,
        )
    except Exception as e:
        log.warning("Update sidecar unreachable: %s", e)
        return JSONResponse(
            _unavailable_state("Update service unavailable. Is the updater sidecar running?")
        )
    return JSONResponse(data)


@router.get("/backups")
@rate_limit(max_requests=20, window_seconds=60)
async def list_update_backups(request: Request):
    """Pre-update database dumps kept by the sidecar (admin only)."""
    user = getattr(request.state, "current_user", None)
    if not user or getattr(user, "role", "admin") != "admin":
        return JSONResponse({"error": "Admin access required"}, status_code=403)

    try:
        _, data = await _sidecar_get("/backups")
    except RuntimeError as e:
        log.error("Update sidecar misconfigured: %s", e)
        return JSONResponse(
            {"backups": [], "error": "Update service is not configured."}, status_code=503
        )
    except Exception as e:
        log.warning("Update sidecar unreachable: %s", e)
        return JSONResponse({"backups": [], "error": "Update service unavailable."})
    return JSONResponse(data)
