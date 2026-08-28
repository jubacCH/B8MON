"""Proxmox VE integration – cluster health, node metrics, VM/LXC status."""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)

from integrations._base import Alert, BaseIntegration, CollectorResult, ConfigField


# ── API Client ────────────────────────────────────────────────────────────────


_NET_IP_RE = re.compile(r"\bip=([0-9]{1,3}(?:\.[0-9]{1,3}){3})/")


def extract_ipv4(net_config: str | None) -> str | None:
    """Pull the static IPv4 out of a Proxmox netN line.

    Reads e.g. "name=eth0,bridge=vmbr0,ip=10.10.30.70/24,..." -> "10.10.30.70".
    Returns None for DHCP or anything unparseable, leaving the caller to fall
    back to the hostname.
    """
    if not net_config:
        return None
    match = _NET_IP_RE.search(net_config)
    return match.group(1) if match else None


class ProxmoxAPI:
    """Thin async client for the Proxmox REST API."""

    def __init__(self, host: str, token_id: str, token_secret: str, verify_ssl: bool = True):
        self.base = host.rstrip("/")
        self._headers = {"Authorization": f"PVEAPIToken={token_id}={token_secret}"}
        self._verify_ssl = verify_ssl

    async def get(self, path: str) -> list | dict:
        url = f"{self.base}/api2/json{path}"
        async with httpx.AsyncClient(verify=self._verify_ssl, timeout=10.0) as client:
            resp = await client.get(url, headers=self._headers)
            resp.raise_for_status()
            return resp.json().get("data", [])

    async def post(self, path: str, data: dict | None = None) -> dict:
        url = f"{self.base}/api2/json{path}"
        async with httpx.AsyncClient(verify=self._verify_ssl, timeout=30.0) as client:
            resp = await client.post(url, headers=self._headers, data=data or {})
            resp.raise_for_status()
            return resp.json().get("data", {})

    async def cluster_status(self) -> list[dict]:
        return await self.get("/cluster/status")

    async def cluster_resources(self) -> list[dict]:
        return await self.get("/cluster/resources")

    async def vm_config(self, node: str, vmid: int) -> dict:
        try:
            return await self.get(f"/nodes/{node}/qemu/{vmid}/config")
        except Exception as exc:
            logger.debug("Failed to fetch VM config node=%s vmid=%d: %s", node, vmid, exc)
            return {}

    async def lxc_config(self, node: str, ctid: int) -> dict:
        try:
            return await self.get(f"/nodes/{node}/lxc/{ctid}/config")
        except Exception as exc:
            logger.debug("Failed to fetch LXC config node=%s ctid=%d: %s", node, ctid, exc)
            return {}

    async def fetch_guest_network(self, guests: list[dict]) -> dict[int, dict]:
        """Static IPv4 and MAC per guest, read from its config.

        Only containers are consulted: their config carries the address directly.
        A VM would need the guest agent, which is often absent, so those keep
        falling back to the hostname.

        Discovery needs the address because a guest name only pings correctly
        where internal names resolve internally. Without split-horizon DNS the
        name resolves to the site's public IP, the check fails on NAT hairpin,
        and a perfectly healthy host reads as down.
        """
        containers = [g for g in guests if g.get("type") != "VM"]
        if not containers:
            return {}

        _mac_re = re.compile(r"([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})", re.I)

        async def _one(g):
            vmid = g.get("id") or g.get("vmid")
            node = g.get("node", "")
            mac = ip = None
            try:
                cfg = await self.lxc_config(node, vmid)
                for key in sorted(cfg.keys()):
                    if not key.startswith("net"):
                        continue
                    val = str(cfg[key])
                    if mac is None:
                        m = _mac_re.search(val)
                        if m:
                            mac = m.group(1).upper()
                    if ip is None:
                        ip = extract_ipv4(val)
                    if mac and ip:
                        break
            except Exception:  # noqa: BLE001 — one bad guest must not stop the rest
                pass
            return vmid, {"mac": mac, "ip": ip}

        results = await asyncio.gather(*[_one(g) for g in containers])
        return {vmid: info for vmid, info in results if info.get("mac") or info.get("ip")}

    async def _unused_fetch_guest_macs(self, guests: list[dict]) -> dict[int, str]:
        _mac_re = re.compile(r"([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})", re.I)

        async def _one(g):
            vmid = g.get("id") or g.get("vmid")
            node = g.get("node", "")
            gtype = g.get("type", "VM")
            try:
                cfg = await (self.vm_config(node, vmid) if gtype == "VM"
                             else self.lxc_config(node, vmid))
                for key in sorted(cfg.keys()):
                    if not key.startswith("net"):
                        continue
                    val = str(cfg[key])
                    m = _mac_re.search(val)
                    if m:
                        return vmid, m.group(1).upper()
            except Exception:
                pass
            return vmid, None

        results = await asyncio.gather(*[_one(g) for g in guests])
        return {vmid: mac for vmid, mac in results if mac}

    async def cluster_tasks(self) -> list[dict]:
        """Fetch recent cluster-wide tasks."""
        try:
            return await self.get("/cluster/tasks")
        except Exception as exc:
            logger.debug("Failed to fetch cluster tasks: %s", exc)
            return []

    async def node_storages(self, node: str) -> list[dict]:
        """Fetch storage list for a node."""
        try:
            return await self.get(f"/nodes/{node}/storage")
        except Exception as exc:
            logger.debug("Failed to fetch storages for node %s: %s", node, exc)
            return []

    async def storage_backup_content(self, node: str, storage: str) -> list[dict]:
        """Fetch backup files from a specific storage on a node."""
        try:
            return await self.get(f"/nodes/{node}/storage/{storage}/content?content=backup")
        except Exception as exc:
            logger.debug("Failed to fetch backup content %s/%s: %s", node, storage, exc)
            return []

    async def fetch_backup_data(self, resources: list[dict]) -> dict[str, list[dict]]:
        """
        For each node, find backup-capable storages and fetch their backup file list.
        Returns {storage_name: [backup_file_entries]}.
        """
        nodes = [r.get("node") or r.get("name") for r in resources if r.get("type") == "node"]
        if not nodes:
            return {}

        # Fetch storages from the first available node (storage list is cluster-wide)
        storages = []
        for node in nodes:
            storages = await self.node_storages(node)
            if storages:
                break

        # Find backup-capable storages (content includes "backup")
        backup_storages: list[str] = []
        for s in storages:
            content = s.get("content", "")
            if "backup" in content:
                backup_storages.append(s.get("storage", ""))

        if not backup_storages:
            return {}

        # Fetch backup content from each storage on each node
        all_backups: dict[str, list[dict]] = {}
        seen_volids: set[str] = set()

        async def _fetch(node: str, storage: str):
            files = await self.storage_backup_content(node, storage)
            for f in files:
                volid = f.get("volid", "")
                if volid and volid not in seen_volids:
                    seen_volids.add(volid)
                    all_backups.setdefault(storage, []).append(f)

        tasks = [_fetch(node, storage) for node in nodes for storage in backup_storages]
        await asyncio.gather(*tasks)

        return all_backups

    async def health_check(self) -> bool:
        try:
            await self.cluster_status()
            return True
        except Exception:
            return False


# ── Parser ────────────────────────────────────────────────────────────────────


def parse_cluster_data(resources: list[dict], cluster_status: list[dict],
                       tasks: list[dict] | None = None) -> dict:
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
            disk_used = r.get("disk") or 0
            disk_total = r.get("maxdisk") or 1
            uptime_s = r.get("uptime") or 0
            nodes.append({
                "name": r.get("node", r.get("name", "?")),
                "status": r.get("status", "unknown"),
                "online": r.get("status") == "online",
                "cpu_pct": cpu_pct,
                "mem_pct": mem_pct,
                "mem_used_gb": round(mem_used / 1024**3, 1),
                "mem_total_gb": round(mem_total / 1024**3, 1),
                "disk_used_gb": round(disk_used / 1024**3, 1),
                "disk_total_gb": round(disk_total / 1024**3, 1),
                "disk_pct": round(disk_used / disk_total * 100, 1) if disk_total else 0,
                "netin": r.get("netin") or 0,
                "netout": r.get("netout") or 0,
                "uptime_s": uptime_s,
            })

        elif rtype == "qemu":
            cpu_pct = round((r.get("cpu") or 0) * 100, 1)
            mem_used = r.get("mem") or 0
            mem_total = r.get("maxmem") or 1
            disk_used = r.get("disk") or 0
            disk_total = r.get("maxdisk") or 1
            vms.append({
                "id": r.get("vmid"),
                "name": r.get("name", f"vm-{r.get('vmid')}"),
                "node": r.get("node", "?"),
                "status": r.get("status", "unknown"),
                "running": r.get("status") == "running",
                "cpu_pct": cpu_pct,
                "mem_used_gb": round(mem_used / 1024**3, 2),
                "mem_total_gb": round(mem_total / 1024**3, 2),
                "disk_used_gb": round(disk_used / 1024**3, 2),
                "disk_total_gb": round(disk_total / 1024**3, 2),
                "disk_pct": round(disk_used / disk_total * 100, 1) if disk_total else 0,
                "netin": r.get("netin") or 0,
                "netout": r.get("netout") or 0,
                "diskread": r.get("diskread") or 0,
                "diskwrite": r.get("diskwrite") or 0,
                "uptime_s": r.get("uptime") or 0,
                "type": "VM",
            })

        elif rtype == "lxc":
            cpu_pct = round((r.get("cpu") or 0) * 100, 1)
            mem_used = r.get("mem") or 0
            mem_total = r.get("maxmem") or 1
            disk_used = r.get("disk") or 0
            disk_total = r.get("maxdisk") or 1
            containers.append({
                "id": r.get("vmid"),
                "name": r.get("name", f"ct-{r.get('vmid')}"),
                "node": r.get("node", "?"),
                "status": r.get("status", "unknown"),
                "running": r.get("status") == "running",
                "cpu_pct": cpu_pct,
                "mem_used_gb": round(mem_used / 1024**3, 2),
                "mem_total_gb": round(mem_total / 1024**3, 2),
                "disk_used_gb": round(disk_used / 1024**3, 2),
                "disk_total_gb": round(disk_total / 1024**3, 2),
                "disk_pct": round(disk_used / disk_total * 100, 1) if disk_total else 0,
                "netin": r.get("netin") or 0,
                "netout": r.get("netout") or 0,
                "diskread": r.get("diskread") or 0,
                "diskwrite": r.get("diskwrite") or 0,
                "uptime_s": r.get("uptime") or 0,
                "type": "LXC",
            })

    nodes.sort(key=lambda n: n["name"])
    vms.sort(key=lambda v: (v["node"], v["name"]))
    containers.sort(key=lambda c: (c["node"], c["name"]))

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

    # Parse tasks
    parsed_tasks = []
    for t in (tasks or []):
        starttime = t.get("starttime") or 0
        endtime = t.get("endtime")
        status = t.get("status", "")
        # Store as naive UTC datetime strings — localtime filter handles tz
        start_dt = datetime.utcfromtimestamp(starttime) if starttime else None
        parsed_tasks.append({
            "starttime": start_dt.strftime("%Y-%m-%d %H:%M:%S") if start_dt else None,
            "_sort": starttime,
            "node": t.get("node", ""),
            "user": t.get("user", ""),
            "type": t.get("type", ""),
            "id": t.get("id", ""),
            "status": status if status else "running",
            "ok": status == "OK" if status else None,
        })
    parsed_tasks.sort(key=lambda x: x["_sort"], reverse=True)
    for t in parsed_tasks:
        del t["_sort"]

    return {
        "quorum_ok": quorum_ok,
        "cluster_name": cluster_name,
        "nodes": nodes,
        "vms": vms,
        "containers": containers,
        "totals": totals,
        "tasks": parsed_tasks,
    }


# ── Host Import ───────────────────────────────────────────────────────────────


async def import_proxmox_hosts(cluster_name: str, data: dict, db) -> dict:
    from models.ping import PingHost
    from sqlalchemy import select

    existing_q = await db.execute(select(PingHost))
    existing_all: list[PingHost] = existing_q.scalars().all()
    by_hostname: dict[str, PingHost] = {h.hostname: h for h in existing_all}
    # Index proxmox-sourced hosts by source_detail for stable VMID matching
    by_source_key: dict[str, PingHost] = {}
    for h in existing_all:
        if h.source == "proxmox" and h.source_detail and ":" in h.source_detail:
            by_source_key[h.source_detail] = h

    # Import nodes (physical hosts) + VMs + LXCs
    all_entries = []
    for node in data.get("nodes", []):
        all_entries.append({
            **node,
            "running": node.get("online", False),
            "_is_node": True,
        })
    for g in data.get("vms", []) + data.get("containers", []):
        all_entries.append(g)

    added = merged = skipped = 0
    dirty = False

    for g in all_entries:
        hostname = (g.get("name") or "").strip()
        if not hostname:
            skipped += 1
            continue

        # Build stable source key: "cluster:vmid" for VMs/LXCs, "cluster:node:name" for nodes
        vmid = g.get("id")
        if vmid and not g.get("_is_node"):
            source_key = f"{cluster_name}:{vmid}"
        else:
            source_key = f"{cluster_name}:node:{hostname}"

        # Match by stable source_key first, fall back to hostname
        host = by_source_key.get(source_key) or by_hostname.get(hostname)

        if host:
            changed = False
            if host.source == "manual":
                host.source = "proxmox"
                changed = True
            # Update name if it changed in Proxmox
            if host.source == "proxmox" and host.name != hostname:
                logger.info("Proxmox rename: %s → %s", host.name, hostname)
                host.name = hostname
                host.hostname = hostname
                changed = True
            # The guest config is the authoritative address for a guest we
            # discovered, so it overwrites whatever is stored — it does not
            # merely fill a blank.
            #
            # Only backfilling was not enough. A separate DNS pass also sets
            # ip_address, and whichever ran first won. Where a guest name
            # resolves publicly, that pass stored the site's WAN address, and
            # the check then measured the wrong machine: two healthy containers
            # read as down on NAT hairpin, and one whose name sits behind a CDN
            # read as *up* because the CDN edge answered the ping. A false green
            # on a monitoring product is the worse half of that.
            if host.source == "proxmox" and g.get("ip") and host.ip_address != g["ip"]:
                logger.info(
                    "Proxmox address: %s %s → %s (guest config)",
                    host.name, host.ip_address or "unset", g["ip"],
                )
                host.ip_address = g["ip"]
                changed = True

            # Store stable source_key for future matching
            if host.source_detail != source_key:
                host.source_detail = source_key
                changed = True
            if changed:
                dirty = True
            merged += 1
        else:
            new_host = PingHost(
                name=hostname,
                hostname=hostname,
                ip_address=g.get("ip") or None,
                check_type="icmp",
                enabled=g.get("running", False),
                source="proxmox",
                source_detail=source_key,
            )
            db.add(new_host)
            by_hostname[hostname] = new_host
            by_source_key[source_key] = new_host
            added += 1
            dirty = True

    if dirty:
        await db.commit()

    return {"added": added, "merged": merged, "skipped": skipped}


# ── Integration Plugin ────────────────────────────────────────────────────────


class ProxmoxIntegration(BaseIntegration):
    name = "proxmox"
    display_name = "Proxmox VE"
    icon = "proxmox"
    color = "orange"
    description = "Monitor Proxmox VE clusters via the REST API."

    config_fields = [
        ConfigField(key="host", label="Host URL", field_type="url",
                    placeholder="https://proxmox.local:8006"),
        ConfigField(key="token_id", label="Token ID",
                    placeholder="user@realm!tokenid"),
        ConfigField(key="token_secret", label="Token Secret",
                    field_type="password", encrypted=True),
        ConfigField(key="verify_ssl", label="Verify SSL",
                    field_type="checkbox", required=False, default=True),
        ConfigField(key="ssh_user", label="SSH User (for deploy)",
                    placeholder="root", required=False, default="root"),
        ConfigField(key="ssh_private_key", label="SSH Private Key",
                    field_type="password", encrypted=True, required=False,
                    placeholder="Paste private key for pct exec on Proxmox node"),
    ]

    def _api(self) -> ProxmoxAPI:
        return ProxmoxAPI(
            host=self.config["host"],
            token_id=self.config["token_id"],
            token_secret=self.config["token_secret"],
            verify_ssl=self.config.get("verify_ssl", True),
        )

    async def collect(self) -> CollectorResult:
        try:
            api = self._api()
            resources, status, tasks = await asyncio.gather(
                api.cluster_resources(),
                api.cluster_status(),
                api.cluster_tasks(),
            )
            data = parse_cluster_data(resources, status, tasks)

            # Attach each container's static address. Host discovery needs it: a
            # guest name only pings correctly where internal names resolve
            # internally. Without split-horizon DNS the name resolves to the
            # site's public IP, the check fails on NAT hairpin, and a perfectly
            # healthy container reads as down.
            try:
                containers = data.get("containers", [])
                network = await api.fetch_guest_network(containers)
                for c in containers:
                    info = network.get(c.get("id")) or {}
                    if info.get("ip"):
                        c["ip"] = info["ip"]
            except Exception as net_exc:  # noqa: BLE001 — addresses are a bonus
                logger.debug("Could not resolve container addresses: %s", net_exc)

            # Fetch backup storage content (additive — does not replace existing data)
            try:
                backup_data = await api.fetch_backup_data(resources)
                data["backups"] = backup_data
            except Exception as bk_exc:
                logger.warning("Failed to fetch backup data: %s", bk_exc)
                data["backups"] = {}

            return CollectorResult(success=True, data=data)
        except Exception as exc:
            return CollectorResult(success=False, error=str(exc))

    def parse_alerts(self, data: dict) -> list[Alert]:
        alerts: list[Alert] = []
        if not data.get("quorum_ok", True):
            alerts.append(Alert(severity="critical", title="Cluster quorum lost",
                                detail=f"Cluster: {data.get('cluster_name', '?')}"))
        for node in data.get("nodes", []):
            if not node.get("online", True):
                alerts.append(Alert(severity="critical", title="Node offline",
                                    detail=node.get("name", "?"), entity=f"node: {node.get('name', '?')}"))
            if node.get("cpu_pct", 0) >= 95:
                alerts.append(Alert(severity="warning", title="Node CPU critical",
                                    detail=f"{node.get('name', '?')}: {node.get('cpu_pct')}%",
                                    entity=f"node: {node.get('name', '?')}"))
            if node.get("mem_pct", 0) >= 95:
                alerts.append(Alert(severity="warning", title="Node memory critical",
                                    detail=f"{node.get('name', '?')}: {node.get('mem_pct')}%",
                                    entity=f"node: {node.get('name', '?')}"))
        return alerts

    async def health_check(self) -> bool:
        return await self._api().health_check()

    async def on_snapshot(self, data: dict, config: dict, db) -> None:
        """Auto-import VMs/LXCs as ping hosts and sync backup data after each successful collect."""
        cluster_name = data.get("cluster_name", config.get("host", "Proxmox"))
        await import_proxmox_hosts(cluster_name, data, db)

        # Sync backup jobs from collected data.
        #
        # We need this integration's DB config_id for sync_proxmox_backups, but the
        # on_snapshot hook only receives the decrypted `config` dict (no DB id). The
        # host to match on is therefore taken directly from `config`. We fetch the
        # candidate rows in a single query (instead of select-ids + per-row db.get,
        # which was an N+1) and decrypt lazily, breaking on the first host match so
        # we run at most one expensive PBKDF2 decrypt per non-matching row up to the
        # match — not a full decrypt of every row before doing anything.
        try:
            from services.backup_monitor import sync_proxmox_backups
            from services.integration import decrypt_config
            from models.integration import IntegrationConfig
            from sqlalchemy import select

            own_host = config.get("host")
            result = await db.execute(
                select(IntegrationConfig).where(
                    IntegrationConfig.type == "proxmox",
                    IntegrationConfig.enabled == True,
                )
            )
            for cfg in result.scalars().all():
                if decrypt_config(cfg.config_json).get("host") == own_host:
                    await sync_proxmox_backups(db, cfg.id, data)
                    break
        except Exception as exc:
            logger.warning("Backup sync in on_snapshot failed: %s", exc)

    def get_detail_context(self, data: dict, config: dict) -> dict:
        """Provide parsed data for the Proxmox detail template."""
        nodes = data.get("nodes", [])
        vms = data.get("vms", [])
        containers = data.get("containers", [])
        totals = data.get("totals", {})
        return {
            "nodes": nodes,
            "vms": vms,
            "containers": containers,
            "totals": totals,
            "quorum_ok": data.get("quorum_ok", True),
            "cluster_name": data.get("cluster_name", ""),
            "tasks": data.get("tasks", []),
            "backups": data.get("backups", {}),
        }
