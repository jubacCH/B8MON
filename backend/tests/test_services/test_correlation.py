"""Tests for the correlation engine helpers."""
from datetime import datetime

from models.incident import Incident, IncidentEvent
from services.correlation import _host_ids_hash, _find_or_create_incident


async def test_host_ids_hash_deterministic():
    h1 = _host_ids_hash([1, 2, 3])
    h2 = _host_ids_hash([3, 1, 2])
    assert h1 == h2


async def test_host_ids_hash_different_for_different_ids():
    h1 = _host_ids_hash([1, 2])
    h2 = _host_ids_hash([1, 3])
    assert h1 != h2


async def test_find_or_create_new_incident(db):
    inc = await _find_or_create_incident(
        db,
        rule="test_rule",
        title="Test incident",
        severity="warning",
        host_ids=[1],
        event_type="created",
        summary="Host 1 is down",
    )
    await db.commit()

    assert inc.id is not None
    assert inc.rule == "test_rule"
    assert inc.status == "open"


async def test_find_or_create_dedup(db):
    inc1 = await _find_or_create_incident(
        db,
        rule="test_rule",
        title="Test incident",
        severity="warning",
        host_ids=[1],
        event_type="created",
        summary="First trigger",
    )
    await db.commit()

    inc2 = await _find_or_create_incident(
        db,
        rule="test_rule",
        title="Test incident",
        severity="warning",
        host_ids=[1],
        event_type="host_down",
        summary="Second trigger",
    )
    await db.commit()

    # Same incident should be returned (dedup by rule + host_ids_hash)
    assert inc1.id == inc2.id


async def test_find_or_create_different_hosts_creates_new(db):
    inc1 = await _find_or_create_incident(
        db, rule="r", title="t", severity="warning",
        host_ids=[1], event_type="created", summary="s1",
    )
    await db.commit()

    inc2 = await _find_or_create_incident(
        db, rule="r", title="t", severity="warning",
        host_ids=[2], event_type="created", summary="s2",
    )
    await db.commit()

    assert inc1.id != inc2.id
