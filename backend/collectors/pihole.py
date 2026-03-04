"""Pi-hole integration – queries DNS stats via the Pi-hole API.

Supports Pi-hole v5 (query-param auth) and v6 (session-based auth).
v6 is tried first; falls back to v5 automatically.
"""
from __future__ import annotations

import httpx


class PiholeAPI:
    """Async client for the Pi-hole admin API (v5 and v6)."""

    def __init__(self, host: str, api_key: str | None = None,
                 verify_ssl: bool = False):
        self.base = host.rstrip("/")
        self.api_key = api_key
        self.verify_ssl = verify_ssl

    async def fetch_all(self) -> dict:
        """Try v6 session auth first, fall back to v5 query-param auth."""
        async with httpx.AsyncClient(
            verify=self.verify_ssl,
            timeout=10.0,
            follow_redirects=True,
        ) as client:
            # ── Try Pi-hole v6 ─────────────────────────────────────────────
            if self.api_key:
                try:
                    auth_resp = await client.post(
                        f"{self.base}/api/auth",
                        json={"password": self.api_key},
                    )
                    if auth_resp.status_code == 200:
                        auth_data = auth_resp.json()
                        session = auth_data.get("session", {})
                        sid = session.get("sid")
                        if sid:
                            headers = {"sid": sid}
                            stats_resp = await client.get(
                                f"{self.base}/api/stats/summary",
                                headers=headers,
                            )
                            stats_resp.raise_for_status()
                            raw = stats_resp.json()

                            # Also fetch top items
                            top_resp = await client.get(
                                f"{self.base}/api/stats/top_domains",
                                headers=headers,
                                params={"blocked": "false", "count": 10},
                            )
                            top_blocked_resp = await client.get(
                                f"{self.base}/api/stats/top_domains",
                                headers=headers,
                                params={"blocked": "true", "count": 10},
                            )

                            top_queries = []
                            top_blocked = []
                            if top_resp.status_code == 200:
                                top_data = top_resp.json()
                                domains = top_data.get("domains", [])
                                top_queries = [
                                    {"domain": d.get("domain", ""), "count": d.get("count", 0)}
                                    for d in domains[:10]
                                ]
                            if top_blocked_resp.status_code == 200:
                                blk_data = top_blocked_resp.json()
                                domains = blk_data.get("domains", [])
                                top_blocked = [
                                    {"domain": d.get("domain", ""), "count": d.get("count", 0)}
                                    for d in domains[:10]
                                ]

                            # Logout
                            try:
                                await client.delete(
                                    f"{self.base}/api/auth",
                                    headers=headers,
                                )
                            except Exception:
                                pass

                            return parse_pihole_v6_data(raw, top_queries, top_blocked)
                except Exception:
                    pass  # Fall through to v5

            # ── Try Pi-hole v5 ─────────────────────────────────────────────
            params: dict = {"summaryRaw": ""}
            if self.api_key:
                params["auth"] = self.api_key

            resp = await client.get(
                f"{self.base}/admin/api.php",
                params=params,
            )
            resp.raise_for_status()
            raw = resp.json()

            if not raw or "status" not in raw:
                raise ValueError(f"Unexpected Pi-hole v5 response: {raw}")

            # Fetch top queries and blocked
            top_queries = []
            top_blocked = []
            if self.api_key:
                tq_params = {"topItems": 10, "auth": self.api_key}
            else:
                tq_params = {"topItems": 10}
            try:
                tq_resp = await client.get(
                    f"{self.base}/admin/api.php",
                    params=tq_params,
                )
                if tq_resp.status_code == 200:
                    tq_data = tq_resp.json()
                    tq_raw = tq_data.get("top_queries") or {}
                    tb_raw = tq_data.get("top_ads") or {}
                    top_queries = [
                        {"domain": d, "count": c}
                        for d, c in sorted(tq_raw.items(), key=lambda x: -x[1])
                    ][:10]
                    top_blocked = [
                        {"domain": d, "count": c}
                        for d, c in sorted(tb_raw.items(), key=lambda x: -x[1])
                    ][:10]
            except Exception:
                pass

            return parse_pihole_data(raw, top_queries, top_blocked)

    async def health_check(self) -> bool:
        try:
            await self.fetch_all()
            return True
        except Exception:
            return False


# ── Parsers ───────────────────────────────────────────────────────────────────


def parse_pihole_data(raw: dict, top_queries: list, top_blocked: list) -> dict:
    """
    Parse Pi-hole v5 summaryRaw API response into a structured dict.

    Pi-hole v5 summaryRaw fields:
      domains_being_blocked, dns_queries_today, ads_blocked_today,
      ads_percentage_today, unique_clients, status, reply_*, gravity_last_updated
    """
    queries_today  = int(raw.get("dns_queries_today", 0))
    blocked_today  = int(raw.get("ads_blocked_today", 0))
    blocked_pct    = float(raw.get("ads_percentage_today", 0.0))
    domains_blocked = int(raw.get("domains_being_blocked", 0))
    clients        = int(raw.get("unique_clients", 0))
    status         = str(raw.get("status", "unknown"))

    reply_types = {}
    for key, val in raw.items():
        if key.startswith("reply_"):
            reply_types[key[6:]] = val

    gravity = raw.get("gravity_last_updated", {})
    gravity_str = ""
    if isinstance(gravity, dict):
        relative = gravity.get("relative", {})
        if relative:
            days  = relative.get("days", 0)
            hours = relative.get("hours", 0)
            mins  = relative.get("minutes", 0)
            gravity_str = f"{days}d {hours}h {mins}m ago"
        elif gravity.get("absolute"):
            import datetime
            ts = gravity["absolute"]
            try:
                gravity_str = datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
            except Exception:
                gravity_str = str(ts)

    return {
        "status":              status,
        "queries_today":       queries_today,
        "blocked_today":       blocked_today,
        "blocked_pct":         round(blocked_pct, 1),
        "domains_blocked":     domains_blocked,
        "dns_queries_all_types": int(raw.get("dns_queries_all_types", 0)),
        "reply_types":         reply_types,
        "top_queries":         top_queries,
        "top_blocked":         top_blocked,
        "clients":             clients,
        "gravity_last_updated": gravity_str,
        "api_version":         5,
    }


def parse_pihole_v6_data(raw: dict, top_queries: list, top_blocked: list) -> dict:
    """
    Parse Pi-hole v6 /api/stats/summary response into a structured dict.

    v6 summary fields: queries.total, queries.blocked, queries.percent_blocked,
    gravity.domains_being_blocked, clients.unique, status
    """
    queries    = raw.get("queries", {})
    gravity    = raw.get("gravity", {})
    clients    = raw.get("clients", {})

    queries_today   = int(queries.get("total", 0))
    blocked_today   = int(queries.get("blocked", 0))
    blocked_pct     = float(queries.get("percent_blocked", 0.0))
    domains_blocked = int(gravity.get("domains_being_blocked", 0))
    unique_clients  = int(clients.get("unique", 0))
    status          = "enabled" if raw.get("blocking", {}).get("enabled", True) else "disabled"

    return {
        "status":              status,
        "queries_today":       queries_today,
        "blocked_today":       blocked_today,
        "blocked_pct":         round(blocked_pct, 1),
        "domains_blocked":     domains_blocked,
        "dns_queries_all_types": queries_today,
        "reply_types":         {},
        "top_queries":         top_queries,
        "top_blocked":         top_blocked,
        "clients":             unique_clients,
        "gravity_last_updated": "",
        "api_version":         6,
    }
