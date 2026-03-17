#!/bin/sh
# Fix ownership of mounted /data volume so the nodeglow user can read/write it.
# The entrypoint runs as root; after fixing permissions it drops to nodeglow.
chown -R nodeglow:nodeglow /data 2>/dev/null || true
exec gosu nodeglow "$@"
