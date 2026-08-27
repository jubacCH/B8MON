"""Tests for the sidecar HTTP layer: run lifecycle, 409 guard, status, backups."""
import importlib.util
import json
import os
import sys
import threading
import time

SIDECAR_DIR = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, SIDECAR_DIR)


def load_server(monkeypatch, tmp_path):
    """Import update-server.py fresh with temp paths."""
    monkeypatch.setenv("UPDATE_SIDECAR_TOKEN", "test-token")
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setenv("REPO_PATH", str(tmp_path / "repo"))
    monkeypatch.setenv("DB_CONTAINER", "vigil-db-1")
    os.makedirs(tmp_path / "backups", exist_ok=True)
    os.makedirs(tmp_path / "repo", exist_ok=True)

    spec = importlib.util.spec_from_file_location(
        "update_server", os.path.join(SIDECAR_DIR, "update-server.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_status_is_idle_before_any_run(monkeypatch, tmp_path):
    server = load_server(monkeypatch, tmp_path)
    status = server.current_status()
    assert status["run_id"] is None
    assert status["status"] == "idle"


def test_start_run_launches_runner_and_returns_202(monkeypatch, tmp_path):
    server = load_server(monkeypatch, tmp_path)
    started = threading.Event()

    def fake_runner(ctx, steps=None):
        started.set()
        return None

    code, payload = server.start_run(runner=fake_runner)

    assert code == 202
    assert payload["ok"] is True
    assert payload["run_id"]
    assert started.wait(timeout=5)


def test_second_start_while_running_returns_409(monkeypatch, tmp_path):
    server = load_server(monkeypatch, tmp_path)
    release = threading.Event()

    def slow_runner(ctx, steps=None):
        release.wait(timeout=5)
        return None

    first_code, _ = server.start_run(runner=slow_runner)
    time.sleep(0.1)
    second_code, second_payload = server.start_run(runner=slow_runner)
    release.set()

    assert first_code == 202
    assert second_code == 409
    assert "already" in second_payload["error"].lower()


def test_status_reflects_state_written_by_run(monkeypatch, tmp_path):
    server = load_server(monkeypatch, tmp_path)

    def runner(ctx, steps=None):
        from orchestrator import run_update
        return run_update(ctx, steps=[("alpha", lambda c: "done")])

    server.start_run(runner=runner)
    for _ in range(50):
        if server.current_status().get("status") == "done":
            break
        time.sleep(0.05)

    status = server.current_status()
    assert status["status"] == "done"
    assert status["steps"][0]["name"] == "alpha"


def test_status_recovers_from_state_file_after_restart(monkeypatch, tmp_path):
    (tmp_path / "state.json").write_text(json.dumps({
        "run_id": "r1", "status": "running", "step": "migrate",
        "steps": [{"name": "migrate", "status": "running", "detail": None}],
        "started_at": "x", "finished_at": None, "error": None,
    }))
    server = load_server(monkeypatch, tmp_path)

    status = server.current_status()
    assert status["run_id"] == "r1"
    assert status["step"] == "migrate"


def test_build_ctx_reads_settings_from_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("POSTGRES_USER", "nodeglow")
    monkeypatch.setenv("POSTGRES_DB", "nodeglow")
    monkeypatch.setenv("BACKUP_RETENTION", "7")
    server = load_server(monkeypatch, tmp_path)

    ctx = server.build_ctx("run-1")

    assert ctx.db_container == "vigil-db-1"
    # Falls back to the repo directory name when nothing else is available.
    assert ctx.compose_project
    assert ctx.db_user == "nodeglow"
    assert ctx.backup_retention == 7
    assert ctx.run_id == "run-1"
