"""
Ping/HTTP/TCP/SSL utilities for host monitoring.
"""
from __future__ import annotations

import asyncio
import ssl
import time
from datetime import datetime
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from database import PingHost


# ── ICMP ──────────────────────────────────────────────────────────────────────

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


# ── HTTP / HTTPS ───────────────────────────────────────────────────────────────

async def check_http(url: str, timeout: float = 5.0) -> tuple[bool, float | None]:
    """HTTP(S) GET check. Returns (success, latency_ms). Success = 2xx/3xx."""
    try:
        async with httpx.AsyncClient(verify=False, timeout=timeout,
                                     follow_redirects=True) as client:
            start = time.perf_counter()
            resp = await client.get(url)
            latency = round((time.perf_counter() - start) * 1000, 2)
            return resp.status_code < 500, latency
    except Exception:
        return False, None


# ── TCP ───────────────────────────────────────────────────────────────────────

async def check_tcp(hostname: str, port: int, timeout: float = 3.0) -> tuple[bool, float | None]:
    """TCP connect check. Returns (success, latency_ms)."""
    try:
        start = time.perf_counter()
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(hostname, port), timeout=timeout
        )
        latency = round((time.perf_counter() - start) * 1000, 2)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True, latency
    except Exception:
        return False, None


# ── SSL expiry ─────────────────────────────────────────────────────────────────

async def get_ssl_expiry_days(hostname: str, port: int = 443) -> int | None:
    """Return days until SSL certificate expiry for an HTTPS host, or None on error."""
    try:
        loop = asyncio.get_event_loop()
        cert_pem = await loop.run_in_executor(
            None, lambda: ssl.get_server_certificate((hostname, port), timeout=5)
        )
        proc = await asyncio.create_subprocess_exec(
            "openssl", "x509", "-noout", "-enddate",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate(input=cert_pem.encode())
        line = stdout.decode().strip()
        date_str = line.split("=", 1)[1].strip()
        from datetime import timezone
        expiry = datetime.strptime(date_str, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        delta = expiry - datetime.now(timezone.utc)
        return max(0, delta.days)
    except Exception:
        return None


# ── Dispatcher ─────────────────────────────────────────────────────────────────

async def _check_single(host: "PingHost", ct: str) -> tuple[bool, float | None]:
    """Run a single check type for the given host."""
    ct = ct.lower()
    if ct == "icmp":
        return await ping_host(host.hostname)
    if ct in ("http", "https"):
        hostname = host.hostname
        if hostname.startswith("http://") or hostname.startswith("https://"):
            url = hostname
        else:
            scheme = "https" if ct == "https" else "http"
            default_port = 443 if ct == "https" else 80
            port_suffix = f":{host.port}" if host.port and host.port != default_port else ""
            url = f"{scheme}://{hostname}{port_suffix}"
        return await check_http(url)
    if ct == "tcp":
        port = host.port or 80
        return await check_tcp(host.hostname, port)
    return await ping_host(host.hostname)


async def check_host(host: "PingHost") -> tuple[bool, bool, float | None, dict]:
    """Run all check types in parallel.

    Returns (online, port_error, latency_ms, check_detail):
      - online: True if ICMP succeeds (or no ICMP configured and any check passes)
      - port_error: True if host is online but a port/http/https check failed
      - latency_ms: ICMP latency preferred, else first available
      - check_detail: per-check results dict, e.g. {"icmp": true, "https": false}
    """
    types = [t.strip() for t in (host.check_type or "icmp").split(",") if t.strip()]
    results: list[tuple[bool, float | None]] = await asyncio.gather(
        *[_check_single(host, ct) for ct in types]
    )

    # Build per-check detail
    detail: dict = {}
    for ct, (ok, lat) in zip(types, results):
        label = ct
        if ct == "tcp" and host.port:
            label = f"tcp:{host.port}"
        detail[label] = ok

    # Determine online status from ICMP only
    icmp_types = [i for i, t in enumerate(types) if t == "icmp"]
    service_types = [i for i, t in enumerate(types) if t != "icmp"]

    if icmp_types:
        online = all(results[i][0] for i in icmp_types)
    else:
        # No ICMP configured — use all checks for online status
        online = any(r[0] for r in results)

    # Port error: host is online but a non-ICMP check failed
    port_error = False
    if online and service_types:
        port_error = any(not results[i][0] for i in service_types)

    # Latency: prefer ICMP
    primary: float | None = None
    if icmp_types:
        primary = results[icmp_types[0]][1]
    if primary is None:
        primary = next((r[1] for r in results if r[1] is not None), None)
    return online, port_error, primary, detail
