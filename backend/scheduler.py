"""
Background scheduler – runs collection jobs periodically.
"""
import json
import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import delete, select

from collectors.ping import check_host, get_ssl_expiry_days
from collectors.proxmox import ProxmoxAPI, import_proxmox_hosts, parse_cluster_data
from collectors.unifi import UnifiAPI, import_unifi_devices
from collectors.unas import UnasAPI
from collectors.synology import SynologyAPI, parse_synology_data
from collectors.firewall import OPNsenseAPI, PfsenseAPI, parse_opnsense_data, parse_pfsense_data
from collectors.hass import HassAPI, parse_hass_data
from collectors.gitea import GiteaAPI, parse_gitea_data
from collectors.nut import NutClient
from collectors.redfish import RedfishAPI
from collectors.speedtest import run_speedtest
from collectors.pihole import PiholeAPI
from collectors.adguard import AdguardAPI, parse_adguard_data
from collectors.portainer import PortainerAPI
from collectors.truenas import TruenasAPI
from database import (
    AsyncSessionLocal, PingHost, PingResult,
    ProxmoxCluster, ProxmoxSnapshot,
    UnifiController, UnifiSnapshot,
    UnasServer, UnasSnapshot,
    SynologyServer, SynologySnapshot,
    FirewallInstance, FirewallSnapshot,
    HassInstance, HassSnapshot,
    GiteaInstance, GiteaSnapshot,
    NutInstance, NutSnapshot,
    RedfishServer, RedfishSnapshot,
    SpeedtestConfig, SpeedtestResult,
    PiholeInstance, PiholeSnapshot,
    AdguardInstance, AdguardSnapshot,
    PortainerInstance, PortainerSnapshot,
    TruenasServer, TruenasSnapshot,
    decrypt_value,
)

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()


async def run_ping_checks():
    """Ping all enabled hosts and store results."""
    import asyncio as _asyncio
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(PingHost).where(PingHost.enabled == True))
        hosts = result.scalars().all()

    if not hosts:
        return

    # Load previous results for state-change detection
    async with AsyncSessionLocal() as db:
        from sqlalchemy import func as sa_func
        # Subquery: latest timestamp per host_id
        sub = (
            select(PingResult.host_id, sa_func.max(PingResult.timestamp).label("max_ts"))
            .group_by(PingResult.host_id)
            .subquery()
        )
        prev_rows = await db.execute(
            select(PingResult.host_id, PingResult.success)
            .join(sub, (PingResult.host_id == sub.c.host_id) & (PingResult.timestamp == sub.c.max_ts))
        )
        prev_success: dict[int, bool] = {row.host_id: row.success for row in prev_rows}

    async with AsyncSessionLocal() as db:
        for host in hosts:
            if host.maintenance:
                continue
            success, latency = await check_host(host)
            db.add(PingResult(
                host_id=host.id,
                timestamp=datetime.utcnow(),
                success=success,
                latency_ms=latency,
            ))

            # Notify on state change
            prev = prev_success.get(host.id)
            if prev is True and not success:
                from notifications import notify
                _asyncio.create_task(notify(
                    f"Host offline: {host.name}",
                    f"Host {host.hostname} ist nicht mehr erreichbar.",
                    "critical"
                ))
            elif prev is False and success:
                from notifications import notify
                _asyncio.create_task(notify(
                    f"Host wieder online: {host.name}",
                    f"Host {host.hostname} ist wieder erreichbar.",
                    "info"
                ))

        await db.commit()

    logger.debug("Ping check done for %d hosts", len(hosts))


async def run_proxmox_checks():
    """Fetch status for all Proxmox clusters and store a snapshot."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(ProxmoxCluster))
        clusters = result.scalars().all()

    if not clusters:
        return

    async with AsyncSessionLocal() as db:
        for cluster in clusters:
            try:
                api = ProxmoxAPI(
                    host=cluster.host,
                    token_id=cluster.token_id,
                    token_secret=decrypt_value(cluster.token_secret),
                    verify_ssl=cluster.verify_ssl,
                )
                resources = await api.cluster_resources()
                status = await api.cluster_status()
                data = parse_cluster_data(resources, status)
                db.add(ProxmoxSnapshot(
                    cluster_id=cluster.id,
                    timestamp=datetime.utcnow(),
                    ok=True,
                    data_json=json.dumps(data),
                    error=None,
                ))
                await db.flush()  # ensure snapshot is written before import
                stats = await import_proxmox_hosts(cluster.name, data, db)
                if stats["added"] > 0:
                    logger.info("Proxmox auto-import [%s]: +%d added, %d merged",
                                cluster.name, stats["added"], stats["merged"])
            except Exception as exc:
                db.add(ProxmoxSnapshot(
                    cluster_id=cluster.id,
                    timestamp=datetime.utcnow(),
                    ok=False,
                    data_json=None,
                    error=str(exc),
                ))
        await db.commit()

    logger.debug("Proxmox check done for %d cluster(s)", len(clusters))


async def update_ssl_expiry():
    """Update ssl_expiry_days for all HTTPS hosts."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(PingHost).where(PingHost.enabled == True, PingHost.check_type == "https")
        )
        hosts = result.scalars().all()

    if not hosts:
        return

    async with AsyncSessionLocal() as db:
        for host in hosts:
            hostname = host.hostname
            # Strip scheme if present
            for prefix in ("https://", "http://"):
                if hostname.startswith(prefix):
                    hostname = hostname[len(prefix):]
                    break
            hostname = hostname.split("/")[0].split(":")[0]
            days = await get_ssl_expiry_days(hostname, port=host.port or 443)
            if days is not None:
                from sqlalchemy import update as sa_update
                await db.execute(
                    sa_update(PingHost).where(PingHost.id == host.id).values(ssl_expiry_days=days)
                )
        await db.commit()
    logger.debug("SSL expiry updated for %d HTTPS host(s)", len(hosts))


async def run_unifi_checks():
    """Fetch status for all UniFi controllers and store a snapshot."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(UnifiController))
        controllers = result.scalars().all()

    if not controllers:
        return

    async with AsyncSessionLocal() as db:
        for ctrl in controllers:
            try:
                api = UnifiAPI(
                    host       = ctrl.host,
                    username   = ctrl.username,
                    password   = decrypt_value(ctrl.password_enc),
                    site       = ctrl.site,
                    verify_ssl = ctrl.verify_ssl,
                    is_udm     = ctrl.is_udm,
                )
                data = await api.fetch_all()
                db.add(UnifiSnapshot(
                    controller_id = ctrl.id,
                    timestamp     = datetime.utcnow(),
                    ok            = True,
                    data_json     = json.dumps(data),
                    error         = None,
                ))
                await db.flush()
                stats = await import_unifi_devices(ctrl.name, data, db)
                if stats["added"] > 0:
                    logger.info("UniFi auto-import [%s]: +%d added, %d merged",
                                ctrl.name, stats["added"], stats["merged"])
            except Exception as exc:
                db.add(UnifiSnapshot(
                    controller_id = ctrl.id,
                    timestamp     = datetime.utcnow(),
                    ok            = False,
                    data_json     = None,
                    error         = str(exc),
                ))
        await db.commit()

    logger.debug("UniFi check done for %d controller(s)", len(controllers))


async def cleanup_old_results():
    """Remove old ping results and integration snapshots based on configured retention."""
    now = datetime.utcnow()
    async with AsyncSessionLocal() as db:
        from database import get_setting
        ping_days  = int(await get_setting(db, "ping_retention_days", "30"))
        px_days    = int(await get_setting(db, "proxmox_retention_days", "7"))
        unifi_days = int(await get_setting(db, "unifi_retention_days", "2"))
        int_days   = int(await get_setting(db, "integration_retention_days", "7"))

        await db.execute(delete(PingResult).where(PingResult.timestamp < now - timedelta(days=ping_days)))
        await db.execute(delete(ProxmoxSnapshot).where(ProxmoxSnapshot.timestamp < now - timedelta(days=px_days)))
        await db.execute(delete(UnifiSnapshot).where(UnifiSnapshot.timestamp < now - timedelta(days=unifi_days)))

        int_cutoff = now - timedelta(days=int_days)
        for snap_model in [
            UnasSnapshot, PiholeSnapshot, AdguardSnapshot,
            PortainerSnapshot, TruenasSnapshot, SynologySnapshot,
            FirewallSnapshot, HassSnapshot, GiteaSnapshot,
            NutSnapshot, RedfishSnapshot,
        ]:
            await db.execute(delete(snap_model).where(snap_model.timestamp < int_cutoff))

        # SpeedtestResult uses timestamp column too
        await db.execute(delete(SpeedtestResult).where(SpeedtestResult.timestamp < int_cutoff))

        await db.commit()
    logger.info("Cleaned up old results (ping >%dd, proxmox >%dd, unifi >%dd, integrations >%dd)",
                ping_days, px_days, unifi_days, int_days)


async def run_pihole_checks():
    """Fetch stats for all Pi-hole instances and store a snapshot."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(PiholeInstance))
        instances = result.scalars().all()

    if not instances:
        return

    async with AsyncSessionLocal() as db:
        for inst in instances:
            try:
                api_key = decrypt_value(inst.api_key_enc) if inst.api_key_enc else None
                api = PiholeAPI(host=inst.host, api_key=api_key, verify_ssl=inst.verify_ssl)
                data = await api.fetch_all()
                db.add(PiholeSnapshot(
                    instance_id=inst.id, timestamp=datetime.utcnow(),
                    ok=True, data_json=json.dumps(data), error=None,
                ))
            except Exception as exc:
                db.add(PiholeSnapshot(
                    instance_id=inst.id, timestamp=datetime.utcnow(),
                    ok=False, data_json=None, error=str(exc),
                ))
        await db.commit()
    logger.debug("Pi-hole check done for %d instance(s)", len(instances))


async def run_adguard_checks():
    """Fetch stats for all AdGuard Home instances and store a snapshot."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(AdguardInstance))
        instances = result.scalars().all()

    if not instances:
        return

    async with AsyncSessionLocal() as db:
        for inst in instances:
            try:
                password = decrypt_value(inst.password_enc) if inst.password_enc else None
                api = AdguardAPI(host=inst.host, username=inst.username,
                                 password=password, verify_ssl=inst.verify_ssl)
                data = await api.fetch_all()
                db.add(AdguardSnapshot(
                    instance_id=inst.id, timestamp=datetime.utcnow(),
                    ok=True, data_json=json.dumps(data), error=None,
                ))
            except Exception as exc:
                db.add(AdguardSnapshot(
                    instance_id=inst.id, timestamp=datetime.utcnow(),
                    ok=False, data_json=None, error=str(exc),
                ))
        await db.commit()
    logger.debug("AdGuard check done for %d instance(s)", len(instances))


async def run_portainer_checks():
    """Fetch container status for all Portainer instances and store a snapshot."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(PortainerInstance))
        instances = result.scalars().all()

    if not instances:
        return

    async with AsyncSessionLocal() as db:
        for inst in instances:
            try:
                api_key = decrypt_value(inst.api_key_enc) if inst.api_key_enc else None
                api = PortainerAPI(host=inst.host, api_key=api_key, verify_ssl=inst.verify_ssl)
                data = await api.fetch_all()
                db.add(PortainerSnapshot(
                    instance_id=inst.id, timestamp=datetime.utcnow(),
                    ok=True, data_json=json.dumps(data), error=None,
                ))
            except Exception as exc:
                db.add(PortainerSnapshot(
                    instance_id=inst.id, timestamp=datetime.utcnow(),
                    ok=False, data_json=None, error=str(exc),
                ))
        await db.commit()
    logger.debug("Portainer check done for %d instance(s)", len(instances))


async def run_truenas_checks():
    """Fetch pool/disk stats for all TrueNAS servers and store a snapshot."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(TruenasServer))
        servers = result.scalars().all()

    if not servers:
        return

    async with AsyncSessionLocal() as db:
        for srv in servers:
            try:
                api_key = decrypt_value(srv.api_key_enc) if srv.api_key_enc else ""
                api = TruenasAPI(host=srv.host, api_key=api_key, verify_ssl=srv.verify_ssl)
                data = await api.fetch_all()
                db.add(TruenasSnapshot(
                    server_id=srv.id, timestamp=datetime.utcnow(),
                    ok=True, data_json=json.dumps(data), error=None,
                ))
            except Exception as exc:
                db.add(TruenasSnapshot(
                    server_id=srv.id, timestamp=datetime.utcnow(),
                    ok=False, data_json=None, error=str(exc),
                ))
        await db.commit()
    logger.debug("TrueNAS check done for %d server(s)", len(servers))


async def run_synology_checks():
    """Fetch status for all Synology NAS servers and store a snapshot."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(SynologyServer))
        servers = result.scalars().all()

    if not servers:
        return

    async with AsyncSessionLocal() as db:
        for srv in servers:
            try:
                api = SynologyAPI(
                    host       = srv.host,
                    port       = srv.port,
                    username   = srv.username,
                    password   = decrypt_value(srv.password_enc),
                    verify_ssl = srv.verify_ssl,
                )
                raw  = await api.fetch_all()
                data = parse_synology_data(raw["info"], raw["storage"], raw["load"])
                db.add(SynologySnapshot(
                    server_id = srv.id,
                    timestamp = datetime.utcnow(),
                    ok        = True,
                    data_json = json.dumps(data),
                    error     = None,
                ))
            except Exception as exc:
                db.add(SynologySnapshot(
                    server_id = srv.id,
                    timestamp = datetime.utcnow(),
                    ok        = False,
                    data_json = None,
                    error     = str(exc),
                ))
        await db.commit()

    logger.debug("Synology check done for %d server(s)", len(servers))


async def run_firewall_checks():
    """Fetch status for all firewall instances and store a snapshot."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(FirewallInstance))
        instances = result.scalars().all()

    if not instances:
        return

    async with AsyncSessionLocal() as db:
        for inst in instances:
            try:
                if inst.fw_type == "opnsense":
                    api_key    = decrypt_value(inst.api_key_enc)    if inst.api_key_enc    else ""
                    api_secret = decrypt_value(inst.api_secret_enc) if inst.api_secret_enc else ""
                    api  = OPNsenseAPI(
                        host       = inst.host,
                        api_key    = api_key,
                        api_secret = api_secret,
                        verify_ssl = inst.verify_ssl,
                    )
                    raw  = await api.fetch_all()
                    data = parse_opnsense_data(raw["firmware"], raw["status"])
                    # Enrich interface list
                    ifaces_raw = raw.get("interfaces", {})
                    if isinstance(ifaces_raw, dict):
                        data["interfaces"] = [
                            {"name": k, "description": v, "ipv4": "", "status": "up"}
                            for k, v in ifaces_raw.items()
                        ]
                else:
                    username = inst.username or ""
                    password = decrypt_value(inst.password_enc) if inst.password_enc else ""
                    api  = PfsenseAPI(
                        host       = inst.host,
                        username   = username,
                        password   = password,
                        verify_ssl = inst.verify_ssl,
                    )
                    raw  = await api.fetch_all()
                    data = parse_pfsense_data(raw.get("sys_info", {}))
                    ifaces_raw = raw.get("interfaces", {})
                    data_ifaces = ifaces_raw.get("data", ifaces_raw) if isinstance(ifaces_raw, dict) else {}
                    if isinstance(data_ifaces, dict):
                        data["interfaces"] = [
                            {
                                "name": k,
                                "description": v.get("descr", k) if isinstance(v, dict) else str(v),
                                "ipv4": v.get("ipaddr", "") if isinstance(v, dict) else "",
                                "status": v.get("status", "up") if isinstance(v, dict) else "up",
                            }
                            for k, v in data_ifaces.items()
                        ]

                db.add(FirewallSnapshot(
                    instance_id = inst.id,
                    timestamp   = datetime.utcnow(),
                    ok          = True,
                    data_json   = json.dumps(data),
                    error       = None,
                ))
            except Exception as exc:
                db.add(FirewallSnapshot(
                    instance_id = inst.id,
                    timestamp   = datetime.utcnow(),
                    ok          = False,
                    data_json   = None,
                    error       = str(exc),
                ))
        await db.commit()

    logger.debug("Firewall check done for %d instance(s)", len(instances))


async def run_hass_checks():
    """Fetch status for all Home Assistant instances and store a snapshot."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(HassInstance))
        instances = result.scalars().all()

    if not instances:
        return

    async with AsyncSessionLocal() as db:
        for inst in instances:
            try:
                token = decrypt_value(inst.token_enc)
                api = HassAPI(host=inst.host, token=token, verify_ssl=inst.verify_ssl)
                raw = await api.fetch_all()
                data = parse_hass_data(raw["config"], raw["states"])
                db.add(HassSnapshot(
                    instance_id=inst.id,
                    timestamp=datetime.utcnow(),
                    ok=True,
                    data_json=json.dumps(data),
                    error=None,
                ))
            except Exception as exc:
                db.add(HassSnapshot(
                    instance_id=inst.id,
                    timestamp=datetime.utcnow(),
                    ok=False,
                    data_json=None,
                    error=str(exc),
                ))
        await db.commit()

    logger.debug("Home Assistant check done for %d instance(s)", len(instances))


async def run_gitea_checks():
    """Fetch status for all Gitea instances and store a snapshot."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(GiteaInstance))
        instances = result.scalars().all()

    if not instances:
        return

    async with AsyncSessionLocal() as db:
        for inst in instances:
            try:
                token = decrypt_value(inst.token_enc) if inst.token_enc else None
                api = GiteaAPI(host=inst.host, token=token, verify_ssl=inst.verify_ssl)
                raw = await api.fetch_all()
                data = parse_gitea_data(raw["version_info"], raw["repos"], raw["users"], raw["orgs"])
                db.add(GiteaSnapshot(
                    instance_id=inst.id,
                    timestamp=datetime.utcnow(),
                    ok=True,
                    data_json=json.dumps(data),
                    error=None,
                ))
            except Exception as exc:
                db.add(GiteaSnapshot(
                    instance_id=inst.id,
                    timestamp=datetime.utcnow(),
                    ok=False,
                    data_json=None,
                    error=str(exc),
                ))
        await db.commit()

    logger.debug("Gitea check done for %d instance(s)", len(instances))


async def run_phpipam_sync():
    """Sync hosts from phpIPAM if configured and auto-sync is enabled."""
    async with AsyncSessionLocal() as db:
        from database import get_setting
        sync_hours = int(await get_setting(db, "phpipam_sync_hours", "0"))
        if sync_hours <= 0:
            return

    from collectors.phpipam import sync_phpipam_hosts
    async with AsyncSessionLocal() as db:
        result = await sync_phpipam_hosts(db)
        logger.info("phpIPAM auto-sync: %s", result)


async def run_unas_checks():
    """Fetch status for all UniFi NAS servers and store a snapshot."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(UnasServer))
        servers = result.scalars().all()

    if not servers:
        return

    async with AsyncSessionLocal() as db:
        for srv in servers:
            try:
                api  = UnasAPI(
                    host       = srv.host,
                    username   = srv.username,
                    password   = decrypt_value(srv.password_enc),
                    verify_ssl = srv.verify_ssl,
                )
                data = await api.fetch_all()
                db.add(UnasSnapshot(
                    server_id = srv.id,
                    timestamp = datetime.utcnow(),
                    ok        = True,
                    data_json = json.dumps(data),
                    error     = None,
                ))
            except Exception as exc:
                db.add(UnasSnapshot(
                    server_id = srv.id,
                    timestamp = datetime.utcnow(),
                    ok        = False,
                    data_json = None,
                    error     = str(exc),
                ))
        await db.commit()

    logger.debug("UniFi NAS check done for %d server(s)", len(servers))


async def run_nut_checks():
    """Fetch status for all NUT UPS instances and store a snapshot."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(NutInstance))
        instances = result.scalars().all()

    if not instances:
        return

    async with AsyncSessionLocal() as db:
        for inst in instances:
            try:
                password = decrypt_value(inst.password_enc) if inst.password_enc else None
                client = NutClient(
                    host     = inst.host,
                    port     = inst.port,
                    ups_name = inst.ups_name,
                    username = inst.username,
                    password = password,
                )
                data = await client.fetch_all()
                db.add(NutSnapshot(
                    instance_id = inst.id,
                    timestamp   = datetime.utcnow(),
                    ok          = True,
                    data_json   = json.dumps(data),
                    error       = None,
                ))
            except Exception as exc:
                db.add(NutSnapshot(
                    instance_id = inst.id,
                    timestamp   = datetime.utcnow(),
                    ok          = False,
                    data_json   = None,
                    error       = str(exc),
                ))
        await db.commit()

    logger.debug("NUT check done for %d instance(s)", len(instances))


async def run_redfish_checks():
    """Fetch status for all Redfish servers and store a snapshot."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(RedfishServer))
        servers = result.scalars().all()

    if not servers:
        return

    async with AsyncSessionLocal() as db:
        for srv in servers:
            try:
                api = RedfishAPI(
                    host       = srv.host,
                    username   = srv.username,
                    password   = decrypt_value(srv.password_enc),
                    verify_ssl = srv.verify_ssl,
                )
                data = await api.fetch_all()
                db.add(RedfishSnapshot(
                    server_id = srv.id,
                    timestamp = datetime.utcnow(),
                    ok        = True,
                    data_json = json.dumps(data),
                    error     = None,
                ))
            except Exception as exc:
                db.add(RedfishSnapshot(
                    server_id = srv.id,
                    timestamp = datetime.utcnow(),
                    ok        = False,
                    data_json = None,
                    error     = str(exc),
                ))
        await db.commit()

    logger.debug("Redfish check done for %d server(s)", len(servers))


async def run_speedtest_checks():
    """Run scheduled speedtest and store results."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(SpeedtestConfig).limit(1))
        cfg = result.scalar_one_or_none()

    if cfg is None:
        return  # no config yet

    try:
        data = await run_speedtest(cfg.server_id)
        async with AsyncSessionLocal() as db:
            db.add(SpeedtestResult(
                config_id     = cfg.id,
                timestamp     = datetime.utcnow(),
                ok            = True,
                download_mbps = data["download_mbps"],
                upload_mbps   = data["upload_mbps"],
                ping_ms       = data["ping_ms"],
                server_name   = data["server_name"],
                error         = None,
            ))
            await db.commit()
        logger.info("Scheduled speedtest: %.1f / %.1f Mbps, %.0f ms",
                    data["download_mbps"], data["upload_mbps"], data["ping_ms"])
    except Exception as exc:
        logger.warning("Scheduled speedtest failed: %s", exc)
        async with AsyncSessionLocal() as db:
            db.add(SpeedtestResult(
                config_id = cfg.id,
                timestamp = datetime.utcnow(),
                ok        = False,
                error     = str(exc),
            ))
            await db.commit()


def start_scheduler():
    scheduler.add_job(run_ping_checks,    "interval", seconds=60, id="ping_checks",    replace_existing=True)
    scheduler.add_job(run_proxmox_checks, "interval", seconds=60, id="proxmox_checks", replace_existing=True)
    scheduler.add_job(run_unifi_checks,   "interval", seconds=30, id="unifi_checks",   replace_existing=True)
    scheduler.add_job(run_unas_checks,      "interval", seconds=60,  id="unas_checks",      replace_existing=True)
    scheduler.add_job(run_pihole_checks,    "interval", seconds=60,  id="pihole_checks",    replace_existing=True)
    scheduler.add_job(run_adguard_checks,   "interval", seconds=60,  id="adguard_checks",   replace_existing=True)
    scheduler.add_job(run_portainer_checks, "interval", seconds=60,  id="portainer_checks", replace_existing=True)
    scheduler.add_job(run_truenas_checks,   "interval", seconds=120, id="truenas_checks",   replace_existing=True)
    scheduler.add_job(run_synology_checks,  "interval", seconds=60,  id="synology_checks",  replace_existing=True)
    scheduler.add_job(run_firewall_checks,  "interval", seconds=60,  id="firewall_checks",  replace_existing=True)
    scheduler.add_job(run_hass_checks,      "interval", seconds=60,  id="hass_checks",      replace_existing=True)
    scheduler.add_job(run_gitea_checks,     "interval", seconds=120, id="gitea_checks",     replace_existing=True)
    scheduler.add_job(run_nut_checks,       "interval", seconds=60,  id="nut_checks",       replace_existing=True)
    scheduler.add_job(run_redfish_checks,   "interval", seconds=120, id="redfish_checks",   replace_existing=True)
    scheduler.add_job(run_speedtest_checks, "interval", minutes=60,  id="speedtest_checks", replace_existing=True)
    scheduler.add_job(update_ssl_expiry,    "interval", hours=6,     id="ssl_expiry",       replace_existing=True)
    scheduler.add_job(cleanup_old_results,  "cron", hour=3, minute=0, id="cleanup",         replace_existing=True)
    # phpIPAM: registered dynamically when settings are saved; check on startup too
    scheduler.add_job(run_phpipam_sync, "interval", hours=1, id="phpipam_sync", replace_existing=True)
    scheduler.start()
    logger.info("Scheduler started")


def stop_scheduler():
    scheduler.shutdown(wait=False)
