# Update Orchestrator — Design

**Date:** 2026-06-10
**Status:** Implemented and verified in production 2026-08-27

## Problem

The self-update feature (sidecar `POST /apply`) runs `git pull` + `docker compose up
-d --build` and nothing else. Alembic migrations never run on any deploy path —
neither in the sidecar nor in the container entrypoint. Every release that ships a
migration silently deploys new code against an old schema (documented schema-drift
incident on 2026-04-29). There is also no backup, no pre-flight validation, and no
visibility into what the update is doing.

## Goals

- One-click update: preflight → backup → pull → build → migrate → restart, fully
  autonomous, with live step-by-step progress in the UI.
- Postgres backup (`pg_dump`) before every migration, kept with retention 5,
  manually restorable.
- Pre-flight checks abort the run before anything is mutated.
- A failed step stops the chain; nothing half-deployed. The old containers keep
  running unless the failure happens in the final restart step itself.
- Migrations also run as a safety net on every backend container start, covering
  manual deploys (`git pull` + `compose up` by hand).

## Non-Goals

- No automatic rollback (failed health-check → previous image). Deliberately
  deferred; the backup is the manual escape hatch.
- No ClickHouse backup. Alembic only touches Postgres; ClickHouse schemas are
  created idempotently by the app (`_ensure_schemas`) and reaped by TTL.
- No agent updates (separate, already-signed mechanism).

## Architecture

The **sidecar** is the orchestrator. It is the only component that survives the
update (it is rebuilt with `--no-deps nodeglow frontend`, so the updater container
keeps running) and it already has the Docker socket and the repo mount.

### Sidecar state machine (`sidecar/update-server.py`)

State lives in memory and is mirrored to `/opt/repo/.update-state.json` (crash
recovery; the file is in `.gitignore` so the working tree stays clean for the
dirty-check). Shape:

```json
{
  "run_id": "2026-06-10T14-02-11",
  "step": "migrate",
  "steps": [
    {"name": "preflight", "status": "ok",      "detail": "disk 12.4GB free"},
    {"name": "backup",    "status": "ok",      "detail": "pre-update-2026-06-10T14-02.dump.gz (4.2MB)"},
    {"name": "pull",      "status": "ok",      "detail": "2174518 → a1b2c3d (3 commits)"},
    {"name": "build",     "status": "running", "detail": null},
    {"name": "migrate",   "status": "pending", "detail": null},
    {"name": "restart",   "status": "pending", "detail": null}
  ],
  "started_at": "...", "finished_at": null, "error": null
}
```

Endpoints (all bearer-token-authenticated like today):

- `POST /apply` — starts a worker thread running the steps sequentially.
  Returns 409 if a run is already active.
- `GET /status` — returns the state object (also after completion; cleared on
  next `/apply`).
- `GET /backups` — lists files in the backup directory (name, size, mtime).

Steps (each `subprocess` call with timeout; any non-zero exit fails the run with
the step name + last 500 chars of stderr in `error`):

| Step | Action | Failure consequence |
|---|---|---|
| `preflight` | disk ≥ 2 GB free on the Docker volume, Docker socket present, DB container running, working tree clean, HEAD on `refs/heads/main` (existing checks reused) | nothing mutated |
| `backup` | `docker exec <db> pg_dump -U nodeglow -Fc nodeglow`, gzipped to `/backups/pre-update-<ts>.dump.gz`; then prune to the 5 newest | nothing mutated |
| `pull` | `git pull origin main` + post-pull ref verification (existing logic) | repo updated, containers untouched — rerun is safe |
| `build` | `docker compose build nodeglow frontend` (no up) | old containers keep running |
| `migrate` | `docker compose run --rm nodeglow alembic -c alembic.ini upgrade head` — new code migrates the DB **before** the new container goes live | old containers keep running; backup path shown in UI |
| `restart` | `docker compose up -d --no-deps nodeglow frontend` | shown as failed; backup available |

The DB container name comes from `DB_CONTAINER` env (default `vigil-db-1`) so the
legacy prod naming is config, not code.

### Backend (`routers/update.py`)

- `GET /api/update/status` → proxies sidecar `/status` (admin not required for
  reading; same auth model as `/check`).
- `GET /api/update/backups` → proxies sidecar `/backups` (admin only).
- `POST /api/update/apply` unchanged (admin only), response now just confirms the
  run started.

### Entrypoint safety net (`backend/entrypoint.sh`)

Before `exec gosu nodeglow "$@"`: run `alembic -c alembic.ini upgrade head` as the
nodeglow user; on failure, exit non-zero (better a restart loop with a clear log
line than a running app on a drifted schema). This makes the sidecar `migrate`
step idempotent and covers manual deploys. Skippable via `SKIP_MIGRATIONS=1` for
emergencies.

### Frontend (System → Update)

While a run is active, poll `GET /api/update/status` every 2 s and render the step
list (check / spinner / error per step, detail line underneath). During the
`restart` step the backend itself goes away — polling errors while the last known
step is `restart` render as "backend restarting…" and polling continues until the
new backend answers. Terminal states: success banner with new commit, or error
banner with failed step, stderr excerpt, and the backup filename.

### Compose / infra

- New named volume `backups`, mounted into the sidecar at `/backups`.
- `pg_dump` output is streamed from `docker exec` stdout, so the DB container
  needs no mount.
- `.update-state.json` added to `.gitignore`.

## Error handling summary

- Any failed step → chain stops, status `failed`, old stack keeps serving
  (except a failure inside `restart` itself).
- Sidecar crash mid-run → state file shows the last step after the sidecar
  container restarts; no auto-resume (operator decides).
- Concurrent `/apply` → 409.
- Migration failure leaves the DB at whatever Alembic reached — Alembic runs each
  migration in a transaction on Postgres, and the pre-migration dump is the
  recovery path.

## Testing

- **Sidecar:** extract the step-runner into pure functions taking a `run_cmd`
  callable; unit-test the sequence, every abort point, state-file writing, 409
  behavior, and backup retention pruning with mocked subprocess calls.
- **Backend:** proxy endpoint tests with a mocked sidecar (httpx mock), auth
  checks (admin vs. not).
- **Entrypoint:** CI smoke step — run `alembic upgrade head` against an empty
  Postgres service container, assert exit 0; assert `SKIP_MIGRATIONS=1` skips.
- **Frontend:** status-rendering component test for the three terminal shapes
  (running, failed-with-backup, done) plus the restart-gap behavior.


---

## Implementation notes (2026-08-27)

Implemented across PRs #17 (orchestrator + entrypoint), #19 (progress UI),
#24 and #25 (fixes found by the first production run).

### Deviations from the design above

- The state object carries an explicit top-level `status` (`idle` | `running` |
  `done` | `failed` | `unavailable`) alongside `error`/`finished_at`, so the UI
  can detect terminal states instead of inferring them.
- `DB_CONTAINER` is honoured when set; when empty the sidecar resolves the
  container via `docker compose ps -q db` before falling back to the legacy
  production name `vigil-db-1`.
- Migrations run through `migrate.py` rather than `alembic upgrade head`
  directly. The migration chain grew alongside `create_all()`, so a fresh
  database cannot be built by alembic alone; `migrate.py` distinguishes fresh
  installs (create schema from the models, then stamp) from existing ones
  (apply outstanding migrations). See PR #20.

### What the first production run exposed

Both defects would have hit every installation and were invisible until the
chain actually ran end to end:

1. **`.env` backups blocked updates permanently.** Any file left in the working
   tree failed the clean-tree preflight, and the message said only "Working tree
   is dirty" without naming it. Backing up `.env` before editing is a normal
   admin reflex. Fixed in #24: such backups are gitignored and the preflight
   names the offending files.

2. **Compose ran under the wrong project.** The sidecar sees the repo at
   `/opt/repo`, while production started the stack from `/opt/vigil`. Compose
   derives the project name from the directory, so it treated the running
   containers as foreign: it built images under a `repo-` prefix and then failed
   trying to create a container whose name was already taken. Had `migrate`
   succeeded, `restart` would have hit the same conflict. Fixed in #25: every
   compose call passes `-p`, resolved from the running container's
   `com.docker.compose.project` label.

### Verified behaviour

A failing step stops the chain and leaves the running containers untouched —
observed directly when the first run failed at `migrate`: `restart` never ran,
the stack kept serving, and the pre-migration dump was on disk.

Successful run, second attempt:

```
preflight -> ok | 34.7 GB free, vigil-db-1 running, HEAD on main
backup    -> ok | pre-update-2026-08-27T09-52-59.dump.gz (241.8 MB), pruned 2 kept
pull      -> ok | 7db12b9 -> 7db12b9 (0 commits)
build     -> ok | built nodeglow, frontend
migrate   -> ok | schema up to date
restart   -> ok | nodeglow, frontend restarted
```
