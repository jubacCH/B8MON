"""Home Assistant integration – collects entity states and config."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

MONITORED_DOMAINS = {"sensor", "binary_sensor", "switch", "light", "person", "automation", "input_boolean", "climate", "cover", "media_player"}


class HassAPI:
    def __init__(self, host: str, token: str, verify_ssl: bool = False):
        self.base = host.rstrip("/")
        self.token = token
        self.verify_ssl = verify_ssl

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    async def fetch_all(self) -> dict:
        """Fetch config and entity states, return raw dicts."""
        async with httpx.AsyncClient(verify=self.verify_ssl, timeout=15) as client:
            config_resp = await client.get(f"{self.base}/api/config", headers=self._headers())
            config_resp.raise_for_status()
            config = config_resp.json()

            states_resp = await client.get(f"{self.base}/api/states", headers=self._headers())
            states_resp.raise_for_status()
            states = states_resp.json()

        return {"config": config, "states": states}

    async def health_check(self) -> bool:
        """Return True if the HA API is reachable."""
        try:
            async with httpx.AsyncClient(verify=self.verify_ssl, timeout=8) as client:
                resp = await client.get(f"{self.base}/api/", headers=self._headers())
                return resp.status_code == 200
        except Exception:
            return False


def parse_hass_data(config: dict, states: list) -> dict:
    """
    Parse Home Assistant config + states into a structured summary dict.

    Returns:
    {
      "version": str,
      "location_name": str,
      "timezone": str,
      "components": int,
      "entities": {
        "total": int,
        "by_domain": {"sensor": int, "binary_sensor": int, ...}
      },
      "automations": [{"entity_id", "name", "state", "last_triggered"}],
      "persons": [{"name", "state"}],
    }
    """
    version = config.get("version", "unknown")
    location_name = config.get("location_name", "Home")
    timezone = config.get("time_zone", config.get("timezone", "UTC"))
    components = len(config.get("components", []))

    by_domain: dict[str, int] = {}
    automations: list[dict] = []
    persons: list[dict] = []

    for entity in states:
        entity_id: str = entity.get("entity_id", "")
        domain = entity_id.split(".")[0] if "." in entity_id else ""

        if domain in MONITORED_DOMAINS:
            by_domain[domain] = by_domain.get(domain, 0) + 1

        if domain == "automation":
            attrs = entity.get("attributes", {})
            last_triggered = attrs.get("last_triggered")
            automations.append({
                "entity_id": entity_id,
                "name": attrs.get("friendly_name", entity_id),
                "state": entity.get("state", "unknown"),
                "last_triggered": last_triggered,
            })

        if domain == "person":
            attrs = entity.get("attributes", {})
            persons.append({
                "name": attrs.get("friendly_name", entity_id),
                "state": entity.get("state", "unknown"),
            })

    total = sum(by_domain.values())

    return {
        "version": version,
        "location_name": location_name,
        "timezone": timezone,
        "components": components,
        "entities": {
            "total": total,
            "by_domain": by_domain,
        },
        "automations": sorted(automations, key=lambda a: a["name"]),
        "persons": persons,
    }


async def collect_hass_instance(instance_id: int, db: "AsyncSession") -> None:
    """Fetch and store a snapshot for a single Home Assistant instance."""
    import json
    from datetime import datetime

    from database import HassInstance, HassSnapshot, decrypt_value

    instance = await db.get(HassInstance, instance_id)
    if not instance:
        return

    token = decrypt_value(instance.token_enc)
    api = HassAPI(host=instance.host, token=token, verify_ssl=instance.verify_ssl)

    try:
        raw = await api.fetch_all()
        data = parse_hass_data(raw["config"], raw["states"])
        db.add(HassSnapshot(
            instance_id=instance_id,
            timestamp=datetime.utcnow(),
            ok=True,
            data_json=json.dumps(data),
            error=None,
        ))
    except Exception as exc:
        logger.error("Home Assistant collect [%s]: %s", instance.name, exc)
        db.add(HassSnapshot(
            instance_id=instance_id,
            timestamp=datetime.utcnow(),
            ok=False,
            data_json=None,
            error=str(exc),
        ))
    await db.commit()
