"""
Ping integration – monitors hosts via ICMP ping.
Hosts are managed in the DB (PingHost table), not via settings.
This collector queries all enabled ping hosts and returns their status.
"""
import asyncio
from datetime import datetime
from typing import Any

from collectors.base import BaseCollector, CollectorResult, ConfigField


async def ping_host(hostname: str, timeout: float = 2.0) -> tuple[bool, float | None]:
    """Ping a host using system ping binary. Returns (success, latency_ms)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ping", "-c", "1", "-W", str(int(timeout)),
            hostname,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            output = stdout.decode()
            # Parse latency: "time=1.23 ms" or "time=1.23ms"
            for token in output.split():
                if token.startswith("time="):
                    try:
                        latency = float(token.split("=")[1].replace("ms", "").strip())
                        return True, latency
                    except ValueError:
                        pass
            return True, None
        return False, None
    except Exception:
        return False, None


class PingCollector(BaseCollector):
    name = "ping"
    display_name = "Ping Monitor"
    description = "Überwacht Hosts per ICMP Ping"
    icon = "📡"

    @classmethod
    def get_config_fields(cls) -> list[ConfigField]:
        # No global config – hosts are added individually in the UI
        return []

    async def collect(self) -> CollectorResult:
        # The actual per-host pinging is done by the scheduler directly,
        # using the DB. This method is a no-op for the ping integration.
        return CollectorResult(success=True, data={})

    async def health_check(self) -> bool:
        # Ping localhost to verify ping works
        ok, _ = await ping_host("127.0.0.1", timeout=1.0)
        return ok
