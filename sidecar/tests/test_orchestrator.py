"""Unit tests for the update orchestrator step runner.

Every step is exercised through injected I/O, so none of this needs Docker,
git or Postgres.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orchestrator import (  # noqa: E402
    CmdResult,
    Ctx,
    DumpResult,
    DEFAULT_STEPS,
    STEP_NAMES,
    StepError,
    UpdateState,
    idle_state,
    list_backups,
    prune_backups,
    run_update,
    step_backup,
    step_build,
    step_migrate,
    step_preflight,
    step_pull,
    step_restart,
)


def make_ctx(tmp_path, **overrides):
    """Build a Ctx whose I/O is fully faked; override single fields per test."""
    defaults = dict(
        run_cmd=lambda argv, timeout=60, cwd=None: (_ for _ in ()).throw(
            AssertionError(f"unexpected command: {argv}")
        ),
        run_dump=lambda argv, dest, timeout=1800: (_ for _ in ()).throw(
            AssertionError("unexpected dump")
        ),
        now=lambda: "2026-06-10T14:02:11",
        disk_free=lambda path: 20 * 1024**3,
        path_exists=lambda path: True,
        log=lambda msg: None,
        repo_path=str(tmp_path / "repo"),
        compose_file=str(tmp_path / "repo" / "docker-compose.yml"),
        backup_dir=str(tmp_path / "backups"),
        backup_retention=5,
        db_container="vigil-db-1",
        db_user="nodeglow",
        db_name="nodeglow",
        state_path=str(tmp_path / "state.json"),
        run_id="2026-06-10T14-02-11",
    )
    defaults.update(overrides)
    os.makedirs(defaults["repo_path"], exist_ok=True)
    os.makedirs(defaults["backup_dir"], exist_ok=True)
    return Ctx(**defaults)


# ── Runner ───────────────────────────────────────────────────────────────────

def test_all_steps_ok_marks_run_done(tmp_path):
    calls = []
    steps = [(name, lambda ctx, n=name: calls.append(n) or f"{n} detail")
             for name in ("alpha", "beta")]

    state = run_update(make_ctx(tmp_path), steps=steps)

    assert calls == ["alpha", "beta"]
    assert state.status == "done"
    assert state.step is None
    assert state.error is None
    assert [(s.name, s.status) for s in state.steps] == [("alpha", "ok"), ("beta", "ok")]


def test_failed_step_stops_the_chain(tmp_path):
    calls = []

    def boom(ctx):
        raise StepError("disk is full")

    steps = [
        ("alpha", lambda ctx: calls.append("alpha") or "ok"),
        ("beta", boom),
        ("gamma", lambda ctx: calls.append("gamma") or "ok"),
    ]

    state = run_update(make_ctx(tmp_path), steps=steps)

    assert calls == ["alpha"]  # gamma never ran
    assert state.status == "failed"
    assert state.error == "beta: disk is full"
    assert [s.status for s in state.steps] == ["ok", "failed", "pending"]


def test_unexpected_exception_is_captured_not_raised(tmp_path):
    def boom(ctx):
        raise ValueError("something odd")

    state = run_update(make_ctx(tmp_path), steps=[("alpha", boom)])

    assert state.status == "failed"
    assert "something odd" in state.error


def test_error_message_is_truncated(tmp_path):
    def boom(ctx):
        raise StepError("x" * 2000)

    state = run_update(make_ctx(tmp_path), steps=[("alpha", boom)])
    assert len(state.error) <= 500 + len("alpha: ")


def test_state_file_is_written_on_every_transition(tmp_path):
    seen = []
    ctx = make_ctx(tmp_path)

    def record(ctx_arg):
        with open(ctx.state_path) as fh:
            seen.append(json.load(fh))
        return "ok"

    run_update(ctx, steps=[("alpha", record)])

    assert seen[0]["step"] == "alpha"
    assert seen[0]["steps"][0]["status"] == "running"
    with open(ctx.state_path) as fh:
        final = json.load(fh)
    assert final["status"] == "done"
    assert final["run_id"] == "2026-06-10T14-02-11"


def test_idle_state_shape():
    data = idle_state()
    assert data["run_id"] is None
    assert data["status"] == "idle"
    assert data["steps"] == []


def test_default_steps_match_documented_order():
    assert [name for name, _ in DEFAULT_STEPS] == STEP_NAMES


# ── Preflight ────────────────────────────────────────────────────────────────

def _git_responder(overrides=None):
    answers = {
        ("git", "status", "--porcelain"): CmdResult(0, "", ""),
        ("git", "symbolic-ref", "-q", "HEAD"): CmdResult(0, "refs/heads/main", ""),
        ("docker", "inspect", "-f", "{{.State.Running}}", "vigil-db-1"): CmdResult(0, "true", ""),
    }
    answers.update(overrides or {})

    def run_cmd(argv, timeout=60, cwd=None):
        key = tuple(argv)
        if key not in answers:
            raise AssertionError(f"unexpected command: {argv}")
        return answers[key]

    return run_cmd


def test_preflight_passes(tmp_path):
    detail = step_preflight(make_ctx(tmp_path, run_cmd=_git_responder()))
    assert "20.0 GB free" in detail
    assert "vigil-db-1 running" in detail


def test_preflight_rejects_low_disk(tmp_path):
    ctx = make_ctx(tmp_path, run_cmd=_git_responder(), disk_free=lambda p: 1024**3)
    with pytest.raises(StepError, match="Not enough free disk space"):
        step_preflight(ctx)


def test_preflight_requires_docker_socket(tmp_path):
    ctx = make_ctx(tmp_path, run_cmd=_git_responder(),
                   path_exists=lambda p: p != "/var/run/docker.sock")
    with pytest.raises(StepError, match="Docker socket"):
        step_preflight(ctx)


def test_preflight_rejects_dirty_tree(tmp_path):
    ctx = make_ctx(tmp_path, run_cmd=_git_responder({
        ("git", "status", "--porcelain"): CmdResult(0, " M backend/main.py", ""),
    }))
    with pytest.raises(StepError, match="dirty"):
        step_preflight(ctx)


def test_preflight_rejects_foreign_head(tmp_path):
    ctx = make_ctx(tmp_path, run_cmd=_git_responder({
        ("git", "symbolic-ref", "-q", "HEAD"): CmdResult(1, "", ""),
    }))
    with pytest.raises(StepError, match="refs/heads/main"):
        step_preflight(ctx)


def test_preflight_rejects_stopped_database(tmp_path):
    ctx = make_ctx(tmp_path, run_cmd=_git_responder({
        ("docker", "inspect", "-f", "{{.State.Running}}", "vigil-db-1"): CmdResult(1, "", "no such object"),
    }))
    with pytest.raises(StepError, match="vigil-db-1"):
        step_preflight(ctx)


# ── Backup ───────────────────────────────────────────────────────────────────

def _fake_dump(size=4_200_000):
    def run_dump(argv, dest, timeout=1800):
        with open(dest, "wb") as fh:
            fh.write(b"\0" * size)
        return DumpResult(0, size, "")
    return run_dump


def test_backup_writes_dump_and_reports_size(tmp_path):
    seen = {}

    def run_dump(argv, dest, timeout=1800):
        seen["argv"] = argv
        seen["dest"] = dest
        with open(dest, "wb") as fh:
            fh.write(b"\0" * 4_200_000)
        return DumpResult(0, 4_200_000, "")

    ctx = make_ctx(tmp_path, run_dump=run_dump)
    detail = step_backup(ctx)

    assert seen["argv"] == [
        "docker", "exec", "vigil-db-1",
        "pg_dump", "-U", "nodeglow", "-Fc", "nodeglow",
    ]
    assert seen["dest"].endswith("pre-update-2026-06-10T14-02-11.dump.gz")
    assert "4.0 MB" in detail
    assert os.path.exists(seen["dest"])


def test_backup_failure_removes_partial_file(tmp_path):
    def run_dump(argv, dest, timeout=1800):
        with open(dest, "wb") as fh:
            fh.write(b"partial")
        return DumpResult(1, 7, "FATAL: role does not exist")

    ctx = make_ctx(tmp_path, run_dump=run_dump)
    with pytest.raises(StepError, match="role does not exist"):
        step_backup(ctx)
    assert os.listdir(ctx.backup_dir) == []


def test_backup_prunes_to_retention(tmp_path):
    ctx = make_ctx(tmp_path, run_dump=_fake_dump(), backup_retention=3)
    for i, name in enumerate(["a", "b", "c", "d", "e"]):
        path = os.path.join(ctx.backup_dir, f"pre-update-{name}.dump.gz")
        with open(path, "wb") as fh:
            fh.write(b"x")
        os.utime(path, (1000 + i, 1000 + i))

    step_backup(ctx)

    remaining = sorted(os.listdir(ctx.backup_dir))
    assert len(remaining) == 3
    assert "pre-update-2026-06-10T14-02-11.dump.gz" in remaining
    assert "pre-update-a.dump.gz" not in remaining


def test_prune_ignores_unrelated_files(tmp_path):
    ctx = make_ctx(tmp_path, backup_retention=1)
    for name in ("pre-update-a.dump.gz", "pre-update-b.dump.gz", "README.txt"):
        with open(os.path.join(ctx.backup_dir, name), "wb") as fh:
            fh.write(b"x")
    prune_backups(ctx)
    assert "README.txt" in os.listdir(ctx.backup_dir)


def test_list_backups_newest_first(tmp_path):
    ctx = make_ctx(tmp_path)
    for i, name in enumerate(["old", "new"]):
        path = os.path.join(ctx.backup_dir, f"pre-update-{name}.dump.gz")
        with open(path, "wb") as fh:
            fh.write(b"x" * (i + 1))
        os.utime(path, (1000 + i, 1000 + i))

    entries = list_backups(ctx.backup_dir)
    assert [e["name"] for e in entries] == ["pre-update-new.dump.gz", "pre-update-old.dump.gz"]
    assert entries[0]["size"] == 2


def test_list_backups_missing_dir_is_empty(tmp_path):
    assert list_backups(str(tmp_path / "nope")) == []


# ── Pull / build / migrate / restart ─────────────────────────────────────────

def _responder(answers):
    def run_cmd(argv, timeout=60, cwd=None):
        key = tuple(argv)
        if key not in answers:
            raise AssertionError(f"unexpected command: {argv}")
        return answers[key]
    return run_cmd


def test_pull_reports_commit_range(tmp_path):
    calls = []

    def run_cmd(argv, timeout=60, cwd=None):
        key = tuple(argv)
        calls.append(key)
        if key == ("git", "rev-parse", "--short", "HEAD"):
            nth = len([c for c in calls if c == key])
            return CmdResult(0, "2174518" if nth == 1 else "a1b2c3d", "")
        return {
            ("git", "pull", "origin", "main"): CmdResult(0, "Fast-forward", ""),
            ("git", "symbolic-ref", "-q", "HEAD"): CmdResult(0, "refs/heads/main", ""),
            ("git", "rev-list", "--count", "2174518..HEAD"): CmdResult(0, "3", ""),
        }[key]

    assert step_pull(make_ctx(tmp_path, run_cmd=run_cmd)) == "2174518 → a1b2c3d (3 commits)"


def test_pull_failure_surfaces_stderr(tmp_path):
    ctx = make_ctx(tmp_path, run_cmd=_responder({
        ("git", "rev-parse", "--short", "HEAD"): CmdResult(0, "2174518", ""),
        ("git", "pull", "origin", "main"): CmdResult(1, "", "fatal: could not read from remote"),
    }))
    with pytest.raises(StepError, match="could not read from remote"):
        step_pull(ctx)


def test_pull_rejects_head_moving_to_foreign_ref(tmp_path):
    ctx = make_ctx(tmp_path, run_cmd=_responder({
        ("git", "rev-parse", "--short", "HEAD"): CmdResult(0, "2174518", ""),
        ("git", "pull", "origin", "main"): CmdResult(0, "Fast-forward", ""),
        ("git", "symbolic-ref", "-q", "HEAD"): CmdResult(0, "refs/heads/evil", ""),
    }))
    with pytest.raises(StepError, match="unexpected ref"):
        step_pull(ctx)


def test_build_only_builds_app_services(tmp_path):
    seen = {}

    def run_cmd(argv, timeout=60, cwd=None):
        seen["argv"] = argv
        seen["timeout"] = timeout
        return CmdResult(0, "", "")

    ctx = make_ctx(tmp_path, run_cmd=run_cmd)
    detail = step_build(ctx)

    assert seen["argv"] == ["docker", "compose", "-f", ctx.compose_file,
                            "build", "nodeglow", "frontend"]
    assert seen["timeout"] >= 1800
    assert "nodeglow" in detail


def test_build_failure_is_reported(tmp_path):
    ctx = make_ctx(tmp_path, run_cmd=lambda argv, timeout=60, cwd=None: CmdResult(
        1, "", "ERROR: failed to solve"))
    with pytest.raises(StepError, match="failed to solve"):
        step_build(ctx)


def test_migrate_runs_alembic_in_throwaway_container(tmp_path):
    seen = {}

    def run_cmd(argv, timeout=60, cwd=None):
        seen["argv"] = argv
        return CmdResult(0, "INFO  [alembic] Running upgrade 031 -> 032, add x", "")

    ctx = make_ctx(tmp_path, run_cmd=run_cmd)
    detail = step_migrate(ctx)

    assert seen["argv"] == ["docker", "compose", "-f", ctx.compose_file, "run", "--rm",
                            "nodeglow", "alembic", "-c", "alembic.ini", "upgrade", "head"]
    assert "031 -> 032" in detail


def test_migrate_without_output_still_succeeds(tmp_path):
    ctx = make_ctx(tmp_path, run_cmd=lambda argv, timeout=60, cwd=None: CmdResult(0, "", ""))
    assert step_migrate(ctx) == "schema at head"


def test_migrate_failure_is_reported(tmp_path):
    ctx = make_ctx(tmp_path, run_cmd=lambda argv, timeout=60, cwd=None: CmdResult(
        1, "", "DuplicateColumn: column already exists"))
    with pytest.raises(StepError, match="column already exists"):
        step_migrate(ctx)


def test_restart_leaves_updater_alone(tmp_path):
    seen = {}

    def run_cmd(argv, timeout=60, cwd=None):
        seen["argv"] = argv
        return CmdResult(0, "", "")

    ctx = make_ctx(tmp_path, run_cmd=run_cmd)
    detail = step_restart(ctx)

    assert seen["argv"] == ["docker", "compose", "-f", ctx.compose_file,
                            "up", "-d", "--no-deps", "nodeglow", "frontend"]
    assert "updater" not in seen["argv"]
    assert "restarted" in detail
