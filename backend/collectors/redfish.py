"""Redfish / iDRAC integration – hardware health monitoring."""
from __future__ import annotations
import base64
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Candidate system paths to try in order (Dell iDRAC first, then generic)
_SYSTEM_PATHS = [
    "/redfish/v1/Systems/System.Embedded.1",
    "/redfish/v1/Systems/1",
    "/redfish/v1/Systems/Self",
]

# Candidate chassis paths for thermal / power
_CHASSIS_PATHS = [
    "/redfish/v1/Chassis/System.Embedded.1",
    "/redfish/v1/Chassis/1",
    "/redfish/v1/Chassis/Self",
]


def parse_redfish_data(system: dict, thermal: dict | None, power: dict | None) -> dict:
    """
    Parse raw Redfish API dicts into a structured summary.
    Returns:
    {
      "hostname": str,
      "manufacturer": str,
      "model": str,
      "serial": str,
      "bios_version": str,
      "status": str,       # OK | Warning | Critical
      "healthy": bool,
      "power_state": str,  # On | Off | ...
      "cpu_count": int,
      "memory_gb": float,
      "temperatures": [{"name", "reading_c", "status", "threshold_c"}],
      "fans": [{"name", "rpm", "status"}],
      "power_watts": float | None,
      "health_summary": str,
    }
    """
    def _health(obj: Any) -> str:
        if isinstance(obj, dict):
            return obj.get("Health") or obj.get("State") or "Unknown"
        return "Unknown"

    status_obj = system.get("Status", {})
    overall    = _health(status_obj) if isinstance(status_obj, dict) else "Unknown"

    mem_summary = system.get("MemorySummary", {})
    cpu_summary = system.get("ProcessorSummary", {})

    mem_health = _health(mem_summary.get("Status", {}))
    cpu_health = _health(cpu_summary.get("Status", {}))

    # Health summary string
    parts = []
    if mem_health not in ("", "Unknown", "OK"):
        parts.append(f"Memory: {mem_health}")
    if cpu_health not in ("", "Unknown", "OK"):
        parts.append(f"CPU: {cpu_health}")
    health_summary = ", ".join(parts) if parts else overall

    # Memory in GB
    mem_gib = mem_summary.get("TotalSystemMemoryGiB") or mem_summary.get("TotalSystemMemoryGB")
    try:
        memory_gb = round(float(mem_gib), 1) if mem_gib is not None else 0.0
    except (TypeError, ValueError):
        memory_gb = 0.0

    # CPU count
    try:
        cpu_count = int(cpu_summary.get("Count") or cpu_summary.get("LogicalProcessorCount") or 0)
    except (TypeError, ValueError):
        cpu_count = 0

    # Temperatures
    temperatures: list[dict] = []
    if thermal:
        for t in thermal.get("Temperatures", []):
            if not isinstance(t, dict):
                continue
            name    = t.get("Name") or t.get("MemberId") or ""
            reading = t.get("ReadingCelsius")
            thresh  = t.get("UpperThresholdCritical") or t.get("UpperThresholdNonCritical")
            tstatus = _health(t.get("Status", {}))
            if reading is None:
                continue
            try:
                temperatures.append({
                    "name":        name,
                    "reading_c":   round(float(reading), 1),
                    "status":      tstatus,
                    "threshold_c": round(float(thresh), 1) if thresh is not None else None,
                })
            except (TypeError, ValueError):
                pass

    # Fans
    fans: list[dict] = []
    if thermal:
        for f in thermal.get("Fans", []):
            if not isinstance(f, dict):
                continue
            name   = f.get("Name") or f.get("FanName") or f.get("MemberId") or ""
            rpm    = f.get("Reading") or f.get("CurrentReading")
            fstatus = _health(f.get("Status", {}))
            fans.append({
                "name":   name,
                "rpm":    rpm,
                "status": fstatus,
            })

    # Power
    power_watts: float | None = None
    if power:
        for ctrl in power.get("PowerControl", []):
            if not isinstance(ctrl, dict):
                continue
            consumed = ctrl.get("PowerConsumedWatts")
            if consumed is not None:
                try:
                    power_watts = round(float(consumed), 1)
                    break
                except (TypeError, ValueError):
                    pass

    return {
        "hostname":      system.get("HostName") or system.get("Name") or "",
        "manufacturer":  system.get("Manufacturer") or "",
        "model":         system.get("Model") or "",
        "serial":        system.get("SerialNumber") or "",
        "bios_version":  system.get("BiosVersion") or "",
        "status":        overall,
        "healthy":       overall in ("OK", ""),
        "power_state":   system.get("PowerState") or "Unknown",
        "cpu_count":     cpu_count,
        "memory_gb":     memory_gb,
        "temperatures":  temperatures,
        "fans":          fans,
        "power_watts":   power_watts,
        "health_summary": health_summary,
    }


class RedfishAPI:
    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        verify_ssl: bool = False,
    ):
        self.base      = host.rstrip("/")
        self.username  = username
        self.password  = password
        self.verify_ssl = verify_ssl

    def _headers(self) -> dict:
        cred = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
        return {"Authorization": f"Basic {cred}", "Accept": "application/json"}

    async def _get(self, client: httpx.AsyncClient, path: str) -> dict:
        resp = await client.get(
            f"{self.base}{path}",
            headers=self._headers(),
            timeout=20.0,
        )
        resp.raise_for_status()
        return resp.json()

    async def _try_paths(self, client: httpx.AsyncClient, paths: list[str]) -> tuple[str, dict]:
        """Try each path in order; return (path, data) for the first that succeeds."""
        last_exc: Exception | None = None
        for path in paths:
            try:
                data = await self._get(client, path)
                return path, data
            except Exception as exc:
                last_exc = exc
        raise last_exc or RuntimeError("No valid path found")

    async def fetch_all(self) -> dict:
        """Fetch system health, thermal and power data."""
        async with httpx.AsyncClient(verify=self.verify_ssl) as client:
            # Try to discover via service root
            try:
                root = await self._get(client, "/redfish/v1/")
                system_link  = (root.get("Systems",  {}) or {}).get("@odata.id")
                chassis_link = (root.get("Chassis",  {}) or {}).get("@odata.id")
            except Exception:
                system_link  = None
                chassis_link = None

            # Resolve system
            system_path: str | None = None
            system: dict = {}
            if system_link:
                try:
                    coll = await self._get(client, system_link)
                    members = coll.get("Members", [])
                    if members:
                        system_path = members[0].get("@odata.id")
                        system = await self._get(client, system_path)
                except Exception:
                    pass

            if not system:
                _, system = await self._try_paths(client, _SYSTEM_PATHS)

            # Resolve chassis
            chassis_path: str | None = None
            if chassis_link:
                try:
                    coll = await self._get(client, chassis_link)
                    members = coll.get("Members", [])
                    if members:
                        chassis_path = members[0].get("@odata.id")
                except Exception:
                    pass

            if not chassis_path:
                try:
                    chassis_path, _ = await self._try_paths(client, _CHASSIS_PATHS)
                except Exception:
                    chassis_path = None

            # Fetch thermal + power
            thermal: dict | None = None
            power:   dict | None = None
            if chassis_path:
                try:
                    thermal = await self._get(client, f"{chassis_path}/Thermal")
                except Exception:
                    pass
                try:
                    power = await self._get(client, f"{chassis_path}/Power")
                except Exception:
                    pass

        return parse_redfish_data(system, thermal, power)

    async def health_check(self) -> bool:
        """Return True if the Redfish endpoint is reachable."""
        try:
            async with httpx.AsyncClient(verify=self.verify_ssl) as client:
                await self._get(client, "/redfish/v1/")
            return True
        except Exception:
            return False
