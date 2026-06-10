"""Tests for /api/v1/ endpoints — key routes for external API."""


# ── System Status ────────────────────────────────────────────────────────────


async def test_api_status(client):
    """GET /api/v1/status returns system overview."""
    resp = await client.get("/api/v1/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "hosts" in data
    assert "agents" in data
    assert "integrations" in data
    assert "incidents" in data
    assert data["hosts"]["total"] >= 0


# ── Hosts ────────────────────────────────────────────────────────────────────


async def test_list_hosts_empty(client):
    """GET /api/v1/hosts returns empty list when no hosts exist."""
    resp = await client.get("/api/v1/hosts")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
    assert len(resp.json()) == 0


async def test_create_host(client):
    """POST /api/v1/hosts creates a new host."""
    resp = await client.post("/api/v1/hosts", json={
        "name": "test-host",
        "hostname": "192.168.1.100",
        "check_type": "icmp",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "id" in data
    assert data["name"] == "test-host"


async def test_create_host_missing_hostname(client):
    """POST /api/v1/hosts rejects missing hostname."""
    resp = await client.post("/api/v1/hosts", json={
        "name": "test-host",
    })
    assert resp.status_code == 400


async def test_host_crud_lifecycle(client):
    """Create, read, update, delete a host via API."""
    # Create
    create_resp = await client.post("/api/v1/hosts", json={
        "name": "lifecycle-host",
        "hostname": "10.0.0.1",
        "check_type": "icmp",
    })
    assert create_resp.status_code == 200
    host_id = create_resp.json()["id"]

    # Read
    get_resp = await client.get(f"/api/v1/hosts/{host_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["name"] == "lifecycle-host"

    # Update
    patch_resp = await client.patch(f"/api/v1/hosts/{host_id}", json={
        "name": "updated-host",
    })
    assert patch_resp.status_code == 200

    # Verify update
    get_resp2 = await client.get(f"/api/v1/hosts/{host_id}")
    assert get_resp2.json()["name"] == "updated-host"

    # Delete
    del_resp = await client.delete(f"/api/v1/hosts/{host_id}")
    assert del_resp.status_code == 200

    # Verify deleted
    get_resp3 = await client.get(f"/api/v1/hosts/{host_id}")
    assert get_resp3.status_code == 404


async def test_host_not_found(client):
    """GET /api/v1/hosts/99999 returns 404."""
    resp = await client.get("/api/v1/hosts/99999")
    assert resp.status_code == 404


async def test_host_timeline_not_found(client):
    """GET /api/v1/hosts/99999/timeline returns 404 for missing host."""
    resp = await client.get("/api/v1/hosts/99999/timeline")
    assert resp.status_code == 404


async def test_host_timeline_empty_sources(client):
    """Timeline returns empty event list when CH mocks return nothing."""
    create = await client.post("/api/v1/hosts", json={
        "name": "timeline-host",
        "hostname": "10.0.0.42",
        "check_type": "icmp",
    })
    host_id = create.json()["id"]

    resp = await client.get(f"/api/v1/hosts/{host_id}/timeline?hours=24")
    assert resp.status_code == 200
    body = resp.json()
    assert body["host_id"] == host_id
    assert body["host_name"] == "timeline-host"
    assert body["hours"] == 24
    assert body["events"] == []
    assert set(body["sources"]) == {"status", "incident", "syslog"}


async def test_host_timeline_source_filter(client):
    """sources=status only disables incident + syslog queries."""
    create = await client.post("/api/v1/hosts", json={
        "name": "filter-host",
        "hostname": "10.0.0.43",
        "check_type": "icmp",
    })
    host_id = create.json()["id"]

    resp = await client.get(f"/api/v1/hosts/{host_id}/timeline?sources=status")
    assert resp.status_code == 200
    assert resp.json()["sources"] == ["status"]


async def test_host_timeline_hours_validation(client):
    """Lookback window is clamped to 1..720 hours."""
    create = await client.post("/api/v1/hosts", json={
        "name": "clamp-host",
        "hostname": "10.0.0.44",
        "check_type": "icmp",
    })
    host_id = create.json()["id"]

    resp = await client.get(f"/api/v1/hosts/{host_id}/timeline?hours=0")
    assert resp.status_code == 422
    resp = await client.get(f"/api/v1/hosts/{host_id}/timeline?hours=9999")
    assert resp.status_code == 422


async def test_list_hosts_filter_enabled(client):
    """GET /api/v1/hosts?enabled=true filters correctly."""
    resp = await client.get("/api/v1/hosts?enabled=true")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ── Agents ───────────────────────────────────────────────────────────────────


async def test_list_agents_empty(client):
    """GET /api/v1/agents returns empty list."""
    resp = await client.get("/api/v1/agents")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_agent_not_found(client):
    """GET /api/v1/agents/99999 returns 404."""
    resp = await client.get("/api/v1/agents/99999")
    assert resp.status_code == 404


# ── Integrations ─────────────────────────────────────────────────────────────


async def test_list_integrations_empty(client):
    """GET /api/v1/integrations returns empty list."""
    resp = await client.get("/api/v1/integrations")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_integration_not_found(client):
    """GET /api/v1/integrations/99999 returns 404."""
    resp = await client.get("/api/v1/integrations/99999")
    assert resp.status_code == 404


# ── Incidents ────────────────────────────────────────────────────────────────


async def test_list_incidents_empty(client):
    """GET /api/v1/incidents returns empty list."""
    resp = await client.get("/api/v1/incidents")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 0


async def test_list_incidents_filter_status(client):
    """GET /api/v1/incidents?status=open filters correctly."""
    resp = await client.get("/api/v1/incidents?status=open")
    assert resp.status_code == 200


async def test_incident_not_found(client):
    """GET /api/v1/incidents/99999 returns 404."""
    resp = await client.get("/api/v1/incidents/99999")
    assert resp.status_code == 404


async def test_list_incidents_summary_is_latest_meaningful_event(client):
    """The list summary is the newest event, excluding ack/resolve markers.

    Characterization for the greatest-per-group lookup: incidents with many
    events must surface exactly the most recent non-system event summary.
    """
    from datetime import datetime, timedelta

    from database import AsyncSessionLocal
    from models.incident import Incident, IncidentEvent

    now = datetime.utcnow()
    async with AsyncSessionLocal() as s:
        inc = Incident(rule="host_down_syslog", title="host down", severity="critical", status="open")
        other = Incident(rule="syslog_spike", title="spike", severity="warning", status="open")
        s.add_all([inc, other])
        await s.flush()
        s.add_all([
            IncidentEvent(incident_id=inc.id, event_type="created",
                          summary="first event", timestamp=now - timedelta(minutes=10)),
            IncidentEvent(incident_id=inc.id, event_type="host_down",
                          summary="newest meaningful event", timestamp=now - timedelta(minutes=2)),
            # System markers must never win, even when they are newest.
            IncidentEvent(incident_id=inc.id, event_type="acknowledged",
                          summary="acked by julian", timestamp=now - timedelta(minutes=1)),
            IncidentEvent(incident_id=other.id, event_type="created",
                          summary="other incident event", timestamp=now - timedelta(minutes=5)),
        ])
        await s.commit()
        inc_id, other_id = inc.id, other.id

    resp = await client.get("/api/v1/incidents")
    assert resp.status_code == 200
    by_id = {i["id"]: i for i in resp.json()}
    assert by_id[inc_id]["summary"] == "newest meaningful event"
    assert by_id[other_id]["summary"] == "other incident event"


# ── Syslog ───────────────────────────────────────────────────────────────────


async def test_syslog_query(client):
    """GET /api/v1/syslog returns results (mocked ClickHouse)."""
    resp = await client.get("/api/v1/syslog")
    assert resp.status_code == 200
    data = resp.json()
    assert "messages" in data or isinstance(data, list)


# ── API Keys ─────────────────────────────────────────────────────────────────


async def test_list_api_keys(client):
    """GET /api/v1/keys returns list."""
    resp = await client.get("/api/v1/keys")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_create_and_delete_api_key(client):
    """POST + DELETE /api/v1/keys lifecycle."""
    # Create
    resp = await client.post("/api/v1/keys", json={
        "name": "test-key",
        "role": "readonly",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "key" in data
    assert data["key"].startswith("ng_")
    key_id = data["id"]

    # List and verify
    list_resp = await client.get("/api/v1/keys")
    assert any(k["id"] == key_id for k in list_resp.json())

    # Delete
    del_resp = await client.delete(f"/api/v1/keys/{key_id}")
    assert del_resp.status_code == 200


async def test_create_api_key_invalid_role(client):
    """POST /api/v1/keys rejects invalid role."""
    resp = await client.post("/api/v1/keys", json={
        "name": "bad-key",
        "role": "superadmin",
    })
    assert resp.status_code == 400


async def test_create_api_key_missing_name(client):
    """POST /api/v1/keys rejects empty name."""
    resp = await client.post("/api/v1/keys", json={
        "name": "",
        "role": "readonly",
    })
    assert resp.status_code == 400


# ── Audit Log ────────────────────────────────────────────────────────────────


async def test_audit_log(client):
    """GET /api/v1/audit returns paginated results."""
    resp = await client.get("/api/v1/audit")
    assert resp.status_code == 200
    data = resp.json()
    assert "logs" in data or "items" in data or isinstance(data, list)


# ── Backup ───────────────────────────────────────────────────────────────────


async def test_backup_info(client):
    """GET /api/v1/backup/info returns table stats."""
    resp = await client.get("/api/v1/backup/info")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_rows" in data or "tables" in data
