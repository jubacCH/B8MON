"""Add notify_channels to alert_rules.

Revision ID: 011
Revises: 010
"""
from alembic import op
import sqlalchemy as sa

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("alert_rules", sa.Column("notify_channels", sa.String(256), nullable=True))


def downgrade():
    op.drop_column("alert_rules", "notify_channels")
