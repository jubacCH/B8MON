#!/bin/sh
# Fix ownership of the mounted /data volume so the nodeglow user can read/write
# it, apply pending database migrations, then drop privileges and start the app.
#
# The migration step is the safety net for deploy paths the update sidecar does
# not own — a manual `git pull` plus `compose up`, or a plain restart. Without
# it, new code can come up against an old schema, which is exactly how the
# correlation engine ended up crashing every minute for weeks in production.
set -e

mkdir -p /data/geoip
chown -R nodeglow:nodeglow /data 2>/dev/null || true

if [ "$SKIP_MIGRATIONS" = "1" ]; then
    echo "[entrypoint] SKIP_MIGRATIONS=1 — skipping alembic upgrade"
else
    echo "[entrypoint] applying database migrations"
    if ! gosu nodeglow alembic -c alembic.ini upgrade head; then
        echo "[entrypoint] alembic upgrade failed — refusing to start on a drifted schema" >&2
        exit 1
    fi
fi

exec gosu nodeglow "$@"
