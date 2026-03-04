"""Firewall integration – OPNsense and pfSense monitoring."""
from __future__ import annotations

import base64

import httpx


class OPNsenseAPI:
    def __init__(
        self,
        host: str,
        api_key: str,
        api_secret: str,
        verify_ssl: bool = False,
    ):
        if not host.startswith("http://") and not host.startswith("https://"):
            host = f"https://{host}"
        self.base = host.rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret
        self.verify_ssl = verify_ssl

    def _headers(self) -> dict:
        cred = base64.b64encode(
            f"{self.api_key}:{self.api_secret}".encode()
        ).decode()
        return {"Authorization": f"Basic {cred}"}

    async def _get(self, client: httpx.AsyncClient, path: str) -> dict:
        resp = await client.get(
            f"{self.base}{path}",
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()

    async def fetch_all(self) -> dict:
        """Fetch system info from OPNsense API endpoints."""
        async with httpx.AsyncClient(verify=self.verify_ssl, timeout=20) as client:
            firmware: dict = {}
            status: dict = {}
            interfaces: dict = {}

            try:
                firmware = await self._get(client, "/api/core/firmware/info")
            except Exception:
                pass

            try:
                status = await self._get(client, "/api/core/system/status")
            except Exception:
                pass

            try:
                interfaces = await self._get(
                    client, "/api/diagnostics/interface/getInterfaceNames"
                )
            except Exception:
                pass

        return {
            "fw_type": "opnsense",
            "firmware": firmware,
            "status": status,
            "interfaces": interfaces,
        }

    async def health_check(self) -> bool:
        """Return True if OPNsense API responds successfully."""
        try:
            async with httpx.AsyncClient(verify=self.verify_ssl, timeout=10) as client:
                resp = await client.get(
                    f"{self.base}/api/core/firmware/info",
                    headers=self._headers(),
                )
                return resp.status_code < 400
        except Exception:
            return False


class PfsenseAPI:
    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        verify_ssl: bool = False,
    ):
        if not host.startswith("http://") and not host.startswith("https://"):
            host = f"https://{host}"
        self.base = host.rstrip("/")
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl

    def _auth(self) -> tuple[str, str]:
        return (self.username, self.password)

    async def fetch_all(self) -> dict:
        """Try pfSense-API plugin: GET /api/v1/system/info."""
        async with httpx.AsyncClient(verify=self.verify_ssl, timeout=20) as client:
            sys_info: dict = {}
            interfaces: dict = {}

            try:
                resp = await client.get(
                    f"{self.base}/api/v1/system/info",
                    auth=self._auth(),
                )
                resp.raise_for_status()
                sys_info = resp.json()
            except Exception:
                pass

            try:
                resp = await client.get(
                    f"{self.base}/api/v1/interface",
                    auth=self._auth(),
                )
                resp.raise_for_status()
                interfaces = resp.json()
            except Exception:
                pass

        return {
            "fw_type": "pfsense",
            "sys_info": sys_info,
            "interfaces": interfaces,
        }

    async def health_check(self) -> bool:
        """Return True if pfSense API responds successfully."""
        try:
            async with httpx.AsyncClient(verify=self.verify_ssl, timeout=10) as client:
                resp = await client.get(
                    f"{self.base}/api/v1/system/info",
                    auth=self._auth(),
                )
                return resp.status_code < 400
        except Exception:
            return False


def parse_opnsense_data(firmware: dict, status: dict) -> dict:
    """
    Parse OPNsense firmware and system status API responses.

    Returns:
    {
      "fw_type": "opnsense",
      "version": str,
      "hostname": str,
      "cpu_pct": float,
      "mem_pct": float,
      "uptime_s": int,
      "interfaces": [{"name": str, "description": str, "ipv4": str, "status": str}],
      "alerts": int,
    }
    """
    # Firmware / version info
    fw_data = firmware if isinstance(firmware, dict) else {}
    version = fw_data.get("product_version", fw_data.get("version", "unknown"))

    # System status – OPNsense /api/core/system/status returns a dict where
    # each key is a subsystem.  We look for cpu, memory, hostname, uptime.
    st = status if isinstance(status, dict) else {}

    hostname = st.get("hostname", "unknown")
    uptime_s = 0
    cpu_pct = 0.0
    mem_pct = 0.0
    alerts = 0

    # OPNsense status payload varies by version; try common shapes
    if "kernel" in st:
        # Older firmware: {'kernel': {'pf': {...}, 'uptime': ...}, ...}
        uptime_raw = st.get("kernel", {}).get("uptime", "")
        uptime_s = _parse_uptime(uptime_raw)
    elif "uptime" in st:
        uptime_s = _parse_uptime(st["uptime"])

    if "cpu" in st:
        cpu_raw = st["cpu"]
        if isinstance(cpu_raw, (int, float)):
            cpu_pct = float(cpu_raw)
        elif isinstance(cpu_raw, str):
            cpu_pct = float(cpu_raw.rstrip("%")) if cpu_raw.rstrip("%").replace(".", "", 1).isdigit() else 0.0

    if "memory" in st:
        mem_raw = st["memory"]
        if isinstance(mem_raw, dict):
            used = mem_raw.get("used", 0)
            total = mem_raw.get("total", 0)
            if total:
                mem_pct = round(float(used) / float(total) * 100, 1)
        elif isinstance(mem_raw, (int, float)):
            mem_pct = float(mem_raw)

    if "alerts" in st:
        alerts_raw = st["alerts"]
        if isinstance(alerts_raw, (int, float)):
            alerts = int(alerts_raw)

    return {
        "fw_type": "opnsense",
        "version": version,
        "hostname": hostname,
        "cpu_pct": cpu_pct,
        "mem_pct": mem_pct,
        "uptime_s": uptime_s,
        "interfaces": [],  # populated separately by the router if needed
        "alerts": alerts,
    }


def parse_pfsense_data(info: dict) -> dict:
    """
    Parse pfSense-API /api/v1/system/info response.

    Returns a dict with the same shape as parse_opnsense_data.
    """
    data = info.get("data", info) if isinstance(info, dict) else {}

    hostname = data.get("hostname", data.get("name", "unknown"))
    version = data.get("version", {})
    if isinstance(version, dict):
        version = version.get("version", "unknown")
    elif not isinstance(version, str):
        version = str(version)

    uptime_raw = data.get("uptime", "")
    uptime_s = _parse_uptime(uptime_raw)

    cpu_pct = 0.0
    cpu_raw = data.get("cpu_usage", data.get("cpu", None))
    if cpu_raw is not None:
        try:
            cpu_pct = float(cpu_raw)
        except (TypeError, ValueError):
            cpu_pct = 0.0

    mem_pct = 0.0
    mem_raw = data.get("mem_usage", data.get("memory", None))
    if mem_raw is not None:
        try:
            mem_pct = float(mem_raw)
        except (TypeError, ValueError):
            mem_pct = 0.0

    return {
        "fw_type": "pfsense",
        "version": version,
        "hostname": hostname,
        "cpu_pct": cpu_pct,
        "mem_pct": mem_pct,
        "uptime_s": uptime_s,
        "interfaces": [],
        "alerts": 0,
    }


def _parse_uptime(raw) -> int:
    """Convert various uptime representations to seconds (int)."""
    if raw is None:
        return 0
    if isinstance(raw, (int, float)):
        return int(raw)
    raw = str(raw).strip()
    # e.g. "3 days, 04:22:11" or "04:22:11"
    total = 0
    try:
        if "day" in raw:
            parts = raw.split(",")
            days_part = parts[0].strip()
            days = int(days_part.split()[0])
            total += days * 86400
            raw = parts[1].strip() if len(parts) > 1 else ""
        if ":" in raw:
            hms = raw.strip().split(":")
            if len(hms) == 3:
                total += int(hms[0]) * 3600 + int(hms[1]) * 60 + int(hms[2].split(".")[0])
            elif len(hms) == 2:
                total += int(hms[0]) * 60 + int(hms[1])
        elif raw.isdigit():
            total = int(raw)
    except (ValueError, IndexError):
        pass
    return total
