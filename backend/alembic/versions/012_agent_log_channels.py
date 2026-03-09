"""Add log_channels and log_file_paths to agents.

Revision ID: 012
Revises: 011
"""
from alembic import op
import sqlalchemy as sa

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("agents", sa.Column("log_channels", sa.Text, nullable=True))
    op.add_column("agents", sa.Column("log_file_paths", sa.Text, nullable=True))
    # Set default for existing agents
    op.execute("UPDATE agents SET log_channels = 'System,Application' WHERE log_channels IS NULL")


def downgrade():
    op.drop_column("agents", "log_channels")
    op.drop_column("agents", "log_file_paths")
