"""AdGuard Home integration – queries DNS stats via the AdGuard Home API.

AdGuard Home uses HTTP Basic Auth.
Relevant endpoints:
  GET /control/stats   – query/block counts, top lists
  GET /control/status  – running state, version, protection status
"""
from __future__ import annotations

import base64

import httpx


class AdguardAPI:
    """Async client for the AdGuard Home REST API."""

    def __init__(self, host: str, username: str | None = None,
                 password: str | None = None, verify_ssl: bool = False):
        self.base     = host.rstrip("/")
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl

    def _auth_headers(self) -> dict:
        if self.username and self.password:
            cred = base64.b64encode(
                f"{self.username}:{self.password}".encode()
            ).decode()
            return {"Authorization": f"Basic {cred}"}
        return {}

    async def fetch_all(self) -> dict:
        """Fetch /control/stats and /control/status and return parsed data."""
        headers = self._auth_headers()
        async with httpx.AsyncClient(
            verify=self.verify_ssl,
            timeout=10.0,
            follow_redirects=True,
        ) as client:
            stats_resp = await client.get(
                f"{self.base}/control/stats",
                headers=headers,
            )
            stats_resp.raise_for_status()

            status_resp = await client.get(
                f"{self.base}/control/status",
                headers=headers,
            )
            status_resp.raise_for_status()

            return parse_adguard_data(stats_resp.json(), status_resp.json())

    async def health_check(self) -> bool:
        try:
            await self.fetch_all()
            return True
        except Exception:
            return False


# ── Parser ────────────────────────────────────────────────────────────────────


def parse_adguard_data(stats: dict, status: dict) -> dict:
    """
    Parse AdGuard Home /control/stats + /control/status into a structured dict.

    stats fields: num_dns_queries, num_blocked_filtering, avg_processing_time,
                  top_queries, top_blocked, num_replaced_safebrowsing,
                  num_replaced_parental
    status fields: running, version, protection_enabled, filtering_enabled,
                   safebrowsing_enabled, parental_enabled
    """
    queries_today  = int(stats.get("num_dns_queries", 0))
    blocked_today  = int(stats.get("num_blocked_filtering", 0))
    blocked_pct    = (
        round(blocked_today / queries_today * 100, 1)
        if queries_today > 0 else 0.0
    )
    avg_ms = round(float(stats.get("avg_processing_time", 0.0)) * 1000, 3)

    # top_queries is a list of {name: domain, count: N} dicts
    raw_top_q = stats.get("top_queried_domains") or stats.get("top_queries") or []
    raw_top_b = stats.get("top_blocked_domains") or stats.get("top_blocked") or []

    def _parse_top(raw: list) -> list:
        result = []
        for item in raw[:10]:
            if isinstance(item, dict):
                # Format: {domain: count}  or  {name: ..., count: ...}
                if "name" in item:
                    result.append({"domain": item["name"], "count": item.get("count", 0)})
                else:
                    for domain, count in item.items():
                        result.append({"domain": domain, "count": count})
        return result

    top_queries = _parse_top(raw_top_q)
    top_blocked = _parse_top(raw_top_b)

    # Count unique clients from top_clients list
    top_clients_raw = stats.get("top_clients") or []
    clients_today = len(top_clients_raw)

    running           = bool(status.get("running", False))
    version           = str(status.get("version", ""))
    protection_on     = bool(status.get("protection_enabled", True))
    filtering_enabled = bool(status.get("filtering_enabled", protection_on))
    safebrowsing      = bool(status.get("safebrowsing_enabled", False))
    parental          = bool(status.get("parental_enabled", False))

    svc_status = "running" if running else "stopped"

    return {
        "status":                svc_status,
        "version":               version,
        "queries_today":         queries_today,
        "blocked_today":         blocked_today,
        "blocked_pct":           blocked_pct,
        "avg_processing_time_ms": avg_ms,
        "top_queries":           top_queries,
        "top_blocked":           top_blocked,
        "clients_today":         clients_today,
        "filtering_enabled":     filtering_enabled,
        "safebrowsing_enabled":  safebrowsing,
        "parental_enabled":      parental,
        "num_replaced_safebrowsing": int(stats.get("num_replaced_safebrowsing", 0)),
        "num_replaced_parental":     int(stats.get("num_replaced_parental", 0)),
    }
