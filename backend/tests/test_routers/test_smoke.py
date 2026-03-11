"""Smoke tests – verify key routes return 200 and don't crash."""
import pytest
from unittest.mock import AsyncMock, patch


async def test_health(client):
    """Health endpoint bypasses middleware and returns ok."""
    with patch("main.AsyncSessionLocal") as mock_cls:
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_login_page(client):
    """Login page renders without auth."""
    resp = await client.get("/login")
    assert resp.status_code == 200
    assert "password" in resp.text.lower()


async def test_dashboard(client):
    """Dashboard renders with empty data."""
    resp = await client.get("/")
    assert resp.status_code == 200
    assert "NODEGLOW" in resp.text or "dashboard" in resp.text.lower()


async def test_ping_list(client):
    """Hosts list page renders (no hosts)."""
    resp = await client.get("/hosts")
    assert resp.status_code == 200


async def test_alerts_page(client):
    """Alerts page renders."""
    resp = await client.get("/alerts")
    assert resp.status_code == 200


async def test_syslog_page(client):
    """Syslog page renders."""
    resp = await client.get("/syslog")
    assert resp.status_code == 200


async def test_incidents_page(client):
    """Incidents redirects to unified alerts page."""
    resp = await client.get("/incidents", follow_redirects=False)
    assert resp.status_code == 302
    assert "alerts?tab=incidents" in resp.headers.get("location", "")


async def test_settings_page(client):
    """Settings page renders for admin user."""
    resp = await client.get("/settings")
    assert resp.status_code == 200


async def test_api_status(client):
    """Status API returns JSON list."""
    resp = await client.get("/hosts/api/status")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_integration_list(client):
    """Integration list page renders."""
    resp = await client.get("/integration/proxmox")
    assert resp.status_code == 200


async def test_unknown_integration_404(client):
    """Non-existent integration type returns 404."""
    resp = await client.get("/integration/nonexistent")
    assert resp.status_code == 404


# ── Subnet Scanner ──────────────────────────────────────────────────────────


async def test_subnet_scanner_page(client):
    """Subnet scanner page renders."""
    resp = await client.get("/subnet-scanner")
    assert resp.status_code == 200
    assert "subnet" in resp.text.lower() or "scanner" in resp.text.lower()


async def test_subnet_scanner_scan_invalid_cidr(client):
    """Scan with invalid CIDR returns 400."""
    resp = await client.post(
        "/api/subnet-scanner/scan",
        json={"cidr": "not-a-cidr"},
    )
    assert resp.status_code == 400
    assert "error" in resp.json()


async def test_subnet_scanner_scan_too_large(client):
    """Scan with a /8 returns 400 (too large)."""
    resp = await client.post(
        "/api/subnet-scanner/scan",
        json={"cidr": "10.0.0.0/8"},
    )
    assert resp.status_code == 400
    assert "too large" in resp.json()["error"].lower()


async def test_subnet_scanner_create_schedule(client):
    """Create and delete a scheduled scan."""
    resp = await client.post(
        "/api/subnet-scanner/schedules",
        json={"cidr": "192.168.1.0/28", "name": "Test", "interval_m": 60},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "id" in data

    # Delete it
    resp = await client.delete(f"/api/subnet-scanner/schedules/{data['id']}")
    assert resp.status_code == 200


async def test_subnet_scanner_schedule_min_interval(client):
    """Schedule with interval < 5 min returns 400."""
    resp = await client.post(
        "/api/subnet-scanner/schedules",
        json={"cidr": "192.168.1.0/24", "interval_m": 1},
    )
    assert resp.status_code == 400


# ── Hosts API ───────────────────────────────────────────────────────────────


async def test_hosts_search_empty(client):
    """Host search with empty query returns empty list."""
    resp = await client.get("/hosts/api/search?q=")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_hosts_search_short_query(client):
    """Host search with 1-char query returns empty (min 2 chars)."""
    resp = await client.get("/hosts/api/search?q=a")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_hosts_search_no_results(client):
    """Host search returns empty when no hosts match."""
    resp = await client.get("/hosts/api/search?q=nonexistent-host-xyz")
    assert resp.status_code == 200
    assert resp.json() == []


# ── Dashboard API ───────────────────────────────────────────────────────────


async def test_dashboard_renders(client):
    """Dashboard renders with empty database."""
    resp = await client.get("/")
    assert resp.status_code == 200


async def test_agents_page(client):
    """Agents page renders."""
    resp = await client.get("/agents")
    assert resp.status_code == 200


async def test_users_page(client):
    """Users page renders for admin."""
    resp = await client.get("/users")
    assert resp.status_code == 200
