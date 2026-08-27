"""Bring the database schema up to date before the application starts.

Two situations have to be handled, and conflating them is exactly what caused
the production schema drift (code for revision 030 running against schema 028):

**Fresh install** — no ``alembic_version`` table. The migration chain cannot
build the schema from nothing: it grew alongside a codebase where tables were
created by SQLAlchemy's ``create_all()``, so several tables (``agents`` among
them) are defined only in the models and in no migration. Running
``alembic upgrade head`` here fails with "relation ... does not exist".
The correct action is to create the schema from the models and stamp it as
current.

**Existing install** — ``alembic_version`` is present. Run the outstanding
migrations and nothing else.

Run as ``python migrate.py``; exits non-zero if the schema could not be brought
up to date, so the caller can refuse to start the app on a drifted schema.
"""
from __future__ import annotations

import asyncio
import logging
import sys

logging.basicConfig(level=logging.INFO, format="[migrate] %(message)s")
log = logging.getLogger("nodeglow.migrate")

ALEMBIC_CONFIG = "alembic.ini"


async def has_alembic_version() -> bool:
    """True when this database has already been placed under Alembic control."""
    from sqlalchemy import text

    from models.base import engine

    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT to_regclass('public.alembic_version')")
        )
        return result.scalar() is not None


async def create_schema_from_models() -> None:
    """Create every table the models define (fresh installs only)."""
    from models import init_db

    await init_db()


def stamp_head() -> None:
    """Mark the freshly created schema as being at the latest revision."""
    from alembic import command
    from alembic.config import Config

    command.stamp(Config(ALEMBIC_CONFIG), "head")


def upgrade_head() -> None:
    """Apply outstanding migrations to an existing database."""
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config(ALEMBIC_CONFIG), "head")


async def _run() -> None:
    if await has_alembic_version():
        log.info("existing database — applying outstanding migrations")
        upgrade_head()
        log.info("schema is at head")
        return

    log.info("fresh database — creating schema from models")
    await create_schema_from_models()
    stamp_head()
    log.info("schema created and stamped at head")


def main() -> int:
    try:
        asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001
        log.error("failed to bring schema up to date: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
