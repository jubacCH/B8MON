"""Initial schema — all tables managed by the new models package.

Revision ID: 001
Revises: None
Create Date: 2026-03-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Settings
    op.create_table(
        "settings",
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("encrypted", sa.Boolean(), default=False),
    )

    # Users
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("username", sa.String(64), unique=True, nullable=False),
        sa.Column("password_hash", sa.String(128), nullable=False),
        sa.Column("role", sa.String(16), server_default="admin"),
        sa.Column("created_at", sa.DateTime()),
    )

    # Sessions
    op.create_table(
        "sessions",
        sa.Column("token", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )

    # Ping hosts
    op.create_table(
        "ping_hosts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("hostname", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="1"),
        sa.Column("check_type", sa.String(), server_default="icmp"),
        sa.Column("port", sa.Integer(), nullable=True),
        sa.Column("latency_threshold_ms", sa.Float(), nullable=True),
        sa.Column("maintenance", sa.Boolean(), server_default="0"),
        sa.Column("ssl_expiry_days", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(), server_default="manual"),
        sa.Column("source_detail", sa.String(), nullable=True),
        sa.Column("mac_address", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime()),
    )

    # Ping results
    op.create_table(
        "ping_results",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("host_id", sa.Integer(), sa.ForeignKey("ping_hosts.id"), nullable=False),
        sa.Column("timestamp", sa.DateTime(), index=True),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=True),
    )

    # Integration configs (generic)
    op.create_table(
        "integration_configs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("type", sa.String(32), nullable=False, index=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="1"),
        sa.Column("created_at", sa.DateTime()),
    )

    # Snapshots (generic)
    op.create_table(
        "snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("timestamp", sa.DateTime()),
        sa.Column("ok", sa.Boolean(), server_default="1"),
        sa.Column("data_json", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_snap_type_entity_ts",
        "snapshots",
        ["entity_type", "entity_id", sa.text("timestamp DESC")],
    )

    _create_legacy_create_all_tables()


# ── Tables that were historically created by SQLAlchemy's create_all() ───────
#
# These seven were never part of any migration: the chain grew alongside a
# codebase where `Base.metadata.create_all()` produced the schema, so the
# migrations only ever recorded *changes*. The result was that
# `alembic upgrade head` could not build a database from nothing — migration
# 003 failed with "relation agents does not exist" — which meant a fresh
# customer installation had no working migration path at all.
#
# They are defined here at their state as of revision 001, deliberately without
# the columns and indexes that later revisions add (004/012/015/016/017 for
# agents, 019 for the intelligence tables, 003 and 005 for indexes), so the rest
# of the chain still applies cleanly on top.
#
# Existing installations have long recorded 001 as applied and never re-run it,
# so this is a no-op for them. Every step is guarded by an existence check
# regardless, because these tables are present on any database that was ever
# started by the application.

def _create_legacy_create_all_tables() -> None:
    from sqlalchemy import inspect as _inspect

    bind = op.get_bind()
    existing = set(_inspect(bind).get_table_names())

    def missing(name: str) -> bool:
        return name not in existing

    if missing("agents"):
        op.create_table(
            "agents",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("hostname", sa.String(256), nullable=True),
            sa.Column("token", sa.String(64), nullable=False),
            sa.Column("platform", sa.String(32), nullable=True),
            sa.Column("arch", sa.String(32), nullable=True),
            sa.Column("agent_version", sa.String(16), nullable=True),
            sa.Column("enabled", sa.Boolean(), server_default=sa.true()),
            sa.Column("last_seen", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
        # ix_agents_hostname_lower is created by revision 003.
        op.create_index("ix_agents_token", "agents", ["token"], unique=True)

    if missing("log_templates"):
        op.create_table(
            "log_templates",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("template_hash", sa.String(32), nullable=False),
            sa.Column("template", sa.Text(), nullable=False),
            sa.Column("example", sa.Text(), nullable=True),
            sa.Column("count", sa.Integer(), server_default="1"),
            sa.Column("first_seen", sa.DateTime(), nullable=True),
            sa.Column("last_seen", sa.DateTime(), nullable=True),
            sa.Column("noise_score", sa.SmallInteger(), server_default="50"),
            sa.Column("tags", sa.String(256), server_default=""),
            sa.Column("avg_rate_per_hour", sa.Float(), server_default="0.0"),
        )
        op.create_index(
            "ix_log_templates_template_hash", "log_templates",
            ["template_hash"], unique=True,
        )
        # ix_log_tpl_noise and ix_log_tpl_first_seen are created by revision 005.

    if missing("host_baselines"):
        op.create_table(
            "host_baselines",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("host_key", sa.String(64), nullable=False),
            sa.Column("hour_of_day", sa.SmallInteger(), nullable=False),
            sa.Column("day_of_week", sa.SmallInteger(), nullable=False),
            sa.Column("avg_rate", sa.Float(), server_default="0.0"),
            sa.Column("std_rate", sa.Float(), server_default="0.0"),
            sa.Column("sample_count", sa.Integer(), server_default="0"),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )
        op.create_index(
            "ix_baseline_host_time", "host_baselines",
            ["host_key", "hour_of_day", "day_of_week"], unique=True,
        )

    if missing("precursor_patterns"):
        op.create_table(
            "precursor_patterns",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("template_id", sa.Integer(),
                      sa.ForeignKey("log_templates.id"), nullable=False),
            sa.Column("precedes_event", sa.String(64), nullable=False),
            sa.Column("confidence", sa.Float(), server_default="0.0"),
            sa.Column("avg_lead_time_sec", sa.Integer(), server_default="0"),
            sa.Column("occurrence_count", sa.Integer(), server_default="0"),
            sa.Column("total_checked", sa.Integer(), server_default="0"),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )
        op.create_index(
            "ix_precursor_tpl_event", "precursor_patterns",
            ["template_id", "precedes_event"], unique=True,
        )

    if missing("ai_usage_logs"):
        op.create_table(
            "ai_usage_logs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("timestamp", sa.DateTime(), nullable=False),
            sa.Column("feature", sa.String(64), nullable=False),
            sa.Column("model", sa.String(64), nullable=False),
            sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("cost_usd", sa.Float(), nullable=True),
            sa.Column("metadata_json", sa.Text(), nullable=True),
        )
        op.create_index("ix_ai_usage_feature", "ai_usage_logs", ["feature"])
        op.create_index("ix_ai_usage_ts", "ai_usage_logs", ["timestamp"])

    if missing("discovered_ports"):
        op.create_table(
            "discovered_ports",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("host_id", sa.Integer(), nullable=False),
            sa.Column("port", sa.Integer(), nullable=False),
            sa.Column("protocol", sa.String(8), server_default="tcp"),
            sa.Column("service", sa.String(64), nullable=True),
            sa.Column("status", sa.String(16), server_default="new"),
            sa.Column("has_ssl", sa.Boolean(), server_default=sa.false()),
            sa.Column("ssl_issuer", sa.String(256), nullable=True),
            sa.Column("ssl_subject", sa.String(256), nullable=True),
            sa.Column("ssl_expiry_days", sa.Integer(), nullable=True),
            sa.Column("ssl_expiry_date", sa.String(32), nullable=True),
            sa.Column("ssl_status", sa.String(16), nullable=True),
            sa.Column("first_seen", sa.DateTime(), nullable=True),
            sa.Column("last_seen", sa.DateTime(), nullable=True),
            sa.Column("last_open", sa.Boolean(), nullable=True),
        )
        op.create_index(
            "ix_disc_port_host_port", "discovered_ports",
            ["host_id", "port"], unique=True,
        )
        op.create_index("ix_disc_port_host", "discovered_ports", ["host_id"])

    if missing("syslog_views"):
        op.create_table(
            "syslog_views",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("filters_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    op.drop_table("snapshots")
    op.drop_table("integration_configs")
    op.drop_table("ping_results")
    op.drop_table("ping_hosts")
    op.drop_table("sessions")
    op.drop_table("users")
    op.drop_table("settings")
