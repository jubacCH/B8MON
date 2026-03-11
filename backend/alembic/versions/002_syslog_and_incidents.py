"""Add syslog_messages, incidents, and incident_events tables.

Revision ID: 002
Revises: 001
Create Date: 2026-03-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Syslog messages
    op.create_table(
        "syslog_messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("source_ip", sa.String(45), nullable=False),
        sa.Column("hostname", sa.String(255), nullable=True),
        sa.Column("facility", sa.SmallInteger(), nullable=True),
        sa.Column("severity", sa.SmallInteger(), nullable=True),
        sa.Column("app_name", sa.String(128), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("host_id", sa.Integer(), sa.ForeignKey("ping_hosts.id"), nullable=True),
    )
    op.create_index("ix_syslog_ts", "syslog_messages", [sa.text("timestamp DESC")])
    op.create_index("ix_syslog_host_ts", "syslog_messages", ["host_id", sa.text("timestamp DESC")])
    op.create_index("ix_syslog_severity_ts", "syslog_messages", ["severity", sa.text("timestamp DESC")])
    op.create_index("ix_syslog_source_ip", "syslog_messages", ["source_ip"])

    # PostgreSQL-only: tsvector column + GIN index + trigger
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # Use raw SQL for tsvector since it's PG-specific
        op.execute("ALTER TABLE syslog_messages ADD COLUMN search_vector tsvector")
        op.execute("CREATE INDEX IF NOT EXISTS ix_syslog_fts ON syslog_messages USING gin(search_vector)")
        op.execute("""
            CREATE OR REPLACE FUNCTION syslog_search_vector_update() RETURNS trigger AS $$
            BEGIN
                NEW.search_vector :=
                    setweight(to_tsvector('english', COALESCE(NEW.hostname, '')), 'A') ||
                    setweight(to_tsvector('english', COALESCE(NEW.app_name, '')), 'B') ||
                    setweight(to_tsvector('english', COALESCE(NEW.message, '')), 'C');
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
        """)
        op.execute("""
            DO $$ BEGIN
                CREATE TRIGGER syslog_search_vector_trigger
                    BEFORE INSERT OR UPDATE ON syslog_messages
                    FOR EACH ROW EXECUTE FUNCTION syslog_search_vector_update();
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$;
        """)

    # Incidents
    op.create_table(
        "incidents",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("rule", sa.String(64), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False, server_default="warning"),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("host_ids_hash", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("acknowledged_by", sa.String(128), nullable=True),
    )
    op.create_index("ix_incident_status", "incidents", ["status"])
    op.create_index("ix_incident_rule_hash", "incidents", ["rule", "host_ids_hash"])

    # Incident events
    op.create_table(
        "incident_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("incident_id", sa.Integer(), sa.ForeignKey("incidents.id"), nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
    )
    op.create_index("ix_incident_event_ts", "incident_events", ["incident_id", sa.text("timestamp DESC")])


def downgrade() -> None:
    op.drop_table("incident_events")
    op.drop_table("incidents")

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS syslog_search_vector_trigger ON syslog_messages")
        op.execute("DROP FUNCTION IF EXISTS syslog_search_vector_update()")

    op.drop_table("syslog_messages")
