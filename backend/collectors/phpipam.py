"""
phpIPAM integration – optionally imports IP addresses as PingHosts.

Authentication supports two modes:
  - Username + password  (recommended)
  - App code only        (when phpIPAM app is set to "App code" security)

Sync behaviour:
  - New IP → create PingHost with source="phpipam"
  - IP already exists → merge: update name if it is still just the IP,
    mark source="phpipam", count as "merged"
  - Inactive/empty → skip
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class PhpIpamClient:
    def __init__(
        self,
        base_url: str,
        app_id: str,
        username: str | None = None,
        password: str | None = None,
        verify_ssl: bool = True,
    ):
        self.base = base_url.rstrip("/")
        self.app_id = app_id
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self._token: str | None = None

    def _api(self, path: str) -> str:
        return f"{self.base}/api/{self.app_id}/{path.lstrip('/')}"

    async def authenticate(self) -> None:
        """Fetch a session token using username + password."""
        if not self.username or not self.password:
            return  # App-code-only mode; no token needed

        async with httpx.AsyncClient(verify=self.verify_ssl, timeout=10) as client:
            resp = await client.post(
                self._api("user/"),
                auth=(self.username, self.password),
            )
            resp.raise_for_status()
            body = resp.json()
            if not body.get("success"):
                raise ValueError(f"phpIPAM auth failed: {body.get('message', 'unknown')}")
            self._token = body["data"]["token"]

    def _headers(self) -> dict:
        if self._token:
            return {"phpipam-token": self._token}
        return {}

    async def get_addresses(self) -> list[dict]:
        """Return all IP addresses from phpIPAM."""
        async with httpx.AsyncClient(verify=self.verify_ssl, timeout=15) as client:
            resp = await client.get(self._api("addresses/all/"), headers=self._headers())
            if resp.status_code == 404:
                return []
            resp.raise_for_status()
            body = resp.json()
            if not body.get("success"):
                return []
            return body.get("data") or []


async def sync_phpipam_hosts(db: "AsyncSession") -> dict:
    """
    Fetch addresses from phpIPAM and sync them as PingHosts.

    - New IPs are created with source="phpipam".
    - IPs that already exist are merged: name is enriched if still bare IP,
      source is updated to "phpipam" if it was "manual".

    Returns: {"added": int, "merged": int, "skipped": int, "errors": list[str]}
    """
    from database import PingHost, decrypt_value, get_setting
    from sqlalchemy import select

    url           = await get_setting(db, "phpipam_url", "")
    app_id        = await get_setting(db, "phpipam_app_id", "")
    username      = await get_setting(db, "phpipam_username", "")
    password_enc  = await get_setting(db, "phpipam_password", "")
    verify_ssl_str = await get_setting(db, "phpipam_verify_ssl", "1")

    if not url or not app_id:
        return {"added": 0, "merged": 0, "skipped": 0,
                "errors": ["phpIPAM not configured (URL or App-ID missing)"]}

    password   = decrypt_value(password_enc) if password_enc else ""
    verify_ssl = verify_ssl_str != "0"

    client = PhpIpamClient(
        base_url=url,
        app_id=app_id,
        username=username or None,
        password=password or None,
        verify_ssl=verify_ssl,
    )

    try:
        await client.authenticate()
        addresses = await client.get_addresses()
    except Exception as exc:
        logger.error("phpIPAM fetch failed: %s", exc)
        return {"added": 0, "merged": 0, "skipped": 0, "errors": [str(exc)]}

    # Load existing hosts indexed by hostname for O(1) lookup
    existing_q = await db.execute(select(PingHost))
    existing: dict[str, PingHost] = {h.hostname: h for h in existing_q.scalars().all()}

    added = 0
    merged = 0
    skipped = 0
    errors: list[str] = []
    dirty = False

    for addr in addresses:
        if str(addr.get("active", "1")) == "0":
            skipped += 1
            continue

        ip = (addr.get("ip") or "").strip()
        if not ip:
            skipped += 1
            continue

        # Prefer hostname > description > IP as display name
        name = (
            addr.get("hostname") or
            addr.get("description") or
            ip
        ).strip() or ip

        try:
            if ip in existing:
                host = existing[ip]
                changed = False
                # Enrich name if it is still just the raw IP
                if host.name == host.hostname and name != ip:
                    host.name = name[:128]
                    changed = True
                # Update source if not already set from an import
                if host.source == "manual":
                    host.source = "phpipam"
                    changed = True
                if changed:
                    dirty = True
                merged += 1
            else:
                db.add(PingHost(
                    name=name[:128],
                    hostname=ip,
                    check_type="icmp",
                    enabled=True,
                    source="phpipam",
                    source_detail=url,
                ))
                existing[ip] = True  # type: ignore[assignment]
                added += 1
                dirty = True
        except Exception as exc:
            errors.append(f"{ip}: {exc}")

    if dirty:
        await db.commit()

    logger.info("phpIPAM sync: +%d added, ~%d merged, %d skipped, %d errors",
                added, merged, skipped, len(errors))
    return {"added": added, "merged": merged, "skipped": skipped, "errors": errors}
