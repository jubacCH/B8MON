# Operating Nodeglow

What you need to run this in production: updating, backing up, restoring, and
working out what is wrong when something looks off.

For installation see the Quick start in the [README](../README.md).

---

## Updating

### From the UI

**System → Status → Software Updates → Update Now.**

The update runs as an observable sequence and the page shows each step live:

| Step | What happens | If it fails |
|---|---|---|
| `preflight` | Checks free disk, Docker socket, database container, clean working tree, `HEAD` on `main` | Nothing has been changed yet |
| `backup` | `pg_dump` of the whole database, gzipped, retention 5 | Nothing has been changed yet |
| `pull` | Fast-forwards the repo to `origin/main` | Repo updated, containers untouched — rerunning is safe |
| `build` | Builds the new images | Old containers keep serving |
| `migrate` | Applies migrations using the *new* code, before it goes live | Old containers keep serving; the backup name is shown |
| `restart` | Recreates the application containers | Shown as failed; the backup is available |

**A failed step stops the chain.** Everything except a failure inside `restart`
itself leaves the running installation serving as before.

There is no automatic rollback — the backup taken in step 2 is the recovery
path, deliberately, so that a bad rollback cannot compound a bad update.

### Common preflight failures

**`Working tree is dirty: ...`** — a file was changed or added inside the
installation directory. The message names the files. Editing `docker-compose.yml`
or `.env` in place is the usual cause; `.env` backups are already ignored.
Move the file out of the directory, or commit it if it is a deliberate local
change.

**`HEAD is not on refs/heads/main`** — the checkout is on a branch or detached.
`git checkout main` in the installation directory.

**`Not enough free disk space`** — under 2 GB free. The build needs room for new
images; old ones can be reclaimed with `docker image prune`.

### From the command line

```bash
cd /path/to/nodeglow
git pull --ff-only
docker compose build nodeglow frontend
docker compose run --rm --no-deps nodeglow python migrate.py   # optional: verify first
docker compose up -d --no-deps nodeglow frontend
```

Migrations also run automatically on every container start, so the explicit
`migrate.py` call is only there if you want to see the result before restarting.
`SKIP_MIGRATIONS=1` in the environment bypasses it — for emergencies only, since
running new code against an old schema is what it exists to prevent.

---

## Backups

### What is backed up automatically

Every update takes a full `pg_dump` **before** migrating. The five most recent
are kept in the `backups` Docker volume.

```bash
docker compose exec updater ls -lh /backups/
```

PostgreSQL holds configuration, hosts, rules, incidents and learned patterns —
everything that cannot be recomputed. ClickHouse holds the time series (ping
results, syslog, metrics) and is **not** included: it is reaped by TTL anyway
and would dominate the dump size.

### Taking one manually

```bash
docker compose exec db pg_dump -U nodeglow -Fc nodeglow | gzip > backup.dump.gz
```

### Restoring

```bash
# Stop the application so nothing writes while restoring
docker compose stop nodeglow frontend

gunzip -c backup.dump.gz | docker compose exec -T db \
  pg_restore -U nodeglow -d nodeglow --clean --if-exists

docker compose start nodeglow frontend
```

The container applies any outstanding migrations on start, so restoring an
older dump onto newer code is safe.

---

## When something looks wrong

### Nodeglow tells you first

A `self_check` job runs every five minutes and raises **ordinary incidents**
when Nodeglow stops working properly:

- a data source that is in use stops receiving data
- a scheduled job stops completing successfully

Those appear in the incident list like any other alert. This exists because
silent failure is the worst outcome for a monitoring product: the UI keeps
rendering while nothing is collected. Check the incident list before assuming
the platform is healthy.

### Health at a glance

```bash
docker compose ps                    # all containers should be healthy
curl -s localhost:8000/health        # backend liveness
```

### Are the collectors actually collecting?

```bash
# Scheduler jobs: divide sum by count for the average duration
docker compose exec nodeglow python -c \
  "import urllib.request;print(urllib.request.urlopen('http://localhost:8000/metrics').read().decode())" \
  | grep -E "job_runs_total|job_duration_seconds_sum"
```

A job with only `status="failure"` and no `status="success"` has never worked.
A `last_success_timestamp` far in the past means it has stopped.

### Database load

`pg_stat_statements` is enabled, so the expensive queries are visible:

```bash
docker compose exec db psql -U nodeglow -d nodeglow -c \
  "SELECT round(total_exec_time) ms, calls, left(query,80)
     FROM pg_stat_statements ORDER BY total_exec_time DESC LIMIT 10"
```

Sequential scans on large tables usually mean a missing index:

```bash
docker compose exec db psql -U nodeglow -d nodeglow -c \
  "SELECT relname, seq_scan, seq_tup_read, n_dead_tup,
          pg_size_pretty(pg_total_relation_size(relid)) AS size
     FROM pg_stat_user_tables ORDER BY seq_tup_read DESC LIMIT 10"
```

### Disk usage

ClickHouse keeps its own telemetry under TTL, and Postgres reclaims space
through autovacuum, which is tuned more aggressively on the churn-heavy tables.
If a table is far larger than its live row count suggests, it is bloated from
past deletions and needs a one-off compaction:

```bash
docker compose exec db psql -U nodeglow -d nodeglow -c 'VACUUM FULL <table>'
```

This takes an exclusive lock for the duration — seconds for a small table, but
plan for it on a large one.

### Logs

```bash
docker compose logs nodeglow --tail 200
```

Prefer `--tail` over `--since` on a long-running container: `--since` still
scans the entire log and can take minutes.

---

## Configuration reference

Required in `.env` — the stack refuses to start without them:

| Variable | Purpose |
|---|---|
| `POSTGRES_PASSWORD` | Database password |
| `UPDATE_SIDECAR_TOKEN` | Shared secret between backend and updater. Generate with `openssl rand -hex 32` |

Useful optional settings:

| Variable | Default | Purpose |
|---|---|---|
| `POSTGRES_SHARED_BUFFERS` | `512MB` | Raise on hosts with plenty of RAM |
| `POSTGRES_WORK_MEM` | `8MB` | Per-operation sort/hash memory |
| `BACKUP_RETENTION` | `5` | Pre-update dumps to keep |
| `DB_CONTAINER` | auto | Only needed if the database container cannot be resolved automatically |
| `SKIP_MIGRATIONS` | unset | `1` skips the schema check on start — emergencies only |

Retention is configured in the UI under Settings, not through the environment:
integration snapshots (7 days), incident events (30 days) and log templates
(90 days).

---

## Security notes

**The updater sidecar is host-root-equivalent.** It holds the Docker socket and
can rebuild the stack, so anyone who reaches it with the token can execute code
as root on the host. It is exposed only on the internal Docker network — never
publish its port, and treat `UPDATE_SIDECAR_TOKEN` like a root password.

**Run behind TLS.** The application sets HSTS, CSP and frame-denial headers, but
it does not terminate TLS itself; put a reverse proxy in front of it.
