"""Remote probes: an agent may run checks for hosts in its network.

A probe is an agent with the capability enabled, so it reuses enrolment, tokens
and signed updates rather than introducing a second kind of thing to manage.

``ping_hosts.probe_id`` says which probe checks a host. NULL means the core
does, which is every existing row — nothing changes behaviour on upgrade.

Revision ID: 034
Revises: 033
"""
import sqlalchemy as sa
from alembic import op

revision = "034"
down_revision = "033"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    return column in {
        c["name"] for c in sa.inspect(bind).get_columns(table)
    }


def upgrade() -> None:
    if not _has_column("agents", "is_probe"):
        op.add_column(
            "agents",
            sa.Column("is_probe", sa.Boolean(), nullable=False,
                      server_default=sa.false()),
        )
    if not _has_column("agents", "probe_interval_seconds"):
        op.add_column(
            "agents",
            sa.Column("probe_interval_seconds", sa.Integer(), nullable=True),
        )

    if not _has_column("ping_hosts", "probe_id"):
        op.add_column(
            "ping_hosts", sa.Column("probe_id", sa.Integer(), nullable=True)
        )
        op.create_index(
            "ix_ping_hosts_probe_id", "ping_hosts", ["probe_id"]
        )
        # SET NULL rather than CASCADE: removing a probe must not delete the
        # hosts it watched. They fall back to the core, which is the safe
        # direction — losing a probe should never lose monitoring coverage.
        op.create_foreign_key(
            "fk_ping_hosts_probe_id", "ping_hosts", "agents",
            ["probe_id"], ["id"], ondelete="SET NULL",
        )


def downgrade() -> None:
    if _has_column("ping_hosts", "probe_id"):
        op.drop_constraint("fk_ping_hosts_probe_id", "ping_hosts", type_="foreignkey")
        op.drop_index("ix_ping_hosts_probe_id", table_name="ping_hosts")
        op.drop_column("ping_hosts", "probe_id")
    if _has_column("agents", "probe_interval_seconds"):
        op.drop_column("agents", "probe_interval_seconds")
    if _has_column("agents", "is_probe"):
        op.drop_column("agents", "is_probe")
