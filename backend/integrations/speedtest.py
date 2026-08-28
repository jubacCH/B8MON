"""Internet speed measurement.

Two tools are supported. The Ookla CLI is preferred: the older `speedtest-cli`
has been unmaintained since 2021 and reports a fixed ping = 1800000.0 (half an
hour) against some connections instead of a measurement.

The legacy path is kept rather than removed so an installation that has not yet
got the Ookla binary keeps measuring instead of silently losing the integration
on upgrade.

Their output differs in a way that is easy to get wrong: Ookla reports
bandwidth in *bytes* per second, the legacy tool in *bits*.
"""
import asyncio
import json
import logging
import shutil
import subprocess

from integrations._base import BaseIntegration, CollectorResult, ConfigField

logger = logging.getLogger(__name__)

# speedtest-cli returns a fixed 1800000.0 instead of measuring on some
# connections. Anything past this ceiling is a sentinel, not a slow link — even
# satellite and congested mobile stay well under it.
MAX_PLAUSIBLE_PING_MS = 10_000

OOKLA_BIN = "speedtest"
LEGACY_BIN = "speedtest-cli"


def plausible_ping(value) -> float | None:
    """Return the ping if it can be a real measurement, else None.

    None means "not measured" and is rendered as such. Showing an invented
    number is worse than showing nothing — the point of a reading is that it can
    be trusted.
    """
    try:
        ping = float(value)
    except (TypeError, ValueError):
        return None
    if ping <= 0 or ping > MAX_PLAUSIBLE_PING_MS:
        return None
    return round(ping, 1)


def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def parse_ookla_result(raw: dict) -> dict:
    """Parse `speedtest --format=json` output.

    bandwidth is bytes per second; Mbit/s is bytes * 8 / 1e6.
    """
    raw = _as_dict(raw)
    server = _as_dict(raw.get("server"))
    location = ", ".join(
        p for p in (server.get("location", ""), server.get("country", "")) if p
    )
    return {
        "download_mbps": round(_num(_as_dict(raw.get("download")).get("bandwidth")) * 8 / 1_000_000, 2),
        "upload_mbps": round(_num(_as_dict(raw.get("upload")).get("bandwidth")) * 8 / 1_000_000, 2),
        "ping_ms": plausible_ping(_as_dict(raw.get("ping")).get("latency")),
        "server_name": location,
        "server_location": server.get("name", ""),
        "isp": raw.get("isp", "") or "",
        "timestamp": raw.get("timestamp", "") or "",
    }


def parse_legacy_result(raw: dict) -> dict:
    """Parse `speedtest-cli --json` output, where bandwidth is bits per second."""
    raw = _as_dict(raw)
    server = _as_dict(raw.get("server"))
    name = ", ".join(
        p for p in (server.get("name", ""), server.get("country", "")) if p
    )
    return {
        "download_mbps": round(_num(raw.get("download")) / 1_000_000, 2),
        "upload_mbps": round(_num(raw.get("upload")) / 1_000_000, 2),
        "ping_ms": plausible_ping(raw.get("ping")),
        "server_name": name,
        "server_location": server.get("sponsor", ""),
        "isp": _as_dict(raw.get("client")).get("isp", ""),
        "timestamp": raw.get("timestamp", "") or "",
    }


def _available(binary: str) -> bool:
    return shutil.which(binary) is not None


async def run_speedtest(server_id: str | None = None) -> dict:
    """Measure, preferring the maintained Ookla CLI."""
    use_ookla = _available(OOKLA_BIN)

    if use_ookla:
        # The licence prompts are interactive and would hang a scheduled run.
        cmd = [OOKLA_BIN, "--format=json", "--accept-license", "--accept-gdpr"]
        if server_id:
            cmd += ["--server-id", str(server_id)]
    else:
        cmd = [LEGACY_BIN, "--json", "--secure"]
        if server_id:
            cmd += ["--server", str(server_id)]

    def _run():
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode != 0:
            raise RuntimeError(f"{cmd[0]} failed: {result.stderr.strip()[:300]}")
        return json.loads(result.stdout)

    raw = await asyncio.get_event_loop().run_in_executor(None, _run)
    return parse_ookla_result(raw) if use_ookla else parse_legacy_result(raw)


async def check_speedtest_available() -> bool:
    return _available(OOKLA_BIN) or _available(LEGACY_BIN)


class SpeedtestIntegration(BaseIntegration):
    name = "speedtest"
    display_name = "Speedtest"
    icon = "speedtest"
    color = "blue"
    single_instance = True
    description = "Measure internet speed using the Ookla speedtest CLI."

    # A speedtest deliberately saturates the connection for 30-60 s. Polled on
    # the default cadence, runs overlapped continuously: readings swung between
    # 1.29 and 176 Mbps on the same line within five minutes, and the uplink was
    # never idle. Hourly is frequent enough to spot a degraded connection.
    default_interval_seconds = 3600

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
