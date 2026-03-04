"""Speedtest.net integration – measures internet speed."""
from __future__ import annotations
import asyncio
import json
import subprocess
import logging

logger = logging.getLogger(__name__)


async def run_speedtest(server_id: str | None = None) -> dict:
    """
    Runs speedtest-cli --json and parses the result.
    Returns:
    {
      "download_mbps": float,
      "upload_mbps": float,
      "ping_ms": float,
      "server_name": str,
      "server_location": str,
      "isp": str,
      "timestamp": str,
    }
    Raises RuntimeError if speedtest-cli is not installed or fails.
    """
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

    return {
        "download_mbps":   round(raw["download"] / 1_000_000, 2),
        "upload_mbps":     round(raw["upload"]   / 1_000_000, 2),
        "ping_ms":         round(raw["ping"], 1),
        "server_name":     f"{raw['server']['name']}, {raw['server']['country']}",
        "server_location": raw["server"].get("sponsor", ""),
        "isp":             raw.get("client", {}).get("isp", ""),
        "timestamp":       raw.get("timestamp", ""),
    }


async def check_speedtest_available() -> bool:
    """Check if speedtest-cli is installed."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "speedtest-cli", "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        return proc.returncode == 0
    except FileNotFoundError:
        return False
