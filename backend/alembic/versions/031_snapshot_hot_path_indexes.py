"""Add indexes for the two snapshot queries that fall back to sequential scans.

Measured on production (103'771 rows, 537 MB, 99 distinct entities):

1. The precursor learner filters ``ok = false AND timestamp >= lookback``. No
   existing index leads with those columns — all three start with entity_type
   or entity_id — so the planner scans the whole table to find the 402 rows
   that actually have ok = false:

       Seq Scan on snapshots  (cost=0.00..18501.69 rows=426)
         Filter: ((NOT ok) AND (timestamp >= now() - '7 days'))

   A partial index holds only the failed snapshots, so it stays tiny.

2. The health service takes max(id) grouped by (entity_type, entity_id) to find
   the newest snapshot per entity. That aggregate reads every heap row:

       HashAggregate  (cost=18501.69..18502.68 rows=99)
         ->  Seq Scan on snapshots  (cost=0.00..17737.68 rows=101868)

   Adding id to the (entity_type, entity_id) prefix lets this be served from
   the index instead of the 537 MB heap.

Revision ID: 031
Revises: 030
"""
revision = "031"
down_revision = "030"

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.create_index(
        "ix_snap_failed_ts",
        "snapshots",
        ["timestamp"],
        postgresql_where=sa.text("ok = false"),
    )
    op.create_index(
        "ix_snap_latest_per_entity",
        "snapshots",
        ["entity_type", "entity_id", sa.text("id DESC")],
    )


def downgrade():
    op.drop_index("ix_snap_latest_per_entity", table_name="snapshots")
    op.drop_index("ix_snap_failed_ts", table_name="snapshots")
