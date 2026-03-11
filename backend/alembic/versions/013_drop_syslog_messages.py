"""Drop syslog_messages table — migrated to ClickHouse.

Revision ID: 013
Revises: 012
"""
from alembic import op

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS syslog_search_vector_trigger ON syslog_messages")
        op.execute("DROP FUNCTION IF EXISTS syslog_search_vector_update()")
    op.drop_table("syslog_messages")


def downgrade():
    # One-way migration — syslog_messages now lives in ClickHouse
    pass
