"""Tests for integration parser functions – pure functions, no HTTP mocking."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


# ── Proxmox ──────────────────────────────────────────────────────────────────

from integrations.proxmox import parse_cluster_data


def test_proxmox_parse_nodes():
    """Parse a single online node with CPU/mem/disk metrics."""
    resources = [{
        "type": "node", "node": "pve01", "status": "online",
        "cpu": 0.155, "mem": 8 * 1024**3, "maxmem": 32 * 1024**3,
        "disk": 50 * 1024**3, "maxdisk": 500 * 1024**3, "uptime": 7200,
    }]
    status = [{"type": "cluster", "name": "testcluster", "quorate": 1}]
    data = parse_cluster_data(resources, status)

    assert data["cluster_name"] == "testcluster"
    assert data["quorum_ok"] is True
    assert len(data["nodes"]) == 1

    node = data["nodes"][0]
    assert node["name"] == "pve01"
    assert node["online"] is True
    assert node["cpu_pct"] == 15.5
    assert node["mem_pct"] == 25.0
    assert node["uptime_h"] == 2.0


def test_proxmox_parse_vms_and_containers():
    """Parse mixed resource types (node + VM + LXC)."""
    resources = [
        {"type": "node", "node": "pve01", "status": "online",
         "cpu": 0.1, "mem": 1024**3, "maxmem": 4 * 1024**3,
         "disk": 0, "maxdisk": 100 * 1024**3, "uptime": 3600},
        {"type": "qemu", "vmid": 100, "name": "web-01", "node": "pve01",
         "status": "running", "cpu": 0.05,
         "mem": 512 * 1024**2, "maxmem": 2 * 1024**3,
         "disk": 10 * 1024**3, "maxdisk": 50 * 1024**3, "uptime": 1800},
        {"type": "lxc", "vmid": 200, "name": "dns-01", "node": "pve01",
         "status": "running", "cpu": 0.02,
         "mem": 256 * 1024**2, "maxmem": 1024**3,
         "disk": 5 * 1024**3, "maxdisk": 20 * 1024**3, "uptime": 900},
    ]
    status = [{"type": "cluster", "name": "lab", "quorate": 1}]
    data = parse_cluster_data(resources, status)

    assert len(data["nodes"]) == 1
    assert len(data["vms"]) == 1
    assert len(data["containers"]) == 1

    vm = data["vms"][0]
    assert vm["id"] == 100
    assert vm["name"] == "web-01"
    assert vm["running"] is True
    assert vm["type"] == "VM"

    ct = data["containers"][0]
    assert ct["id"] == 200
    assert ct["type"] == "LXC"


def test_proxmox_parse_empty():
    """Empty resources and status return sane defaults."""
    data = parse_cluster_data([], [])
    assert data["quorum_ok"] is True
    assert data["cluster_name"] == "Proxmox Cluster"
    assert data["nodes"] == []
    assert data["vms"] == []
    assert data["containers"] == []


# ── Pi-hole ──────────────────────────────────────────────────────────────────

from integrations.pihole import parse_pihole_data, parse_pihole_v6_data


def test_pihole_v5_parser():
    """Parse Pi-hole v5 API summary."""
    raw = {
        "dns_queries_today": "15234",
        "ads_blocked_today": "2400",
        "ads_percentage_today": "15.76",
        "domains_being_blocked": "450123",
        "unique_clients": "12",
        "status": "enabled",
        "dns_queries_all_types": "15234",
        "reply_NODATA": 100,
        "reply_CNAME": 500,
    }
    top_q = [{"domain": "google.com", "count": 850}]
    top_b = [{"domain": "ads.example.com", "count": 45}]
    data = parse_pihole_data(raw, top_q, top_b)

    assert data["queries_today"] == 15234
    assert data["blocked_today"] == 2400
    assert data["blocked_pct"] == 15.8
    assert data["domains_blocked"] == 450123
    assert data["clients"] == 12
    assert data["status"] == "enabled"
    assert data["api_version"] == 5
    assert "NODATA" in data["reply_types"]
    assert data["top_queries"] == top_q
    assert data["top_blocked"] == top_b


def test_pihole_v6_parser():
    """Parse Pi-hole v6 API summary."""
    raw = {
        "queries": {"total": 18000, "blocked": 2800, "percent_blocked": 15.56},
        "gravity": {"domains_being_blocked": 500000},
        "clients": {"unique": 15},
        "blocking": {"enabled": True},
    }
    top_q = [{"domain": "dns.google", "count": 200}]
    top_b = [{"domain": "tracker.example.com", "count": 30}]
    data = parse_pihole_v6_data(raw, top_q, top_b)

    assert data["queries_today"] == 18000
    assert data["blocked_today"] == 2800
    assert data["blocked_pct"] == 15.6
    assert data["domains_blocked"] == 500000
    assert data["clients"] == 15
    assert data["status"] == "enabled"
    assert data["api_version"] == 6


def test_pihole_v6_disabled():
    """Pi-hole v6 with blocking disabled."""
    raw = {
        "queries": {"total": 100, "blocked": 0, "percent_blocked": 0.0},
        "gravity": {"domains_being_blocked": 0},
        "clients": {"unique": 1},
        "blocking": {"enabled": False},
    }
    data = parse_pihole_v6_data(raw, [], [])
    assert data["status"] == "disabled"


# ── AdGuard ──────────────────────────────────────────────────────────────────

from integrations.adguard import parse_adguard_data


def test_adguard_parser():
    """Parse AdGuard Home stats + status."""
    stats = {
        "num_dns_queries": 20000,
        "num_blocked_filtering": 3000,
        "avg_processing_time": 0.003142,
        "top_queried_domains": [
            {"google.com": 500},
            {"github.com": 300},
        ],
        "top_blocked_domains": [
            {"ads.example.com": 100},
        ],
        "top_clients": [{"10.0.0.1": 5000}, {"10.0.0.2": 3000}],
    }
    status = {
        "running": True,
        "version": "v0.107.50",
        "protection_enabled": True,
        "safebrowsing_enabled": False,
        "parental_enabled": False,
    }
    data = parse_adguard_data(stats, status)

    assert data["queries_today"] == 20000
    assert data["blocked_today"] == 3000
    assert data["blocked_pct"] == 15.0
    assert data["avg_processing_time_ms"] == 3.142
    assert data["status"] == "running"
    assert data["version"] == "v0.107.50"
    assert data["filtering_enabled"] is True
    assert data["safebrowsing_enabled"] is False
    assert data["clients_today"] == 2
    assert len(data["top_queries"]) == 2
    assert data["top_queries"][0]["domain"] == "google.com"


def test_adguard_flexible_top_format():
    """AdGuard top items can come as {name, count} dicts."""
    stats = {
        "num_dns_queries": 100,
        "num_blocked_filtering": 10,
        "avg_processing_time": 0.001,
        "top_queried_domains": [
            {"name": "example.com", "count": 50},
        ],
        "top_blocked_domains": [],
        "top_clients": [],
    }
    status = {"running": True, "version": "v0.107.50"}
    data = parse_adguard_data(stats, status)

    assert data["top_queries"][0]["domain"] == "example.com"
    assert data["top_queries"][0]["count"] == 50


def test_adguard_zero_queries():
    """Zero queries should not cause division by zero."""
    stats = {
        "num_dns_queries": 0,
        "num_blocked_filtering": 0,
        "avg_processing_time": 0,
    }
    status = {"running": False, "version": "v0.107.50"}
    data = parse_adguard_data(stats, status)

    assert data["blocked_pct"] == 0.0
    assert data["status"] == "stopped"
