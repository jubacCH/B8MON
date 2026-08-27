"""Align the migration-built schema with the models.

With revision 001 completed, the chain can finally build a database from
nothing — and ``alembic check`` immediately showed that the result differs from
what the models describe. Everything below existed only because
``create_all()`` produced it; a database built purely from migrations was
missing it.

The most consequential are three columns on ``ping_hosts``. ``port_error`` and
``check_detail`` carry the service-check state that the ping scheduler writes on
every cycle, and ``parent_id`` is the topology link the correlation engine uses
to group dependent hosts. A migration-built installation had none of them.

Every step checks first: on any database the application has ever started
against, these objects already exist and this revision is a no-op.

Revision ID: 033
Revises: 032
"""
revision = "033"
down_revision = "032"

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


def _inspector():
    return inspect(op.get_bind())


def _has_column(insp, table: str, column: str) -> bool:
    try:
        return any(c["name"] == column for c in insp.get_columns(table))
    except Exception:  # noqa: BLE001 — table absent
        return False


def _has_index(insp, table: str, name: str) -> bool:
    try:
        return any(i["name"] == name for i in insp.get_indexes(table))
    except Exception:  # noqa: BLE001
        return False


def _has_fk(insp, table: str, column: str) -> bool:
    try:
        return any(
            column in fk.get("constrained_columns", []) for fk in insp.get_foreign_keys(table)
        )
    except Exception:  # noqa: BLE001
        return False


def upgrade():
    insp = _inspector()
    tables = set(insp.get_table_names())

    # ── ping_hosts: service-check state and topology link ────────────────────
    if "ping_hosts" in tables:
        if not _has_column(insp, "ping_hosts", "port_error"):
            op.add_column(
                "ping_hosts",
                sa.Column("port_error", sa.Boolean(), server_default=sa.false()),
            )
        if not _has_column(insp, "ping_hosts", "check_detail"):
            op.add_column("ping_hosts", sa.Column("check_detail", sa.Text(), nullable=True))
        if not _has_column(insp, "ping_hosts", "parent_id"):
            op.add_column("ping_hosts", sa.Column("parent_id", sa.Integer(), nullable=True))
            op.create_foreign_key(
                "fk_ping_hosts_parent", "ping_hosts", "ping_hosts",
                ["parent_id"], ["id"],
            )

    # ── discovered_ports: cascade so ports vanish with their host ────────────
    if "discovered_ports" in tables and not _has_fk(insp, "discovered_ports", "host_id"):
        op.create_foreign_key(
            "fk_discovered_ports_host", "discovered_ports", "ping_hosts",
            ["host_id"], ["id"], ondelete="CASCADE",
        )

    # ── snapshots: ok is written on every collection, never absent ───────────
    if "snapshots" in tables:
        op.execute("UPDATE snapshots SET ok = true WHERE ok IS NULL")
        op.alter_column("snapshots", "ok", existing_type=sa.Boolean(), nullable=False)
        if not _has_index(insp, "snapshots", "ix_snap_entity_id"):
            op.create_index("ix_snap_entity_id", "snapshots", ["entity_id"])

    # ── log_templates: the models name this index differently ───────────────
    if "log_templates" in tables:
        if not _has_index(insp, "log_templates", "ix_log_templates_noise_score"):
            op.create_index(
                "ix_log_templates_noise_score", "log_templates", ["noise_score"]
            )
        if _has_index(insp, "log_templates", "ix_log_tpl_noise"):
            op.drop_index("ix_log_tpl_noise", table_name="log_templates")

    # ── ai_usage_logs: the model orders this index descending ───────────────
    if "ai_usage_logs" in tables:
        if _has_index(insp, "ai_usage_logs", "ix_ai_usage_ts"):
            op.drop_index("ix_ai_usage_ts", table_name="ai_usage_logs")
        op.create_index(
            "ix_ai_usage_ts", "ai_usage_logs", [sa.text("timestamp DESC")]
        )


def downgrade():
    insp = _inspector()

    if _has_index(insp, "ai_usage_logs", "ix_ai_usage_ts"):
        op.drop_index("ix_ai_usage_ts", table_name="ai_usage_logs")
        op.create_index("ix_ai_usage_ts", "ai_usage_logs", ["timestamp"])

    if _has_index(insp, "log_templates", "ix_log_templates_noise_score"):
        op.drop_index("ix_log_templates_noise_score", table_name="log_templates")
        op.create_index("ix_log_tpl_noise", "log_templates", ["noise_score"])

    if _has_index(insp, "snapshots", "ix_snap_entity_id"):
        op.drop_index("ix_snap_entity_id", table_name="snapshots")
    op.alter_column("snapshots", "ok", existing_type=sa.Boolean(), nullable=True)

    if _has_fk(insp, "discovered_ports", "host_id"):
        op.drop_constraint("fk_discovered_ports_host", "discovered_ports", type_="foreignkey")

    for column in ("parent_id", "check_detail", "port_error"):
        if _has_column(insp, "ping_hosts", column):
            if column == "parent_id":
                op.drop_constraint("fk_ping_hosts_parent", "ping_hosts", type_="foreignkey")
            op.drop_column("ping_hosts", column)
