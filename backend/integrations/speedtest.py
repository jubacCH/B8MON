"""Speedtest integration – measures internet speed via speedtest-cli."""
from __future__ import annotations

import asyncio
import json
import subprocess

from integrations._base import BaseIntegration, CollectorResult, ConfigField


# ── Runner ────────────────────────────────────────────────────────────────────


# speedtest-cli has been unmaintained since 2021 and reports a fixed
# ping = 1800000.0 (half an hour) against some connections instead of a
# measurement. Anything past this ceiling is a sentinel, not a slow link:
# even satellite and congested mobile stay well under it.
MAX_PLAUSIBLE_PING_MS = 10_000


def plausible_ping(value) -> float | None:
    """Return the ping if it can be a real measurement, else None.

    None means "not measured" and is rendered as such. Showing an invented
    number is worse than showing nothing — the point of the reading is that it
    can be trusted.
    """
    try:
        ping = float(value)
    except (TypeError, ValueError):
        return None
    if ping <= 0 or ping > MAX_PLAUSIBLE_PING_MS:
        return None
    return ping


async def run_speedtest(server_id: str | None = None) -> dict:
    cmd = ["speedtest-cli", "--json", "--secure"]
    if server_id:
        cmd += ["--server", str(server_id)]

    loop = asyncio.get_event_loop()

    def _run():
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise RuntimeError(f"speedtest-cli failed: {result.stderr.strip()}")
        return json.loads(result.stdout)

    raw = await loop.run_in_executor(None, _run)

    # Use .get() with defaults: speedtest-cli output shape can vary between
    # versions, and a missing field should not raise KeyError on the whole result.
    def _num(value, ndigits: int) -> float:
        try:
            return round(float(value), ndigits)
        except (TypeError, ValueError):
            return 0.0

    server = raw.get("server", {})
    if not isinstance(server, dict):
        server = {}
    server_name = server.get("name", "")
    server_country = server.get("country", "")

    return {
        "download_mbps": round(_num(raw.get("download", 0), 6) / 1_000_000, 2),
        "upload_mbps": round(_num(raw.get("upload", 0), 6) / 1_000_000, 2),
        "ping_ms": plausible_ping(raw.get("ping")),
        "server_name": ", ".join(p for p in (server_name, server_country) if p),
        "server_location": server.get("sponsor", ""),
        "isp": raw.get("client", {}).get("isp", "") if isinstance(raw.get("client"), dict) else "",
        "timestamp": raw.get("timestamp", ""),
    }


async def check_speedtest_available() -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            "speedtest-cli", "--version",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await proc.communicate()
        return proc.returncode == 0
    except FileNotFoundError:
        return False


# ── Integration Plugin ────────────────────────────────────────────────────────


class SpeedtestIntegration(BaseIntegration):
    name = "speedtest"
    display_name = "Speedtest"
    icon = "speedtest"
    color = "blue"
    single_instance = True
    description = "Measure internet speed using speedtest-cli."

    config_fields = [
        ConfigField(key="server_id", label="Server ID (optional)",
                    placeholder="Leave empty for auto-select", required=False),
    ]

    async def collect(self) -> CollectorResult:
        try:
            data = await run_speedtest(self.config.get("server_id") or None)
            return CollectorResult(success=True, data=data)
        except Exception as exc:
            return CollectorResult(success=False, error=str(exc))

    async def health_check(self) -> bool:
        return await check_speedtest_available()
