"""
Proxmox VE integration – fetches cluster health, node metrics, and VM/LXC status
via the Proxmox REST API using API token authentication.
"""
from __future__ import annotations

import httpx


class ProxmoxAPI:
    """Thin async client for the Proxmox REST API."""

    def __init__(self, host: str, token_id: str, token_secret: str, verify_ssl: bool = False):
        # Normalize host: strip trailing slash, ensure no /api2 suffix
        self.base = host.rstrip("/")
        self._headers = {"Authorization": f"PVEAPIToken={token_id}={token_secret}"}
        self._verify_ssl = verify_ssl

    async def get(self, path: str) -> list | dict:
        url = f"{self.base}/api2/json{path}"
        async with httpx.AsyncClient(verify=self._verify_ssl, timeout=10.0) as client:
            resp = await client.get(url, headers=self._headers)
            resp.raise_for_status()
            return resp.json().get("data", [])

    async def cluster_status(self) -> list[dict]:
        """Returns cluster member objects (type: cluster/node)."""
        return await self.get("/cluster/status")

    async def cluster_resources(self) -> list[dict]:
        """Returns all cluster resources: nodes, VMs (qemu), and containers (lxc)."""
        return await self.get("/cluster/resources")

    async def health_check(self) -> bool:
        try:
            await self.cluster_status()
            return True
        except Exception:
            return False


def parse_cluster_data(resources: list[dict], cluster_status: list[dict]) -> dict:
    """
    Parse raw Proxmox API responses into a structured summary dict.

    Returns:
    {
      "quorum_ok": bool,
      "cluster_name": str,
      "nodes": [...],
      "vms": [...],
      "containers": [...],
      "totals": { "nodes_total", "nodes_online", "vms_total", "vms_running",
                  "lxc_total", "lxc_running", "cpu_used", "cpu_total",
                  "mem_used_gb", "mem_total_gb" }
    }
    """
    # Quorum / cluster name from cluster/status
    quorum_ok = True
    cluster_name = "Proxmox Cluster"
    for item in cluster_status:
        if item.get("type") == "cluster":
            quorum_ok = bool(item.get("quorate", 1))
            cluster_name = item.get("name", cluster_name)

    nodes = []
    vms = []
    containers = []

    for r in resources:
        rtype = r.get("type")

        if rtype == "node":
            cpu_pct = round((r.get("cpu") or 0) * 100, 1)
            mem_used = r.get("mem") or 0
            mem_total = r.get("maxmem") or 1
            mem_pct = round(mem_used / mem_total * 100, 1)
            uptime_s = r.get("uptime") or 0
            nodes.append({
                "name": r.get("node", r.get("name", "?")),
                "status": r.get("status", "unknown"),
                "online": r.get("status") == "online",
                "cpu_pct": cpu_pct,
                "mem_pct": mem_pct,
                "mem_used_gb": round(mem_used / 1024**3, 1),
                "mem_total_gb": round(mem_total / 1024**3, 1),
                "uptime_h": round(uptime_s / 3600, 1),
            })

        elif rtype == "qemu":
            cpu_pct = round((r.get("cpu") or 0) * 100, 1)
            mem_used = r.get("mem") or 0
            mem_total = r.get("maxmem") or 1
            vms.append({
                "id": r.get("vmid"),
                "name": r.get("name", f"vm-{r.get('vmid')}"),
                "node": r.get("node", "?"),
                "status": r.get("status", "unknown"),
                "running": r.get("status") == "running",
                "cpu_pct": cpu_pct,
                "mem_used_gb": round(mem_used / 1024**3, 2),
                "mem_total_gb": round(mem_total / 1024**3, 2),
                "uptime_h": round((r.get("uptime") or 0) / 3600, 1),
            })

        elif rtype == "lxc":
            cpu_pct = round((r.get("cpu") or 0) * 100, 1)
            mem_used = r.get("mem") or 0
            mem_total = r.get("maxmem") or 1
            containers.append({
                "id": r.get("vmid"),
                "name": r.get("name", f"ct-{r.get('vmid')}"),
                "node": r.get("node", "?"),
                "status": r.get("status", "unknown"),
                "running": r.get("status") == "running",
                "cpu_pct": cpu_pct,
                "mem_used_gb": round(mem_used / 1024**3, 2),
                "mem_total_gb": round(mem_total / 1024**3, 2),
                "uptime_h": round((r.get("uptime") or 0) / 3600, 1),
            })

    # Sort
    nodes.sort(key=lambda n: n["name"])
    vms.sort(key=lambda v: (v["node"], v["name"]))
    containers.sort(key=lambda c: (c["node"], c["name"]))

    # Totals
    online_nodes = [n for n in nodes if n["online"]]
    cpu_used = sum(n["cpu_pct"] for n in online_nodes)
    mem_used_gb = sum(n["mem_used_gb"] for n in online_nodes)
    mem_total_gb = sum(n["mem_total_gb"] for n in online_nodes)

    totals = {
        "nodes_total": len(nodes),
        "nodes_online": len(online_nodes),
        "vms_total": len(vms),
        "vms_running": sum(1 for v in vms if v["running"]),
        "lxc_total": len(containers),
        "lxc_running": sum(1 for c in containers if c["running"]),
        "cpu_avg_pct": round(cpu_used / len(online_nodes), 1) if online_nodes else 0,
        "mem_used_gb": round(mem_used_gb, 1),
        "mem_total_gb": round(mem_total_gb, 1),
        "mem_pct": round(mem_used_gb / mem_total_gb * 100, 1) if mem_total_gb else 0,
    }

    return {
        "quorum_ok": quorum_ok,
        "cluster_name": cluster_name,
        "nodes": nodes,
        "vms": vms,
        "containers": containers,
        "totals": totals,
    }
