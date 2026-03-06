#!/usr/bin/env python3
"""
One-time migration: copy data from old per-integration tables to the new
generic IntegrationConfig + Snapshot tables.

Works with both SQLite and PostgreSQL.

Run inside the container:
    docker exec vigil python migrate_to_generic.py

Safe to run multiple times -- skips if integration_configs already has rows.
Use --force to re-run even if rows exist (deletes existing data first).
"""
import asyncio
import json
import sys
from datetime import datetime

from sqlalchemy import text

from config import DATABASE_URL
from database import AsyncSessionLocal, decrypt_value
from models.base import encrypt_value as new_encrypt
from models.integration import IntegrationConfig, Snapshot
from services.integration import encrypt_config


# ── Mapping: old table -> new integration type + field extraction ─────────────

def _decrypt_safe(val) -> str:
    """Decrypt a value, returning empty string if blank or invalid."""
    if not val:
        return ""
    try:
        return decrypt_value(str(val))
    except Exception:
        return str(val)


MIGRATIONS = [
    {
        "type": "proxmox",
        "config_table": "proxmox_clusters",
        "snapshot_table": "proxmox_snapshots",
        "snapshot_fk": "cluster_id",
        "extract_config": lambda row: {
            "host": row["host"],
            "token_id": row["token_id"],
            "token_secret": _decrypt_safe(row["token_secret"]),
            "verify_ssl": bool(row["verify_ssl"]),
        },
    },
    {
        "type": "unifi",
        "config_table": "unifi_controllers",
        "snapshot_table": "unifi_snapshots",
        "snapshot_fk": "controller_id",
        "extract_config": lambda row: {
            "host": row["host"],
            "username": row["username"],
            "password": _decrypt_safe(row.get("password_enc", "")),
            "site": row.get("site") or "default",
            "is_udm": bool(row.get("is_udm", False)),
            "verify_ssl": bool(row["verify_ssl"]),
        },
    },
    {
        "type": "unas",
        "config_table": "unas_servers",
        "snapshot_table": "unas_snapshots",
        "snapshot_fk": "server_id",
        "extract_config": lambda row: {
            "host": row["host"],
            "username": row["username"],
            "password": _decrypt_safe(row.get("password_enc", "")),
            "verify_ssl": bool(row["verify_ssl"]),
        },
    },
    {
        "type": "pihole",
        "config_table": "pihole_instances",
        "snapshot_table": "pihole_snapshots",
        "snapshot_fk": "instance_id",
        "extract_config": lambda row: {
            "host": row["host"],
            "api_key": _decrypt_safe(row.get("api_key_enc", "")),
            "verify_ssl": bool(row["verify_ssl"]),
        },
    },
    {
        "type": "adguard",
        "config_table": "adguard_instances",
        "snapshot_table": "adguard_snapshots",
        "snapshot_fk": "instance_id",
        "extract_config": lambda row: {
            "host": row["host"],
            "username": row.get("username", ""),
            "password": _decrypt_safe(row.get("password_enc", "")),
            "verify_ssl": bool(row["verify_ssl"]),
        },
    },
    {
        "type": "portainer",
        "config_table": "portainer_instances",
        "snapshot_table": "portainer_snapshots",
        "snapshot_fk": "instance_id",
        "extract_config": lambda row: {
            "host": row["host"],
            "api_key": _decrypt_safe(row.get("api_key_enc", "")),
            "verify_ssl": bool(row["verify_ssl"]),
        },
    },
    {
        "type": "truenas",
        "config_table": "truenas_servers",
        "snapshot_table": "truenas_snapshots",
        "snapshot_fk": "server_id",
        "extract_config": lambda row: {
            "host": row["host"],
            "api_key": _decrypt_safe(row.get("api_key_enc", "")),
            "verify_ssl": bool(row["verify_ssl"]),
        },
    },
    {
        "type": "synology",
        "config_table": "synology_servers",
        "snapshot_table": "synology_snapshots",
        "snapshot_fk": "server_id",
        "extract_config": lambda row: {
            "host": row["host"],
            "port": row.get("port", 5001),
            "username": row["username"],
            "password": _decrypt_safe(row.get("password_enc", "")),
            "verify_ssl": bool(row["verify_ssl"]),
        },
    },
    {
        "type": "firewall",
        "config_table": "firewall_instances",
        "snapshot_table": "firewall_snapshots",
        "snapshot_fk": "instance_id",
        "extract_config": lambda row: {
            "host": row["host"],
            "fw_type": row.get("fw_type", "opnsense"),
            "api_key": _decrypt_safe(row.get("api_key_enc", "")),
            "api_secret": _decrypt_safe(row.get("api_secret_enc", "")),
            "verify_ssl": bool(row["verify_ssl"]),
        },
    },
    {
        "type": "hass",
        "config_table": "hass_instances",
        "snapshot_table": "hass_snapshots",
        "snapshot_fk": "instance_id",
        "extract_config": lambda row: {
            "host": row["host"],
            "token": _decrypt_safe(row.get("token_enc", "")),
            "verify_ssl": bool(row["verify_ssl"]),
        },
    },
    {
        "type": "gitea",
        "config_table": "gitea_instances",
        "snapshot_table": "gitea_snapshots",
        "snapshot_fk": "instance_id",
        "extract_config": lambda row: {
            "host": row["host"],
            "token": _decrypt_safe(row.get("token_enc", "")),
            "verify_ssl": bool(row["verify_ssl"]),
        },
    },
    {
        "type": "phpipam",
        "config_table": "phpipam_servers",
        "snapshot_table": "phpipam_snapshots",
        "snapshot_fk": "server_id",
        "extract_config": lambda row: {
            "host": row["host"],
            "app_id": row.get("app_id", ""),
            "username": row.get("username", ""),
            "password": _decrypt_safe(row.get("password_enc", "")),
            "verify_ssl": bool(row.get("verify_ssl", True)),
        },
    },
    {
        "type": "speedtest",
        "config_table": "speedtest_configs",
        "snapshot_table": "speedtest_results",
        "snapshot_fk": "config_id",
        "extract_config": lambda row: {
            "server_id": row.get("server_id") or "",
        },
        "convert_snapshot": lambda row: {
            "download_mbps": row.get("download_mbps"),
            "upload_mbps": row.get("upload_mbps"),
            "ping_ms": row.get("ping_ms"),
            "server_name": row.get("server_name", ""),
            "server_location": row.get("server_location", ""),
        },
    },
    {
        "type": "ups",
        "config_table": "nut_instances",
        "snapshot_table": "nut_snapshots",
        "snapshot_fk": "instance_id",
        "extract_config": lambda row: {
            "host": row["host"],
            "port": row.get("port", 3493),
            "ups_name": row.get("ups_name", "ups"),
            "username": row.get("username", ""),
            "password": _decrypt_safe(row.get("password_enc", "")),
        },
    },
    {
        "type": "redfish",
        "config_table": "redfish_servers",
        "snapshot_table": "redfish_snapshots",
        "snapshot_fk": "server_id",
        "extract_config": lambda row: {
            "host": row["host"],
            "username": row["username"],
            "password": _decrypt_safe(row.get("password_enc", "")),
            "verify_ssl": bool(row["verify_ssl"]),
        },
    },
]


def _is_pg() -> bool:
    return "postgresql" in (DATABASE_URL or "")


def _table_exists_sql(table_name: str) -> str:
    if _is_pg():
        return f"SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = '{table_name}')"
    return f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}'"


async def _check_table(db, table_name: str) -> bool:
    r = await db.execute(text(_table_exists_sql(table_name)))
    val = r.scalar()
    return bool(val)


def _fix_val(val, col_name: str):
    """Fix type mismatches between SQLite and PostgreSQL values."""
    if val is None:
        return val
    # Boolean columns stored as int in SQLite
    if col_name in ("ok", "verify_ssl", "enabled", "is_udm"):
        return bool(val)
    # Datetime columns stored as string in SQLite
    if col_name in ("timestamp", "created_at"):
        if isinstance(val, str):
            for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
                try:
                    return datetime.strptime(val, fmt)
                except ValueError:
                    continue
        return val
    return val


async def migrate(force: bool = False):
    # Ensure new tables exist
    from models import init_db
    await init_db()

    async with AsyncSessionLocal() as db:
        result = await db.execute(text("SELECT COUNT(*) FROM integration_configs"))
        existing = result.scalar()
        if existing > 0:
            if force:
                print(f"--force: deleting {existing} existing integration_configs + snapshots...")
                await db.execute(text("DELETE FROM snapshots"))
                await db.execute(text("DELETE FROM integration_configs"))
                await db.commit()
            else:
                print(f"integration_configs already has {existing} rows. Use --force to re-run.")
                return

    total_configs = 0
    total_snaps = 0

    for m in MIGRATIONS:
        int_type = m["type"]
        config_table = m["config_table"]
        snap_table = m["snapshot_table"]
        snap_fk = m["snapshot_fk"]

        async with AsyncSessionLocal() as db:
            if not await _check_table(db, config_table):
                print(f"  [{int_type}] table '{config_table}' not found -- skipping")
                continue

            rows = await db.execute(text(f"SELECT * FROM {config_table}"))
            old_configs = [dict(row._mapping) for row in rows]

            if not old_configs:
                print(f"  [{int_type}] no configs -- skipping")
                continue

            id_map: dict[int, int] = {}

            for old in old_configs:
                try:
                    config_dict = m["extract_config"](old)
                except Exception as e:
                    print(f"  [{int_type}] error extracting config id={old['id']}: {e}")
                    continue

                created_at = _fix_val(old.get("created_at"), "created_at")
                if not isinstance(created_at, datetime):
                    created_at = datetime.utcnow()

                new_cfg = IntegrationConfig(
                    type=int_type,
                    name=old.get("name", int_type),
                    config_json=encrypt_config(config_dict),
                    enabled=True,
                    created_at=created_at,
                )
                db.add(new_cfg)
                await db.flush()
                id_map[old["id"]] = new_cfg.id
                total_configs += 1

            if await _check_table(db, snap_table):
                snap_rows = await db.execute(text(f"SELECT * FROM {snap_table}"))
                for snap in snap_rows:
                    snap_dict = dict(snap._mapping)
                    old_parent_id = snap_dict.get(snap_fk)
                    new_parent_id = id_map.get(old_parent_id)
                    if not new_parent_id:
                        continue

                    if "convert_snapshot" in m:
                        data_json = json.dumps(m["convert_snapshot"](snap_dict))
                    else:
                        data_json = snap_dict.get("data_json")

                    ts = _fix_val(snap_dict.get("timestamp"), "timestamp")
                    if not isinstance(ts, datetime):
                        ts = datetime.utcnow()

                    ok_val = snap_dict.get("ok", True)
                    if not isinstance(ok_val, bool):
                        ok_val = bool(ok_val)

                    new_snap = Snapshot(
                        entity_type=int_type,
                        entity_id=new_parent_id,
                        timestamp=ts,
                        ok=ok_val,
                        data_json=data_json,
                        error=snap_dict.get("error"),
                    )
                    db.add(new_snap)
                    total_snaps += 1

            await db.commit()
            print(f"  [{int_type}] migrated {len(id_map)} configs + snapshots")

    # Reset PG sequences so new IDs don't collide
    if _is_pg():
        async with AsyncSessionLocal() as db:
            for table in ("integration_configs", "snapshots"):
                await db.execute(text(
                    f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                    f"COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)"
                ))
            await db.commit()
        print("  PostgreSQL sequences reset.")

    print(f"\nDone! Migrated {total_configs} configs and {total_snaps} snapshots.")


if __name__ == "__main__":
    force = "--force" in sys.argv
    asyncio.run(migrate(force=force))
