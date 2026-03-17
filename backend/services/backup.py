"""Backup/restore service — JSON-based PostgreSQL export/import."""
import json
import logging
from datetime import datetime

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Tables to export (in dependency order — parents before children)
EXPORT_TABLES = [
    "settings",
    "users",
    "ping_hosts",
    "ping_results",
    "integration_configs",
    "snapshots",
    "agents",
    "agent_snapshots",
    "incidents",
    "incident_events",
    "alert_rules",
    "log_templates",
    "host_baselines",
    "precursor_patterns",
    "credentials",
    "snmp_mibs",
    "snmp_oids",
    "snmp_host_configs",
    "snmp_results",
    "api_keys",
    "notification_logs",
    "discovered_ports",
    "audit_logs",
]


async def export_backup(db: AsyncSession) -> dict:
    """Export all PostgreSQL tables as a JSON dict."""
    backup = {
        "_meta": {
            "version": "1.0",
            "timestamp": datetime.utcnow().isoformat(),
            "format": "nodeglow-backup",
        },
        "tables": {},
    }

    for table_name in EXPORT_TABLES:
        try:
            result = await db.execute(text(f"SELECT * FROM {table_name}"))
            rows = result.mappings().all()
            backup["tables"][table_name] = [
                {k: _serialize(v) for k, v in dict(row).items()}
                for row in rows
            ]
        except Exception as e:
            logger.warning("Skipping table %s: %s", table_name, e)
            backup["tables"][table_name] = []

    return backup


async def get_backup_info(db: AsyncSession) -> dict:
    """Get database statistics for the backup UI."""
    tables = {}
    for table_name in EXPORT_TABLES:
        try:
            result = await db.execute(text(f"SELECT COUNT(*) FROM {table_name}"))
            count = result.scalar()
            tables[table_name] = count
        except Exception:
            tables[table_name] = 0

    # Total database size
    try:
        result = await db.execute(text(
            "SELECT pg_size_pretty(pg_database_size(current_database()))"
        ))
        db_size = result.scalar()
    except Exception:
        db_size = "unknown"

    return {
        "tables": tables,
        "total_rows": sum(tables.values()),
        "db_size": db_size,
    }


async def import_backup(db: AsyncSession, data: dict) -> dict:
    """Import a backup JSON dict, replacing all existing data."""
    meta = data.get("_meta", {})
    if meta.get("format") != "nodeglow-backup":
        raise ValueError("Invalid backup format")

    tables_data = data.get("tables", {})
    imported = {}

    # Disable FK checks during import
    await db.execute(text("SET session_replication_role = 'replica'"))

    try:
        # Truncate in reverse order (children first)
        for table_name in reversed(EXPORT_TABLES):
            if table_name in tables_data:
                await db.execute(text(f"TRUNCATE TABLE {table_name} CASCADE"))

        # Get valid column names for each table from the DB schema
        _valid_columns: dict[str, set[str]] = {}
        conn = await db.connection()
        for t in EXPORT_TABLES:
            try:
                col_info = await conn.run_sync(lambda sc, tn=t: inspect(sc).get_columns(tn))
                _valid_columns[t] = {c["name"] for c in col_info}
            except Exception:
                _valid_columns[t] = set()

        # Insert in forward order (parents first)
        for table_name in EXPORT_TABLES:
            rows = tables_data.get(table_name, [])
            if not rows:
                imported[table_name] = 0
                continue

            # Validate column names against DB schema to prevent SQL injection
            allowed = _valid_columns.get(table_name, set())
            if not allowed:
                logger.warning("Skipping import of %s: could not determine schema", table_name)
                imported[table_name] = 0
                continue
            cols = [c for c in rows[0].keys() if c in allowed]
            if not cols:
                imported[table_name] = 0
                continue
            rejected = set(rows[0].keys()) - allowed
            if rejected:
                logger.warning("Ignoring unknown columns in %s: %s", table_name, rejected)

            placeholders = ", ".join(f":{c}" for c in cols)
            col_names = ", ".join(cols)

            for row in rows:
                # Only include validated columns
                safe_row = {c: row.get(c) for c in cols}
                await db.execute(
                    text(f"INSERT INTO {table_name} ({col_names}) VALUES ({placeholders})"),
                    safe_row,
                )

            # Reset sequence
            try:
                await db.execute(text(
                    f"SELECT setval(pg_get_serial_sequence('{table_name}', 'id'), "
                    f"COALESCE((SELECT MAX(id) FROM {table_name}), 1))"
                ))
            except Exception:
                pass

            imported[table_name] = len(rows)

        await db.commit()
    finally:
        await db.execute(text("SET session_replication_role = 'origin'"))

    return {"imported": imported, "total_rows": sum(imported.values())}


def _serialize(value):
    """Convert Python values to JSON-serializable types."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    return value
