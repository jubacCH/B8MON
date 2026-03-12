"""Add agent_log_level column to agents.

Revision ID: 015
Revises: 014
"""
revision = "015"
down_revision = "014"

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.add_column("agents", sa.Column("agent_log_level", sa.String(16), nullable=True, server_default="errors"))
