"""Add notification_logs table for notification history.

Revision ID: 009
Revises: 008
Create Date: 2026-03-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "notification_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("timestamp", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("severity", sa.String(16), nullable=False, server_default="info"),
        sa.Column("status", sa.String(16), nullable=False, server_default="sent"),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_notif_log_ts", "notification_logs", [sa.text("timestamp DESC")])
    op.create_index("ix_notif_log_channel", "notification_logs", ["channel"])


def downgrade() -> None:
    op.drop_table("notification_logs")
