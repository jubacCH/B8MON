"""Add performance indexes for syslog filtering, snapshot cleanup, and log intelligence.

Revision ID: 005
Revises: 004
Create Date: 2026-03-08
"""
from typing import Sequence, Union

from alembic import op

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Syslog: app_name filter (ILIKE queries on syslog page)
    op.create_index("ix_syslog_app_name", "syslog_messages", ["app_name"])
    # Syslog: hostname filter (host detail syslog tab)
    op.create_index("ix_syslog_hostname", "syslog_messages", ["hostname"])

    # Snapshot: cleanup by type + timestamp range
    op.create_index("ix_snap_type_ts", "snapshots", ["entity_type", "timestamp"])

    # LogTemplate: noise filtering + recently discovered
    op.create_index("ix_log_tpl_noise", "log_templates", ["noise_score"])
    op.create_index("ix_log_tpl_first_seen", "log_templates", ["first_seen"])

    # Incident: list view ordering by updated_at
    op.create_index("ix_incident_updated", "incidents", ["updated_at"])

    # PingHost: agent cleanup (source + source_detail lookup)
    op.create_index("ix_ping_hosts_source", "ping_hosts", ["source"])


def downgrade() -> None:
    op.drop_index("ix_ping_hosts_source", table_name="ping_hosts")
    op.drop_index("ix_incident_updated", table_name="incidents")
    op.drop_index("ix_log_tpl_first_seen", table_name="log_templates")
    op.drop_index("ix_log_tpl_noise", table_name="log_templates")
    op.drop_index("ix_snap_type_ts", table_name="snapshots")
    op.drop_index("ix_syslog_hostname", table_name="syslog_messages")
    op.drop_index("ix_syslog_app_name", table_name="syslog_messages")
