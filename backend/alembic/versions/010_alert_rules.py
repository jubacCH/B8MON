"""Add alert_rules table for user-defined triggers.

Revision ID: 010
Revises: 009
Create Date: 2026-03-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "alert_rules",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("field_path", sa.String(256), nullable=False),
        sa.Column("operator", sa.String(24), nullable=False),
        sa.Column("threshold", sa.String(256), nullable=True),
        sa.Column("severity", sa.String(16), nullable=False, server_default="warning"),
        sa.Column("message_template", sa.Text(), nullable=True),
        sa.Column("cooldown_minutes", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("last_triggered_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("alert_rules")
