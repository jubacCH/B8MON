"""Update orchestrator: the sequential step runner behind the self-update flow.

The self-update used to be a single fire-and-forget `git pull` plus
`docker compose up -d --build`. Alembic never ran on any deploy path, so every
release carrying a migration silently deployed new code against an old schema —
which is exactly what happened in production and left the correlation engine
crashing every minute for weeks.

This module runs the update as an observable sequence instead: preflight,
backup, pull, build, migrate, restart. The first failing step stops the chain,
and the old containers keep serving unless the failure happens in the restart
step itself.

The module is deliberately free of side effects. Every piece of I/O the steps
need — running a command, streaming a database dump, reading the clock, asking
for free disk space, writing the state file — is injected through :class:`Ctx`,
so the whole state machine is unit-testable without Docker, git or Postgres.

Stdlib only: this file ships inside the sidecar image, which installs no pip
dependencies.
"""
from __future__ import annotations

import json
import os
from collections import namedtuple
from dataclasses import dataclass
from typing import Callable

CmdResult = namedtuple("CmdResult", "returncode stdout stderr")
DumpResult = namedtuple("DumpResult", "returncode size stderr")

STEP_NAMES = ["preflight", "backup", "pull", "build", "migrate", "restart"]

MAX_ERROR_CHARS = 500

EXPECTED_REF = "refs/heads/main"
MIN_FREE_BYTES = 2 * 1024**3
DOCKER_SOCKET = "/var/run/docker.sock"

BACKUP_PREFIX = "pre-update-"
BACKUP_SUFFIX = ".dump.gz"

BUILD_SERVICES = ["nodeglow", "frontend"]
BUILD_TIMEOUT = 1800
MIGRATE_TIMEOUT = 600
RESTART_TIMEOUT = 900


class StepError(Exception):
    """A step failed for an expected, reportable reason."""


@dataclass
class Step:
    name: str
    status: str = "pending"  # pending | running | ok | failed
    detail: str | None = None

    def to_dict(self) -> dict:
        return {"name": self.name, "status": self.status, "detail": self.detail}


@dataclass
class UpdateState:
    run_id: str
    status: str  # running | done | failed
    step: str | None
    steps: list[Step]
    started_at: str
    finished_at: str | None = None
    error: str | None = None

    @classmethod
    def new(cls, run_id: str, step_names: list[str], started_at: str) -> "UpdateState":
        return cls(
            run_id=run_id,
            status="running",
            step=None,
            steps=[Step(name) for name in step_names],
            started_at=started_at,
        )

    def by_name(self, name: str) -> Step:
        for step in self.steps:
            if step.name == name:
                return step
        raise KeyError(name)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "step": self.step,
            "steps": [s.to_dict() for s in self.steps],
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
        }


@dataclass
class Ctx:
    """Everything a step is allowed to touch, injected for testability."""

    run_cmd: Callable[..., CmdResult]
    run_dump: Callable[..., DumpResult]
    now: Callable[[], str]
    disk_free: Callable[[str], int]
    path_exists: Callable[[str], bool]
    log: Callable[[str], None]
    repo_path: str
    compose_file: str
    backup_dir: str
    backup_retention: int
    db_container: str
    db_user: str
    db_name: str
    state_path: str
    run_id: str


def idle_state() -> dict:
    """State reported before the first run of this sidecar's lifetime."""
    return {
        "run_id": None,
        "status": "idle",
        "step": None,
        "steps": [],
        "started_at": None,
        "finished_at": None,
        "error": None,
    }


def write_state(ctx: Ctx, state: UpdateState) -> None:
    """Mirror the state to disk atomically so a crash cannot truncate it."""
    tmp = f"{ctx.state_path}.tmp"
    try:
        with open(tmp, "w") as fh:
            json.dump(state.to_dict(), fh)
        os.replace(tmp, ctx.state_path)
    except OSError as exc:  # never let bookkeeping abort an update
        ctx.log(f"failed to write state file: {exc}")


def run_update(ctx: Ctx, steps=None) -> UpdateState:
    """Run the steps in order, aborting the chain on the first failure."""
    steps = steps if steps is not None else DEFAULT_STEPS
    state = UpdateState.new(ctx.run_id, [name for name, _ in steps], ctx.now())
    write_state(ctx, state)

    for name, fn in steps:
        state.step = name
        step = state.by_name(name)
        step.status = "running"
        write_state(ctx, state)
        ctx.log(f"step {name}: running")
        try:
            step.detail = fn(ctx)
            step.status = "ok"
        except Exception as exc:  # noqa: BLE001 — any failure ends the run
            step.status = "failed"
            state.status = "failed"
            state.error = f"{name}: {str(exc)[-MAX_ERROR_CHARS:]}"
            state.finished_at = ctx.now()
            ctx.log(f"step {name}: FAILED — {state.error}")
            write_state(ctx, state)
            return state
        ctx.log(f"step {name}: ok — {step.detail}")
        write_state(ctx, state)

    state.step = None
    state.status = "done"
    state.finished_at = ctx.now()
    write_state(ctx, state)
    return state


# ── Steps ────────────────────────────────────────────────────────────────────


def _git(ctx: Ctx, *args, timeout: int = 30) -> CmdResult:
    return ctx.run_cmd(["git", *args], timeout=timeout, cwd=ctx.repo_path)


def _compose(ctx: Ctx, *args, timeout: int) -> CmdResult:
    return ctx.run_cmd(
        ["docker", "compose", "-f", ctx.compose_file, *args],
        timeout=timeout,
        cwd=ctx.repo_path,
    )


def step_preflight(ctx: Ctx) -> str:
    """Validate the environment before anything is mutated."""
    if not ctx.path_exists(os.path.join(ctx.repo_path, ".git")):
        raise StepError("Repository not mounted at " + ctx.repo_path)
    if not ctx.path_exists(DOCKER_SOCKET):
        raise StepError("Docker socket not available")

    free = ctx.disk_free(ctx.repo_path)
    if free < MIN_FREE_BYTES:
        raise StepError(
            f"Not enough free disk space: {free / 1024**3:.1f} GB "
            f"(need {MIN_FREE_BYTES / 1024**3:.0f} GB)"
        )

    status = _git(ctx, "status", "--porcelain", timeout=10)
    if status.returncode != 0:
        raise StepError("Unable to read git status")
    if status.stdout.strip():
        raise StepError("Working tree is dirty, refusing to update")

    head = _git(ctx, "symbolic-ref", "-q", "HEAD", timeout=10)
    if head.returncode != 0 or head.stdout.strip() != EXPECTED_REF:
        raise StepError(
            f"HEAD is not on {EXPECTED_REF} (got {head.stdout.strip() or 'detached'})"
        )

    inspect = ctx.run_cmd(
        ["docker", "inspect", "-f", "{{.State.Running}}", ctx.db_container], timeout=15
    )
    if inspect.returncode != 0 or inspect.stdout.strip() != "true":
        raise StepError(f"Database container {ctx.db_container} is not running")

    return (
        f"{free / 1024**3:.1f} GB free, {ctx.db_container} running, "
        f"HEAD on {EXPECTED_REF.rsplit('/', 1)[-1]}"
    )


def list_backups(backup_dir: str) -> list[dict]:
    """List existing dumps, newest first."""
    try:
        names = os.listdir(backup_dir)
    except OSError:
        return []
    entries = []
    for name in names:
        if not (name.startswith(BACKUP_PREFIX) and name.endswith(BACKUP_SUFFIX)):
            continue
        path = os.path.join(backup_dir, name)
        try:
            stat = os.stat(path)
        except OSError:
            continue
        entries.append({"name": name, "size": stat.st_size, "mtime": stat.st_mtime})
    entries.sort(key=lambda e: e["mtime"], reverse=True)
    return entries


def prune_backups(ctx: Ctx) -> int:
    """Delete all but the ``backup_retention`` newest dumps. Returns the count kept."""
    entries = list_backups(ctx.backup_dir)
    for entry in entries[ctx.backup_retention:]:
        try:
            os.remove(os.path.join(ctx.backup_dir, entry["name"]))
            ctx.log(f"pruned old backup {entry['name']}")
        except OSError as exc:
            ctx.log(f"could not prune {entry['name']}: {exc}")
    return min(len(entries), ctx.backup_retention)


def step_backup(ctx: Ctx) -> str:
    """Dump Postgres before any migration touches it."""
    try:
        os.makedirs(ctx.backup_dir, exist_ok=True)
    except OSError as exc:
        raise StepError(f"Backup directory unavailable: {exc}") from exc

    stamp = ctx.now().replace(":", "-")
    name = f"{BACKUP_PREFIX}{stamp}{BACKUP_SUFFIX}"
    dest = os.path.join(ctx.backup_dir, name)

    result = ctx.run_dump(
        ["docker", "exec", ctx.db_container,
         "pg_dump", "-U", ctx.db_user, "-Fc", ctx.db_name],
        dest,
    )
    if result.returncode != 0:
        try:
            os.remove(dest)
        except OSError:
            pass
        raise StepError(f"pg_dump failed: {result.stderr[-MAX_ERROR_CHARS:]}")

    kept = prune_backups(ctx)
    return f"{name} ({result.size / 1024**2:.1f} MB), pruned {kept} kept"


def step_pull(ctx: Ctx) -> str:
    """Fast-forward the repo to origin/main, re-verifying the ref afterwards."""
    before = _git(ctx, "rev-parse", "--short", "HEAD", timeout=10)
    if before.returncode != 0:
        raise StepError("Unable to resolve HEAD before pull")
    old_sha = before.stdout.strip()

    pull = _git(ctx, "pull", "origin", "main", timeout=120)
    if pull.returncode != 0:
        raise StepError(f"git pull failed: {pull.stderr[-MAX_ERROR_CHARS:]}")

    # An update must never be able to fast-forward an unexpected ref into prod.
    head = _git(ctx, "symbolic-ref", "-q", "HEAD", timeout=10)
    if head.returncode != 0 or head.stdout.strip() != EXPECTED_REF:
        raise StepError(
            f"HEAD moved to unexpected ref after pull "
            f"({head.stdout.strip() or 'detached'})"
        )

    after = _git(ctx, "rev-parse", "--short", "HEAD", timeout=10)
    if after.returncode != 0:
        raise StepError("Unable to resolve HEAD after pull")
    new_sha = after.stdout.strip()

    count = _git(ctx, "rev-list", "--count", f"{old_sha}..HEAD", timeout=10)
    n = count.stdout.strip() if count.returncode == 0 else "?"
    return f"{old_sha} → {new_sha} ({n} commits)"


def step_build(ctx: Ctx) -> str:
    """Build the new images without touching the running containers."""
    result = _compose(ctx, "build", *BUILD_SERVICES, timeout=BUILD_TIMEOUT)
    if result.returncode != 0:
        raise StepError(f"docker compose build failed: {result.stderr[-MAX_ERROR_CHARS:]}")
    return f"built {', '.join(BUILD_SERVICES)}"


def step_migrate(ctx: Ctx) -> str:
    """Migrate the database with the new code before it goes live.

    ``compose run --rm`` starts a throwaway container from the image built in
    the previous step, so the schema is current before the live containers are
    replaced. Ports are not published for ``run`` containers, so the syslog
    listeners are not disturbed.
    """
    result = _compose(
        ctx, "run", "--rm", "nodeglow",
        "alembic", "-c", "alembic.ini", "upgrade", "head",
        timeout=MIGRATE_TIMEOUT,
    )
    if result.returncode != 0:
        raise StepError(f"alembic upgrade failed: {result.stderr[-MAX_ERROR_CHARS:]}")
    lines = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
    return lines[-1][-200:] if lines else "schema at head"


def step_restart(ctx: Ctx) -> str:
    """Recreate the app containers. ``--no-deps`` keeps the updater alive."""
    result = _compose(ctx, "up", "-d", "--no-deps", *BUILD_SERVICES,
                      timeout=RESTART_TIMEOUT)
    if result.returncode != 0:
        raise StepError(f"docker compose up failed: {result.stderr[-MAX_ERROR_CHARS:]}")
    return f"{', '.join(BUILD_SERVICES)} restarted"


DEFAULT_STEPS = [
    ("preflight", step_preflight),
    ("backup", step_backup),
    ("pull", step_pull),
    ("build", step_build),
    ("migrate", step_migrate),
    ("restart", step_restart),
]
