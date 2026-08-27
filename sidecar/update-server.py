"""Minimal update sidecar — handles git + docker operations with Docker socket access.

Listens on port 9100 (internal only). The main app calls this instead of
accessing the Docker socket directly.

Authentication
--------------
All endpoints except ``/health`` require a bearer token matching the
``UPDATE_SIDECAR_TOKEN`` environment variable (shared with the backend
container). If the variable is unset or empty, the sidecar refuses every
non-health request — fail-closed default so a misconfigured deployment
cannot be driven to pull arbitrary code + rebuild containers.

Update runs are orchestrated by :mod:`orchestrator`: preflight, backup, pull,
build, migrate, restart. One run at a time; a second ``POST /apply`` while a
run is active returns 409.
"""
import gzip
import hmac
import json
import os
import shutil
import subprocess
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

from orchestrator import (
    CmdResult,
    Ctx,
    DumpResult,
    idle_state,
    list_backups,
    run_update,
)

REPO_PATH = os.environ.get("REPO_PATH", "/opt/repo")
COMPOSE_FILE = f"{REPO_PATH}/docker-compose.yml"
STATE_PATH = os.environ.get("STATE_PATH", f"{REPO_PATH}/.update-state.json")
BACKUP_DIR = os.environ.get("BACKUP_DIR", "/backups")
BACKUP_RETENTION = int(os.environ.get("BACKUP_RETENTION", "5"))
DB_USER = os.environ.get("POSTGRES_USER", "nodeglow")
DB_NAME = os.environ.get("POSTGRES_DB", "nodeglow")
DUMP_CHUNK = 1 << 20

# Shared secret with the backend. Must be set in the environment of both
# containers via docker-compose. An empty value means no request can mutate
# anything — this is intentional (fail-closed).
AUTH_TOKEN = os.environ.get("UPDATE_SIDECAR_TOKEN", "").strip()

_run_lock = threading.Lock()
_run_thread = None
_run_state = None  # last known state dict, mirrored from the state file


def _log(msg: str) -> None:
    print(f"[update-sidecar] {msg}", flush=True)


def _run_cmd(argv, timeout=60, cwd=None) -> CmdResult:
    """Run a command to completion, capturing output.

    Always waits: the previous implementation fired `docker compose` off with
    Popen and never reaped it, which is why the sidecar accumulated zombie
    processes in production.
    """
    proc = subprocess.run(
        argv, capture_output=True, text=True, timeout=timeout, cwd=cwd
    )
    return CmdResult(proc.returncode, proc.stdout.strip(), proc.stderr.strip())


def _run_dump(argv, dest, timeout=1800) -> DumpResult:
    """Stream a command's stdout into a gzip file (used for pg_dump)."""
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        with gzip.open(dest, "wb") as out:
            while True:
                chunk = proc.stdout.read(DUMP_CHUNK)
                if not chunk:
                    break
                out.write(chunk)
        _, err = proc.communicate(timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        proc.kill()
        proc.communicate()
        return DumpResult(1, 0, str(exc))
    size = os.path.getsize(dest) if os.path.exists(dest) else 0
    return DumpResult(proc.returncode, size, (err or b"").decode(errors="replace").strip())


def _resolve_compose_project() -> str:
    """Determine the compose project the running stack belongs to.

    Compose derives the project name from the directory, but the sidecar sees
    the repo at /opt/repo while production started the stack from /opt/vigil.
    Guessing wrong makes compose treat the live containers as foreign. The
    authoritative answer is the label on a container that is actually running.
    """
    explicit = os.environ.get("COMPOSE_PROJECT_NAME", "").strip()
    if explicit:
        return explicit
    try:
        result = _run_cmd(
            ["docker", "inspect", "nodeglow", "--format",
             "{{index .Config.Labels \"com.docker.compose.project\"}}"],
            timeout=15,
        )
        name = result.stdout.strip()
        if name:
            return name
    except Exception as exc:  # noqa: BLE001
        _log(f"could not resolve compose project from the running container: {exc}")
    # Fall back to compose's own default: the directory the file lives in.
    return os.path.basename(REPO_PATH.rstrip("/")) or "nodeglow"


def _resolve_db_container() -> str:
    """Prefer an explicit DB_CONTAINER, else ask compose, else the prod default."""
    explicit = os.environ.get("DB_CONTAINER", "").strip()
    if explicit:
        return explicit
    try:
        result = _run_cmd(
            ["docker", "compose", "-f", COMPOSE_FILE, "ps", "-q", "db"],
            timeout=15, cwd=REPO_PATH,
        )
        lines = [ln for ln in result.stdout.strip().splitlines() if ln.strip()]
        if lines:
            return lines[0]
    except Exception as exc:  # noqa: BLE001
        _log(f"could not resolve db container via compose: {exc}")
    return "vigil-db-1"


def build_ctx(run_id: str) -> Ctx:
    """Assemble the orchestrator context with real I/O."""
    return Ctx(
        run_cmd=_run_cmd,
        run_dump=_run_dump,
        now=lambda: time.strftime("%Y-%m-%dT%H:%M:%S"),
        disk_free=lambda path: shutil.disk_usage(path).free,
        path_exists=os.path.exists,
        log=_log,
        repo_path=REPO_PATH,
        compose_file=COMPOSE_FILE,
        compose_project=_resolve_compose_project(),
        backup_dir=BACKUP_DIR,
        backup_retention=BACKUP_RETENTION,
        db_container=_resolve_db_container(),
        db_user=DB_USER,
        db_name=DB_NAME,
        state_path=STATE_PATH,
        run_id=run_id,
    )


def current_status() -> dict:
    """Return the live run state, falling back to the state file, then idle."""
    try:
        with open(STATE_PATH) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        pass
    if _run_state is not None:
        return _run_state
    return idle_state()


# SECURITY: This endpoint is host-root-equivalent. It runs `git pull` and
# `docker compose build/up` against the host Docker socket, so anyone who can
# reach it and presents the token can execute arbitrary code as root on the
# host (e.g. by landing a malicious compose/Dockerfile in the repo). It is
# reachable only over the internal docker network (port is `expose`d, not
# host-published) and MUST NEVER be host-published or exposed publicly.
def start_run(runner=run_update):
    """Start an update run in a worker thread. 409 if one is already active."""
    global _run_thread, _run_state

    with _run_lock:
        if _run_thread is not None and _run_thread.is_alive():
            return 409, {"ok": False, "error": "An update run is already active"}

        run_id = time.strftime("%Y-%m-%dT%H-%M-%S")
        ctx = build_ctx(run_id)
        _run_state = None

        def worker():
            global _run_state
            try:
                state = runner(ctx)
                _run_state = state.to_dict() if state is not None else None
            except Exception as exc:  # noqa: BLE001 — never die silently
                _log(f"update run crashed: {exc}")

        _run_thread = threading.Thread(target=worker, name=f"update-{run_id}", daemon=True)
        _run_thread.start()
        _log(f"update run {run_id} started")
        return 202, {"ok": True, "run_id": run_id}


class UpdateHandler(BaseHTTPRequestHandler):
    """Simple HTTP handler for update operations."""

    # Paths that never require authentication (for compose healthcheck + liveness).
    PUBLIC_PATHS = {"/health"}

    def _authorized(self) -> bool:
        """Check Bearer token using constant-time comparison."""
        if self.path in self.PUBLIC_PATHS:
            return True
        if not AUTH_TOKEN:
            return False
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return False
        provided = header[len("Bearer "):].strip()
        return hmac.compare_digest(provided, AUTH_TOKEN)

    def _reject_unauthorized(self):
        self._json(401, {"error": "Unauthorized"})

    def do_GET(self):
        if not self._authorized():
            return self._reject_unauthorized()
        if self.path == "/health":
            self._json(200, {"status": "ok"})
        elif self.path == "/version":
            self._json(200, self._get_version())
        elif self.path == "/check":
            self._json(200, self._check_updates())
        elif self.path == "/status":
            self._json(200, current_status())
        elif self.path == "/backups":
            self._json(200, {"backups": list_backups(BACKUP_DIR)})
        else:
            self._json(404, {"error": "Not found"})

    def do_POST(self):
        if not self._authorized():
            return self._reject_unauthorized()
        if self.path == "/apply":
            code, payload = start_run()
            self._json(code, payload)
        else:
            self._json(404, {"error": "Not found"})

    def _json(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(f"[update-sidecar] {fmt % args}")

    def _get_version(self):
        try:
            r = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, timeout=5, cwd=REPO_PATH,
            )
            commit = r.stdout.strip() if r.returncode == 0 else "unknown"
        except Exception:
            commit = "unknown"
        version = ""
        try:
            with open(f"{REPO_PATH}/VERSION") as f:
                version = f.read().strip()
        except Exception:
            pass
        return {"commit": commit, "version": version}

    def _check_updates(self):
        if not os.path.isdir(f"{REPO_PATH}/.git"):
            return {"error": "Repository not mounted", "update_available": False}
        try:
            subprocess.run(
                ["git", "fetch", "origin", "main", "--quiet"],
                capture_output=True, text=True, timeout=30, cwd=REPO_PATH,
            )
        except Exception:
            return {"error": "Git fetch failed", "update_available": False}
        try:
            r = subprocess.run(
                ["git", "rev-list", "--count", "HEAD..origin/main"],
                capture_output=True, text=True, timeout=5, cwd=REPO_PATH,
            )
            behind = int(r.stdout.strip()) if r.returncode == 0 else 0
        except Exception:
            behind = 0
        changelog = []
        if behind > 0:
            try:
                r = subprocess.run(
                    ["git", "log", "--oneline", "--no-decorate", "HEAD..origin/main"],
                    capture_output=True, text=True, timeout=5, cwd=REPO_PATH,
                )
                if r.returncode == 0:
                    for line in r.stdout.strip().split("\n"):
                        if line.strip():
                            parts = line.split(" ", 1)
                            changelog.append({"hash": parts[0], "message": parts[1] if len(parts) > 1 else ""})
            except Exception:
                pass
        return {"update_available": behind > 0, "commits_behind": behind, "changelog": changelog}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "9100"))
    if not AUTH_TOKEN:
        print("[update-sidecar] WARNING: UPDATE_SIDECAR_TOKEN is empty — "
              "all mutating endpoints will return 401 until it is set.")
    server = HTTPServer(("0.0.0.0", port), UpdateHandler)
    print(f"[update-sidecar] listening on :{port}")
    server.serve_forever()
