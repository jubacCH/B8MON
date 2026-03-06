"""Tests for the generic snapshot service."""
import pytest
from services import snapshot as snap_svc


async def test_save_and_get_latest(db):
    snap = await snap_svc.save(db, "test_type", 1, ok=True, data={"cpu": 42})
    await db.commit()

    latest = await snap_svc.get_latest(db, "test_type", 1)
    assert latest is not None
    assert latest.ok is True
    assert latest.id == snap.id


async def test_get_latest_returns_newest(db):
    await snap_svc.save(db, "test_type", 1, ok=True, data={"v": 1})
    await snap_svc.save(db, "test_type", 1, ok=False, error="fail")
    await db.commit()

    latest = await snap_svc.get_latest(db, "test_type", 1)
    assert latest.ok is False
    assert latest.error == "fail"


async def test_get_latest_batch(db):
    await snap_svc.save(db, "proxmox", 1, ok=True, data={"a": 1})
    await snap_svc.save(db, "proxmox", 2, ok=True, data={"b": 2})
    await snap_svc.save(db, "proxmox", 1, ok=False, error="down")
    await db.commit()

    batch = await snap_svc.get_latest_batch(db, "proxmox")
    assert len(batch) == 2
    assert batch[1].ok is False
    assert batch[2].ok is True


async def test_get_history(db):
    for i in range(5):
        await snap_svc.save(db, "unifi", 10, ok=True, data={"i": i})
    await db.commit()

    history = await snap_svc.get_history(db, "unifi", 10, limit=3)
    assert len(history) == 3


async def test_save_with_error(db):
    snap = await snap_svc.save(db, "pihole", 5, ok=False, error="connection refused")
    await db.commit()

    latest = await snap_svc.get_latest(db, "pihole", 5)
    assert latest.ok is False
    assert "connection refused" in latest.error
