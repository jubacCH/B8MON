"""
Alembic async environment for Nodeglow.

Supports both SQLite (aiosqlite) and PostgreSQL (asyncpg).
"""
import asyncio
import os
import sys
from logging.config import fileConfig

# Ensure the app root is on the path (needed when running alembic CLI directly)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Import all models so metadata knows about them
from config import DATABASE_URL
from models.base import Base
from models.integration import IntegrationConfig, Snapshot  # noqa: F401
from models.settings import Setting, User, Session  # noqa: F401
from models.ping import PingHost  # noqa: F401
from models.syslog import SyslogView  # noqa: F401
from models.incident import Incident, IncidentEvent  # noqa: F401
from models.log_template import LogTemplate, HostBaseline, PrecursorPattern  # noqa: F401
from models.scanner import SubnetScanSchedule  # noqa: F401
from models.credential import Credential  # noqa: F401
from models.snmp import SnmpMib, SnmpOid, SnmpHostConfig, SnmpResult  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Override sqlalchemy.url from our config
config.set_main_option("sqlalchemy.url", DATABASE_URL)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode — emit SQL to stdout."""
    url = config.get_main_option("sqlalchemy.url")


def _include_object(obj, name, type_, reflected, compare_to):
    """Filter comparison noise that is not schema drift.

    A column declared ``unique=True`` in the models produces a unique INDEX,
    while the older migrations created a unique CONSTRAINT for the same column.
    PostgreSQL implements a unique constraint *with* an index, so the two are
    equivalent — but autogenerate reports the constraint as "to be removed".
    Acting on that would drop the uniqueness guarantee on API keys and agent
    install tokens, so it is filtered instead.

    This is deliberately narrow: only reflected constraints with no model
    counterpart are hidden. Genuinely missing columns, indexes and foreign keys
    still surface, which is what `alembic check` exists for.
    """
    if type_ == "unique_constraint" and reflected and compare_to is None:
        return False
    return True


    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode with async engine."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
