"""Subnet Scanner – discover hosts in a CIDR range and cross-reference with monitored hosts."""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from templating import templates

from models.base import get_db
from models.ping import PingHost
from models.scanner import SubnetScanSchedule
from utils.ping import ping_host

logger = logging.getLogger(__name__)
router = APIRouter()

# ── Helpers ──────────────────────────────────────────────────────────────────

MAX_SUBNET_SIZE = 1024  # /22 max to prevent accidental /8 scans
CONCURRENT_PINGS = 100


async def _reverse_dns(ip: str) -> str | None:
    """Reverse DNS lookup, returns hostname or None."""
    loop = asyncio.get_event_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(None, socket.gethostbyaddr, ip),
            timeout=2,
        )
        hostname = result[0]
        if hostname and hostname != ip:
            return hostname
    except (socket.herror, socket.gaierror, OSError, asyncio.TimeoutError):
        pass
    return None


async def _ping_one(sem: asyncio.Semaphore, ip: str) -> dict:
    async with sem:
        ok, latency = await ping_host(ip, timeout=1)
        dns_name = await _reverse_dns(ip) if ok else None
        return {
            "ip": ip,
            "alive": ok,
            "latency_ms": round(latency, 1) if latency else None,
            "dns_name": dns_name,
        }


async def scan_subnet(cidr: str) -> list[dict]:
    """Ping all hosts in a CIDR range concurrently."""
    network = ipaddress.ip_network(cidr, strict=False)
    hosts = [str(ip) for ip in network.hosts()]

    if len(hosts) > MAX_SUBNET_SIZE:
        raise ValueError(f"Subnet too large ({len(hosts)} hosts, max {MAX_SUBNET_SIZE})")

    sem = asyncio.Semaphore(CONCURRENT_PINGS)
    results = await asyncio.gather(*[_ping_one(sem, ip) for ip in hosts])
    return list(results)


def _build_monitored_index(all_hosts: list[PingHost]) -> dict[str, PingHost]:
    """Build a lookup dict for matching scan results to monitored hosts."""
    idx: dict[str, PingHost] = {}
    for h in all_hosts:
        hostname = (h.hostname or "").strip()
        for prefix in ("https://", "http://"):
            if hostname.startswith(prefix):
                hostname = hostname[len(prefix):]
        hostname = hostname.split("/")[0].split(":")[0]
        if hostname:
            idx[hostname.lower()] = h
        name = (h.name or "").strip()
        if name:
            idx[name.lower()] = h
    return idx


def _match_host(r: dict, idx: dict[str, PingHost]) -> PingHost | None:
    """Find a monitored host matching a scan result."""
    host = idx.get(r["ip"])
    if not host and r.get("dns_name"):
        dns = r["dns_name"]
        host = idx.get(dns.lower())
        if not host:
            host = idx.get(dns.split(".")[0].lower())
    return host


async def auto_add_hosts(db: AsyncSession, scan_results: list[dict]) -> int:
    """Add alive, unmonitored hosts to PingHosts. Returns count added."""
    q = await db.execute(select(PingHost))
    all_hosts = q.scalars().all()
    idx = _build_monitored_index(all_hosts)
    existing_ips = {(h.hostname or "").strip() for h in all_hosts}

    added = 0
    for r in scan_results:
        if not r["alive"]:
            continue
        if _match_host(r, idx) is not None:
            continue
        ip = r["ip"]
        if ip in existing_ips:
            continue
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            continue

        dns_name = r.get("dns_name") or ""
        display_name = dns_name.split(".")[0] if dns_name else ip

        host = PingHost(
            name=display_name,
            hostname=ip,
            check_type="icmp",
            enabled=True,
            source="scanner",
            source_detail=f"Auto-scan ({dns_name})" if dns_name else "Auto-scan",
        )
        db.add(host)
        existing_ips.add(ip)
        idx[ip] = host
        added += 1

    if added:
        await db.commit()
    return added


# ── Routes – Page ────────────────────────────────────────────────────────────


@router.get("/subnet-scanner")
async def subnet_scanner_page(request: Request, db: AsyncSession = Depends(get_db)):
    q = await db.execute(
        select(SubnetScanSchedule).order_by(SubnetScanSchedule.created_at.desc())
    )
    schedules = q.scalars().all()
    return templates.TemplateResponse(
        "subnet_scanner.html", {"request": request, "schedules": schedules}
    )


# ── Routes – Manual Scan ─────────────────────────────────────────────────────


@router.post("/api/subnet-scanner/scan")
async def api_scan_subnet(request: Request, db: AsyncSession = Depends(get_db)):
    """Scan a CIDR range and return results cross-referenced with monitored hosts."""
    body = await request.json()
    cidr = (body.get("cidr") or "").strip()

    try:
        network = ipaddress.ip_network(cidr, strict=False)
    except ValueError as e:
        return JSONResponse({"error": f"Invalid CIDR: {e}"}, status_code=400)

    host_count = sum(1 for _ in network.hosts())
    if host_count > MAX_SUBNET_SIZE:
        return JSONResponse(
            {"error": f"Subnet too large ({host_count} hosts, max {MAX_SUBNET_SIZE})"},
            status_code=400,
        )

    q = await db.execute(select(PingHost))
    all_hosts = q.scalars().all()
    idx = _build_monitored_index(all_hosts)

    scan_results = await scan_subnet(cidr)

    results = []
    for r in scan_results:
        host = _match_host(r, idx)
        results.append({
            **r,
            "monitored": host is not None,
            "host_id": host.id if host else None,
            "host_name": host.name if host else None,
            "host_enabled": host.enabled if host else None,
            "source": host.source if host else None,
        })

    alive_count = sum(1 for r in results if r["alive"])
    monitored_count = sum(1 for r in results if r["monitored"])
    unmonitored_alive = sum(1 for r in results if r["alive"] and not r["monitored"])

    return JSONResponse({
        "cidr": str(network),
        "total": len(results),
        "alive": alive_count,
        "monitored": monitored_count,
        "unmonitored_alive": unmonitored_alive,
        "results": results,
        "scanned_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
    })


@router.post("/api/subnet-scanner/add-hosts")
async def api_add_hosts(request: Request, db: AsyncSession = Depends(get_db)):
    """Add discovered IPs as monitored hosts."""
    body = await request.json()
    hosts_to_add = body.get("hosts", [])

    if not hosts_to_add:
        ips = body.get("ips", [])
        hosts_to_add = [{"ip": ip} for ip in ips] if ips else []

    if not hosts_to_add or not isinstance(hosts_to_add, list):
        return JSONResponse({"error": "No hosts provided"}, status_code=400)

    q = await db.execute(select(PingHost))
    existing = {(h.hostname or "").strip() for h in q.scalars().all()}

    added = 0
    for entry in hosts_to_add:
        ip = (entry.get("ip") or "").strip() if isinstance(entry, dict) else str(entry).strip()
        dns_name = (entry.get("dns_name") or "").strip() if isinstance(entry, dict) else ""
        if not ip or ip in existing:
            continue
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            continue

        display_name = dns_name.split(".")[0] if dns_name else ip

        host = PingHost(
            name=display_name,
            hostname=ip,
            check_type="icmp",
            enabled=True,
            source="scanner",
            source_detail=f"Subnet Scanner ({dns_name})" if dns_name else "Subnet Scanner",
        )
        db.add(host)
        existing.add(ip)
        added += 1

    if added:
        await db.commit()

    return JSONResponse({"added": added})


# ── Routes – Scheduled Scans CRUD ────────────────────────────────────────────


@router.post("/api/subnet-scanner/schedules")
async def api_create_schedule(request: Request, db: AsyncSession = Depends(get_db)):
    """Create a new scheduled scan."""
    body = await request.json()
    cidr = (body.get("cidr") or "").strip()
    name = (body.get("name") or "").strip()
    interval_m = int(body.get("interval_m", 60))
    auto_add = bool(body.get("auto_add", True))

    try:
        network = ipaddress.ip_network(cidr, strict=False)
    except ValueError as e:
        return JSONResponse({"error": f"Invalid CIDR: {e}"}, status_code=400)

    host_count = sum(1 for _ in network.hosts())
    if host_count > MAX_SUBNET_SIZE:
        return JSONResponse(
            {"error": f"Subnet too large ({host_count} hosts, max {MAX_SUBNET_SIZE})"},
            status_code=400,
        )

    if interval_m < 5:
        return JSONResponse({"error": "Minimum interval is 5 minutes"}, status_code=400)

    if not name:
        name = str(network)

    schedule = SubnetScanSchedule(
        name=name,
        cidr=str(network),
        interval_m=interval_m,
        auto_add=auto_add,
        enabled=True,
    )
    db.add(schedule)
    await db.commit()
    await db.refresh(schedule)

    return JSONResponse({"id": schedule.id, "name": schedule.name})


@router.delete("/api/subnet-scanner/schedules/{schedule_id}")
async def api_delete_schedule(schedule_id: int, db: AsyncSession = Depends(get_db)):
    """Delete a scheduled scan."""
    await db.execute(
        delete(SubnetScanSchedule).where(SubnetScanSchedule.id == schedule_id)
    )
    await db.commit()
    return JSONResponse({"ok": True})


@router.patch("/api/subnet-scanner/schedules/{schedule_id}")
async def api_toggle_schedule(schedule_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    """Toggle enabled/disabled or update fields."""
    body = await request.json()
    q = await db.execute(
        select(SubnetScanSchedule).where(SubnetScanSchedule.id == schedule_id)
    )
    schedule = q.scalar_one_or_none()
    if not schedule:
        return JSONResponse({"error": "Not found"}, status_code=404)

    if "enabled" in body:
        schedule.enabled = bool(body["enabled"])
    if "auto_add" in body:
        schedule.auto_add = bool(body["auto_add"])
    if "interval_m" in body:
        schedule.interval_m = max(5, int(body["interval_m"]))
    if "name" in body:
        schedule.name = body["name"]

    await db.commit()
    return JSONResponse({"ok": True})


# ── Scheduled scan runner (called by scheduler) ─────────────────────────────


async def run_scheduled_scans():
    """Run all due scheduled subnet scans."""
    from database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        q = await db.execute(
            select(SubnetScanSchedule).where(SubnetScanSchedule.enabled == True)
        )
        schedules = q.scalars().all()

    for sched in schedules:
        # Check if scan is due
        if sched.last_scan:
            from datetime import timedelta
            next_due = sched.last_scan + timedelta(minutes=sched.interval_m)
            if datetime.utcnow() < next_due:
                continue

        try:
            scan_results = await scan_subnet(sched.cidr)
            alive = sum(1 for r in scan_results if r["alive"])
            total = len(scan_results)
            added = 0

            if sched.auto_add:
                async with AsyncSessionLocal() as db:
                    added = await auto_add_hosts(db, scan_results)

            # Update schedule stats
            async with AsyncSessionLocal() as db:
                q = await db.execute(
                    select(SubnetScanSchedule).where(SubnetScanSchedule.id == sched.id)
                )
                s = q.scalar_one_or_none()
                if s:
                    s.last_scan = datetime.utcnow()
                    s.last_alive = alive
                    s.last_total = total
                    s.last_added = added
                    await db.commit()

            logger.info(
                "Scheduled scan [%s] %s: %d/%d alive, %d added",
                sched.name, sched.cidr, alive, total, added,
            )
        except Exception as exc:
            logger.error("Scheduled scan [%s] failed: %s", sched.name, exc)
