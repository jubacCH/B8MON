"""Add (incident_id, event_type, timestamp) index on incident_events.

The incident list endpoint filters events by incident_id + event_type and
sorts by timestamp; the existing (incident_id, timestamp) index forces a
scan over all events of an incident once event counts grow large.

Revision ID: 030
Revises: 029
"""
revision = "030"
down_revision = "029"

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.create_index(
        "ix_incident_event_type_ts",
        "incident_events",
        ["incident_id", "event_type", sa.text("timestamp DESC")],
    )


def downgrade():
    op.drop_index("ix_incident_event_type_ts", table_name="incident_events")
