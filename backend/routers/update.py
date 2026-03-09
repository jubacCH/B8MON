"""Self-update: check GitHub for new commits and apply updates."""
import asyncio
import json
import logging
import os
import subprocess

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/update")
log = logging.getLogger(__name__)

GITHUB_REPO = "jubacCH/Nodeglow"
REPO_PATH = "/opt/repo"
COMPOSE_FILE = f"{REPO_PATH}/docker-compose.yml"


def _get_version_string() -> str:
    """Read VERSION file from mounted repo or fallback."""
    for path in [f"{REPO_PATH}/VERSION", "/app/VERSION"]:
        try:
            with open(path) as f:
                return f.read().strip()
        except Exception:
            pass
    return ""


def _get_local_version() -> dict:
    """Read the build-time version info."""
    version = _get_version_string()
    # Try build-time embedded version first
    try:
        with open("/app/.build-version") as f:
            info = json.load(f)
            if version:
                info["version"] = version
            return info
    except Exception:
        pass
    # Fallback: try git in mounted repo
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=3, cwd=REPO_PATH,
        )
        if result.returncode == 0:
            info = {"commit": result.stdout.strip()[:7], "full_commit": result.stdout.strip()}
            if version:
                info["version"] = version
            return info
    except Exception:
        pass
    # Fallback: try git in /app
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=3, cwd="/app",
        )
        if result.returncode == 0:
            info = {"commit": result.stdout.strip()}
            if version:
                info["version"] = version
            return info
    except Exception:
        pass
    return {"commit": "unknown", "version": version or "unknown"}


def _get_local_full_commit() -> str:
    """Get the full commit hash from the mounted repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=3, cwd=REPO_PATH,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return ""


@router.get("/check")
async def check_for_updates():
    """Compare local commit with GitHub remote. Returns update availability + changelog."""
    local = _get_local_version()
    local_full = _get_local_full_commit()

    # Check if repo mount is available
    repo_available = os.path.isdir(os.path.join(REPO_PATH, ".git"))

    if not repo_available:
        return JSONResponse({
            "local": local,
            "update_available": False,
            "error": "Repository not mounted at /opt/repo. Update checking unavailable.",
        })

    # Fetch latest from remote
    try:
        fetch = subprocess.run(
            ["git", "fetch", "origin", "main", "--quiet"],
            capture_output=True, text=True, timeout=30, cwd=REPO_PATH,
        )
    except Exception as e:
        return JSONResponse({
            "local": local,
            "update_available": False,
            "error": f"Git fetch failed: {e}",
        })

    # Count commits behind
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", "HEAD..origin/main"],
            capture_output=True, text=True, timeout=5, cwd=REPO_PATH,
        )
        commits_behind = int(result.stdout.strip()) if result.returncode == 0 else 0
    except Exception:
        commits_behind = 0

    # Get remote HEAD
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "origin/main"],
            capture_output=True, text=True, timeout=5, cwd=REPO_PATH,
        )
        remote_commit = result.stdout.strip() if result.returncode == 0 else "unknown"
    except Exception:
        remote_commit = "unknown"

    # Get remote VERSION file content
    remote_version = ""
    if commits_behind > 0:
        try:
            result = subprocess.run(
                ["git", "show", "origin/main:VERSION"],
                capture_output=True, text=True, timeout=5, cwd=REPO_PATH,
            )
            if result.returncode == 0:
                remote_version = result.stdout.strip()
        except Exception:
            pass

    # Get changelog (commit messages)
    changelog = []
    if commits_behind > 0:
        try:
            result = subprocess.run(
                ["git", "log", "--oneline", "--no-decorate", f"HEAD..origin/main"],
                capture_output=True, text=True, timeout=5, cwd=REPO_PATH,
            )
            if result.returncode == 0:
                for line in result.stdout.strip().split("\n"):
                    if line.strip():
                        parts = line.split(" ", 1)
                        changelog.append({
                            "hash": parts[0],
                            "message": parts[1] if len(parts) > 1 else "",
                        })
        except Exception:
            pass

    return JSONResponse({
        "local": local,
        "remote_commit": remote_commit,
        "remote_version": remote_version or local.get("version", ""),
        "commits_behind": commits_behind,
        "update_available": commits_behind > 0,
        "changelog": changelog,
    })


@router.post("/apply")
async def apply_update(request: Request):
    """Pull latest code and rebuild the container. Returns immediately — container will restart."""
    # Check admin role
    user = getattr(request.state, "current_user", None)
    if not user or getattr(user, "role", "admin") != "admin":
        return JSONResponse({"error": "Admin access required"}, status_code=403)

    repo_available = os.path.isdir(os.path.join(REPO_PATH, ".git"))
    if not repo_available:
        return JSONResponse({"error": "Repository not mounted"}, status_code=400)

    docker_sock = os.path.exists("/var/run/docker.sock")
    if not docker_sock:
        return JSONResponse({"error": "Docker socket not mounted"}, status_code=400)

    # Step 1: Git pull
    try:
        result = subprocess.run(
            ["git", "pull", "origin", "main"],
            capture_output=True, text=True, timeout=60, cwd=REPO_PATH,
        )
        if result.returncode != 0:
            return JSONResponse({
                "error": f"Git pull failed: {result.stderr.strip()}",
            }, status_code=500)
        pull_output = result.stdout.strip()
    except Exception as e:
        return JSONResponse({"error": f"Git pull error: {e}"}, status_code=500)

    # Step 2: Rebuild and restart (fire-and-forget — container will restart)
    log.info("Update: git pull done (%s). Starting rebuild...", pull_output)

    async def _rebuild():
        """Run docker compose rebuild in background. This will kill the current container."""
        await asyncio.sleep(1)  # Give time for HTTP response to be sent
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "compose", "-f", COMPOSE_FILE,
                "up", "-d", "--build", "--no-deps", "nodeglow",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            # Don't await — this process will kill us
        except Exception as e:
            log.error("Rebuild failed: %s", e)

    asyncio.create_task(_rebuild())

    return JSONResponse({
        "ok": True,
        "message": "Update started. The application will restart in a few seconds.",
        "pull_output": pull_output,
    })
