"""Tune autovacuum on churn-heavy tables and enable pg_stat_statements.

Postgres only autovacuums a table once 20% of its rows are dead
(autovacuum_vacuum_scale_factor = 0.2). For tables that churn constantly under
a retention policy, that threshold arrives far too late and the table bloats
between passes. Measured on production:

- incident_events: 1709 live rows occupying 490 MB heap + 215 MB indexes
- snapshots: 14'751 dead rows (13.6%) against 108'768 live, 537 MB

Lowering the scale factor makes autovacuum reclaim space continuously instead
of in rare large sweeps. This prevents bloat from accumulating on new
installations; existing bloat still needs a one-off VACUUM FULL.

pg_stat_statements is enabled so slow queries can be identified on a customer
installation without shell access. The extension is created here; the library
is loaded via shared_preload_libraries in docker-compose.

Revision ID: 032
Revises: 031
"""
revision = "032"
down_revision = "031"

from alembic import op
import sqlalchemy as sa

# Tables with continuous insert/delete churn driven by retention jobs.
CHURN_TABLES = ["incident_events", "snapshots", "log_templates", "notification_logs"]

AUTOVACUUM_SETTINGS = (
    "autovacuum_vacuum_scale_factor = 0.05, "
    "autovacuum_analyze_scale_factor = 0.02, "
    "autovacuum_vacuum_cost_limit = 1000"
)


def upgrade():
    for table in CHURN_TABLES:
        op.execute(f"ALTER TABLE {table} SET ({AUTOVACUUM_SETTINGS})")

    # Diagnostic aid, not a runtime dependency: only create it where the
    # extension files actually ship. Checking first avoids aborting the
    # migration's transaction on installations that lack contrib.
    available = op.get_bind().execute(
        sa.text(
            "SELECT count(*) FROM pg_available_extensions "
            "WHERE name = 'pg_stat_statements'"
        )
    ).scalar()
    if available:
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_stat_statements")


def downgrade():
    for table in CHURN_TABLES:
        op.execute(
            f"ALTER TABLE {table} RESET ("
            "autovacuum_vacuum_scale_factor, "
            "autovacuum_analyze_scale_factor, "
            "autovacuum_vacuum_cost_limit)"
        )
