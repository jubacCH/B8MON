"""
Correlation engine – runs periodically (60s) to detect and group related issues
into Incidents. Supports deduplication and auto-resolve.

Rules:
1. host_down_syslog  – Host offline + syslog errors from same host (5min window)
2. multi_host_down   – 3+ hosts offline simultaneously → network problem
3. integration_host  – Integration unreachable + associated host offline
4. syslog_spike      – Syslog error rate 5x above baseline
5. log_anomaly       – Per-host log volume > baseline + 3σ
6. port_error        – Host online (ICMP OK) but service check (HTTP/HTTPS/TCP) failed
7. fleet_wide_issue  – Same template on 3+ hosts simultaneously
8. severity_trend    – Error template with rising frequency trend
9. content_anomaly   – New templates on stable host / severity upgrade
"""
import asyncio
import hashlib
import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select

from models.base import AsyncSessionLocal
from models.ping import PingHost
from services.clickhouse_client import query as ch_query
from services.clickhouse_client import query_scalar as ch_scalar
from models.integration import IntegrationConfig
from models.incident import Incident, IncidentEvent
from models.log_template import HostBaseline, LogTemplate, PrecursorPattern
from services.predictor_config import (
    get_blacklist_regexes,
    get_min_confidence,
    get_min_occurrences,
    is_template_blacklisted,
)
from services.topology import build_topology, filter_upstream_failures

log = logging.getLogger("nodeglow.correlation")

# ── Consecutive-failure state (in-memory, resets on restart) ────────────────
# Key = (rule_name, host_ids_hash), value = consecutive cycle count
_rule_hit_counts: dict[tuple[str, str], int] = {}
# Tracks which keys were seen in the current correlation cycle
_current_cycle_hits: set[tuple[str, str]] = set()


def _host_ids_hash(host_ids: list[int]) -> str:
    """Deterministic hash of sorted host IDs for dedup."""
    return hashlib.sha256(",".join(str(i) for i in sorted(host_ids)).encode()).hexdigest()[:16]


def _track_rule_hit(rule: str, host_ids: list[int], min_cycles: int) -> bool:
    """Track consecutive correlation cycles where a rule matches.

    Returns True if the rule has matched for >= min_cycles consecutive runs.
    Counters for combos not seen in a cycle are reset automatically via
    _prune_stale_hits() at end of each correlation run.
    """
    key = (rule, _host_ids_hash(host_ids))
    _current_cycle_hits.add(key)
    _rule_hit_counts[key] = _rule_hit_counts.get(key, 0) + 1
    return _rule_hit_counts[key] >= min_cycles


def _prune_stale_hits():
    """Remove hit counters for combos that didn't match this cycle."""
    stale = [k for k in _rule_hit_counts if k not in _current_cycle_hits]
    for k in stale:
        del _rule_hit_counts[k]
    _current_cycle_hits.clear()


async def cleanup_incident_events(db, retention_days: int) -> int:
    """Delete incident events older than retention_days.

    Open incidents accumulate one event per correlation cycle, so this table
    grows without bound. The newest meaningful (non-ack/resolve) event per
    incident is always kept — it backs the summary shown in the incident list.
    A retention of 0 disables pruning. Returns the number of deleted rows.
    """
    if retention_days <= 0:
        return 0
    cutoff = datetime.utcnow() - timedelta(days=retention_days)
    keep_latest = (
        select(func.max(IncidentEvent.id))
        .where(IncidentEvent.event_type.notin_(["acknowledged", "resolved"]))
        .group_by(IncidentEvent.incident_id)
        .scalar_subquery()
    )
    result = await db.execute(
        delete(IncidentEvent).where(
            IncidentEvent.timestamp < cutoff,
            IncidentEvent.id.notin_(keep_latest),
        )
    )
    return result.rowcount or 0


async def _find_or_create_incident(
    db, rule: str, title: str, severity: str,
    host_ids: list[int], event_type: str, summary: str, detail: str = None,
) -> Incident:
    """Find existing open incident for this rule+hosts combo, or create new one."""
    h = _host_ids_hash(host_ids)

    existing = (await db.execute(
        select(Incident).where(
            Incident.rule == rule,
            Incident.host_ids_hash == h,
            Incident.status.in_(["open", "acknowledged"]),
        )
    )).scalar_one_or_none()

    if existing:
        # Append event to existing incident
        existing.updated_at = datetime.utcnow()
        db.add(IncidentEvent(
            incident_id=existing.id,
            event_type=event_type,
            summary=summary,
            detail=detail,
        ))
        return existing

    # Create new incident
    incident = Incident(
        rule=rule,
        title=title,
        severity=severity,
        host_ids_hash=h,
    )
    db.add(incident)
    await db.flush()

    db.add(IncidentEvent(
        incident_id=incident.id,
        event_type="created",
        summary=summary,
        detail=detail,
    ))

    # Send notification for new incidents
    try:
        from notifications import notify
        await notify(
            f"🔴 Incident: {title}",
            summary,
            severity=severity,
        )
    except Exception as exc:
        log.warning("Failed to send incident notification: %s", exc)

    return incident


async def _get_offline_hosts(db, min_failures: int = 3) -> list[PingHost]:
    """Get hosts that are offline: last *min_failures* consecutive pings all failed.

    Single ClickHouse query — no per-host N+1.
    """
    from services.clickhouse_client import get_offline_hosts_since

    hosts_q = await db.execute(
        select(PingHost).where(PingHost.enabled == True, PingHost.maintenance == False)
    )
    hosts = hosts_q.scalars().all()
    if not hosts:
        return []

    offline_ids = set(await get_offline_hosts_since(
        [h.id for h in hosts], min_failures=min_failures,
    ))
    return [h for h in hosts if h.id in offline_ids]


# ── Topology cache (refreshed once per correlation run) ────────────────────

_topo_cache: dict[int, int | None] = {}
_topo_cache_ts: datetime | None = None


async def _get_topology(db) -> dict[int, int | None]:
    """Get topology with 60s cache."""
    global _topo_cache, _topo_cache_ts
    now = datetime.utcnow()
    if _topo_cache_ts and (now - _topo_cache_ts).total_seconds() < 60:
        return _topo_cache
    try:
        _topo_cache = await build_topology(db)
        _topo_cache_ts = now
    except Exception:
        log.debug("Failed to build topology", exc_info=True)
    return _topo_cache


# ── Rule 1: Host Down + Syslog Errors ───────────────────────────────────────

async def _rule_host_down_syslog(db, min_failures: int = 3, min_cycles: int = 2):
    """Host offline AND syslog severity <= 3 from same host in 5min window.
    Skips hosts whose upstream parent is also offline (topology cascading)."""
    offline_hosts = await _get_offline_hosts(db, min_failures)
    if not offline_hosts:
        return

    # Filter out cascaded failures
    topology = await _get_topology(db)
    offline_ids = {h.id for h in offline_hosts}
    primary_ids, cascaded_ids = filter_upstream_failures(offline_ids, topology)

    window = datetime.utcnow() - timedelta(minutes=5)

    for host in offline_hosts:
        if host.id in cascaded_ids:
            continue  # upstream is down — suppress individual alert

        # Check for error-level syslog messages from this host
        syslog_count = int(await ch_scalar(
            "SELECT count() FROM syslog_messages WHERE host_id = {hid:Int32} AND severity <= 3 AND timestamp >= {t:DateTime64(3)}",
            {"hid": host.id, "t": window},
        ) or 0)

        if syslog_count > 0:
            if not _track_rule_hit("host_down_syslog", [host.id], min_cycles):
                continue
            await _find_or_create_incident(
                db,
                rule="host_down_syslog",
                title=f"{host.name} offline with syslog errors",
                severity="critical",
                host_ids=[host.id],
                event_type="host_down",
                summary=f"{host.name} ({host.hostname}) is offline with {syslog_count} syslog errors in the last 5min",
            )

    # Create a single upstream-failure incident if cascaded hosts exist
    if cascaded_ids:
        # Find the upstream root causes
        cascade_hosts = [h for h in offline_hosts if h.id in cascaded_ids]
        parent_names = set()
        for hid in cascaded_ids:
            from services.topology import get_ancestors
            ancestors = get_ancestors(topology, hid)
            for a in ancestors:
                if a in primary_ids:
                    ph = next((h for h in offline_hosts if h.id == a), None)
                    if ph:
                        parent_names.add(ph.name)
                    break

        names = ", ".join(h.name for h in cascade_hosts[:5])
        if len(cascade_hosts) > 5:
            names += f" (+{len(cascade_hosts) - 5} more)"
        upstream_label = ", ".join(parent_names) if parent_names else "upstream device"

        if not _track_rule_hit("upstream_failure", list(cascaded_ids), min_cycles):
            return
        await _find_or_create_incident(
            db,
            rule="upstream_failure",
            title=f"Upstream failure: {len(cascaded_ids)} hosts affected",
            severity="warning",
            host_ids=list(cascaded_ids),
            event_type="host_down",
            summary=f"{len(cascaded_ids)} hosts offline due to upstream failure ({upstream_label}): {names}",
        )


# ── Rule 2: Multi-Host Down ─────────────────────────────────────────────────

async def _rule_multi_host_down(db, min_failures: int = 3, min_cycles: int = 2):
    """3+ hosts offline simultaneously → likely network problem.
    Excludes hosts already explained by upstream failure."""
    offline_hosts = await _get_offline_hosts(db, min_failures)
    if len(offline_hosts) < 3:
        return

    # Filter out cascaded failures (already handled by rule 1)
    topology = await _get_topology(db)
    offline_ids = {h.id for h in offline_hosts}
    primary_ids, _ = filter_upstream_failures(offline_ids, topology)
    primary_hosts = [h for h in offline_hosts if h.id in primary_ids]

    if len(primary_hosts) < 3:
        return

    # Group by /24 subnet (simple heuristic)
    subnets: dict[str, list[PingHost]] = {}
    for host in primary_hosts:
        hostname = host.hostname.strip()
        parts = hostname.split(".")
        if len(parts) == 4 and all(p.isdigit() for p in parts):
            subnet = ".".join(parts[:3]) + ".0/24"
        else:
            subnet = "unknown"
        subnets.setdefault(subnet, []).append(host)

    for subnet, hosts in subnets.items():
        if len(hosts) >= 3:
            host_ids = [h.id for h in hosts]
            if not _track_rule_hit("multi_host_down", host_ids, min_cycles):
                continue
            names = ", ".join(h.name for h in hosts[:5])
            if len(hosts) > 5:
                names += f" (+{len(hosts) - 5} more)"
            await _find_or_create_incident(
                db,
                rule="multi_host_down",
                title=f"Network issue: {len(hosts)} hosts down in {subnet}",
                severity="critical",
                host_ids=host_ids,
                event_type="host_down",
                summary=f"{len(hosts)} hosts offline in {subnet}: {names}",
            )

    # Also trigger if 3+ hosts down across all subnets (no single subnet has 3+)
    already_covered = sum(len(h) for h in subnets.values() if len(h) >= 3)
    remaining = len(primary_hosts) - already_covered
    if remaining >= 3:
        uncovered = [h for s, hosts in subnets.items() if len(hosts) < 3 for h in hosts]
        host_ids = [h.id for h in uncovered]
        if not _track_rule_hit("multi_host_down", host_ids, min_cycles):
            return
        names = ", ".join(h.name for h in uncovered[:5])
        if len(uncovered) > 5:
            names += f" (+{len(uncovered) - 5} more)"
        await _find_or_create_incident(
            db,
            rule="multi_host_down",
            title=f"Multiple hosts down ({len(uncovered)} across subnets)",
            severity="warning",
            host_ids=host_ids,
            event_type="host_down",
            summary=f"{len(uncovered)} hosts offline across multiple subnets: {names}",
        )


# ── Rule 3: Integration + Host ──────────────────────────────────────────────

async def _rule_integration_host(db, min_failures: int = 3, min_cycles: int = 2):
    """Integration unreachable AND the host running it is also offline."""
    from services import snapshot as snap_svc

    offline_hosts = await _get_offline_hosts(db, min_failures)
    if not offline_hosts:
        return

    offline_hostnames = {h.hostname.lower().strip() for h in offline_hosts}
    offline_by_hostname: dict[str, PingHost] = {h.hostname.lower().strip(): h for h in offline_hosts}

    # Get all integration configs
    configs = (await db.execute(select(IntegrationConfig).where(IntegrationConfig.enabled == True))).scalars().all()
    all_snaps = await snap_svc.get_latest_batch_all(db)

    for cfg in configs:
        snap = all_snaps.get(cfg.type, {}).get(cfg.id)
        if not snap or snap.ok:
            continue

        # Try to extract host from config
        try:
            from services.integration import decrypt_config
            config_dict = decrypt_config(cfg.config_json)
            cfg_host = (config_dict.get("host") or "").lower().strip()
            # Strip protocol and port
            cfg_host = cfg_host.replace("https://", "").replace("http://", "").split(":")[0].split("/")[0]
        except Exception:
            continue

        if cfg_host and cfg_host in offline_hostnames:
            ping_host = offline_by_hostname[cfg_host]
            if not _track_rule_hit("integration_host", [ping_host.id], min_cycles):
                continue
            await _find_or_create_incident(
                db,
                rule="integration_host",
                title=f"{cfg.name} unreachable – host {ping_host.name} offline",
                severity="warning",
                host_ids=[ping_host.id],
                event_type="integration_error",
                summary=f"Integration '{cfg.name}' ({cfg.type}) is unreachable and its host {ping_host.name} ({cfg_host}) is also offline",
            )


# ── Rule 6: Port Error ─────────────────────────────────────────────────────

async def _rule_port_error(db, min_cycles: int = 2):
    """Host is online (ICMP OK) but a service check (HTTP/HTTPS/TCP) failed."""
    results = await db.execute(
        select(PingHost).where(
            PingHost.enabled == True,
            PingHost.maintenance == False,
            PingHost.port_error == True,
        )
    )
    hosts = results.scalars().all()
    if not hosts:
        return

    for host in hosts:
        # Parse check_detail to find which checks failed
        failed_checks = []
        if host.check_detail:
            try:
                detail = json.loads(host.check_detail)
                failed_checks = [k.upper() for k, v in detail.items() if not v]
            except Exception:
                pass

        failed_label = ", ".join(failed_checks) if failed_checks else "service check"
        if not _track_rule_hit("port_error", [host.id], min_cycles):
            continue
        await _find_or_create_incident(
            db,
            rule="port_error",
            title=f"{host.name}: {failed_label} failed",
            severity="warning",
            host_ids=[host.id],
            event_type="port_error",
            summary=f"{host.name} ({host.hostname}) is online but {failed_label} is unreachable",
        )


# ── Rule 4: Syslog Spike ────────────────────────────────────────────────────

async def _rule_syslog_spike(db, min_cycles: int = 2):
    """Syslog error rate 5x above 1h baseline."""
    now = datetime.utcnow()
    window_5m = now - timedelta(minutes=5)
    window_1h = now - timedelta(hours=1)

    # Count errors (severity <= 3) in last 5min
    recent_errors = int(await ch_scalar(
        "SELECT count() FROM syslog_messages WHERE severity <= 3 AND timestamp >= {t:DateTime64(3)}",
        {"t": window_5m},
    ) or 0)

    if recent_errors < 10:  # minimum threshold
        return

    # Count errors in last hour (baseline)
    hourly_errors = int(await ch_scalar(
        "SELECT count() FROM syslog_messages WHERE severity <= 3 AND timestamp >= {t:DateTime64(3)}",
        {"t": window_1h},
    ) or 0)

    # Expected 5min rate, computed over the prior 55min (the 11 five-minute
    # buckets EXCLUDING the recent 5min). Including the recent window would let
    # the current spike inflate its own baseline and suppress detection.
    prior_errors = max(0, hourly_errors - recent_errors)
    baseline_5m = max(1, prior_errors / 11)

    if recent_errors >= baseline_5m * 5:
        if not _track_rule_hit("syslog_spike", [0], min_cycles):
            return
        await _find_or_create_incident(
            db,
            rule="syslog_spike",
            title=f"Syslog error spike: {recent_errors} errors in 5min",
            severity="warning",
            host_ids=[0],  # no specific host
            event_type="syslog_error",
            summary=f"{recent_errors} syslog errors in last 5min (baseline: ~{int(baseline_5m)}/5min)",
        )


# ── Rule 5: Log Anomaly ────────────────────────────────────────────────

async def _rule_log_anomaly(db, min_cycles: int = 2):
    """Detect per-host log volume anomalies vs. learned baselines."""
    now = datetime.utcnow()
    hour = now.hour
    dow = now.weekday()

    # Get baselines for current time slot with sufficient data
    baselines = (await db.execute(
        select(HostBaseline).where(
            HostBaseline.hour_of_day == hour,
            HostBaseline.day_of_week == dow,
            HostBaseline.sample_count >= 3,
            HostBaseline.avg_rate > 0,
        )
    )).scalars().all()

    if not baselines:
        return

    window_10m = now - timedelta(minutes=10)

    for bl in baselines:
        # Current rate: messages in last 10min, extrapolated to per-hour
        if bl.host_key.startswith("host:"):
            parts = bl.host_key.split(":", 1)
            if len(parts) < 2 or not parts[1].isdigit():
                continue
            host_id = int(parts[1])
            count = int(await ch_scalar(
                "SELECT count() FROM syslog_messages WHERE host_id = {hid:Int32} AND timestamp >= {t:DateTime64(3)}",
                {"hid": host_id, "t": window_10m},
            ) or 0)
        else:
            count = int(await ch_scalar(
                "SELECT count() FROM syslog_messages WHERE source_ip = {ip:String} AND timestamp >= {t:DateTime64(3)}",
                {"ip": bl.host_key, "t": window_10m},
            ) or 0)

        current_rate = count * 6  # extrapolate 10min → 1hr
        threshold = bl.avg_rate + 3 * max(bl.std_rate, bl.avg_rate * 0.3)

        if current_rate > threshold and count >= 20:
            # Determine host_ids early for hit tracking
            _host_ids_for_track = [0]
            if bl.host_key.startswith("host:"):
                _p = bl.host_key.split(":", 1)
                if len(_p) > 1 and _p[1].isdigit():
                    _host_ids_for_track = [int(_p[1])]
            if not _track_rule_hit("log_anomaly", _host_ids_for_track, min_cycles):
                continue
            host_label = bl.host_key
            if bl.host_key.startswith("host:"):
                _parts = bl.host_key.split(":", 1)
                _hid = int(_parts[1]) if len(_parts) > 1 and _parts[1].isdigit() else 0
                host = (await db.execute(
                    select(PingHost).where(PingHost.id == _hid)
                )).scalar_one_or_none()
                if host:
                    host_label = host.name
                    host_ids = [host.id]
                else:
                    host_ids = [0]
            else:
                host_ids = [0]

            await _find_or_create_incident(
                db,
                rule="log_anomaly",
                title=f"Log volume anomaly: {host_label}",
                severity="warning",
                host_ids=host_ids,
                event_type="syslog_error",
                summary=f"{host_label}: {current_rate}/hr (baseline: {int(bl.avg_rate)}/hr ± {int(bl.std_rate)})",
                detail=f"{count} messages in last 10min, expected ~{int(bl.avg_rate / 6)}",
            )


# ── Rule 7: Fleet-Wide Issue ───────────────────────────────────────────────

async def _rule_fleet_wide(db, min_cycles: int = 2):
    """Detect same template appearing on 3+ hosts simultaneously."""
    from services.log_intelligence import detect_fleet_patterns
    from models.log_template import LogTemplate

    fleet_issues = await detect_fleet_patterns(db)
    if not fleet_issues:
        return

    for issue in fleet_issues:
        th = issue["template_hash"]
        host_count = issue["host_count"]

        # Get template text for the incident title
        tpl = (await db.execute(
            select(LogTemplate.template).where(LogTemplate.template_hash == th)
        )).scalar()
        tpl_text = (tpl or th)[:80]

        severity = "critical" if host_count > 5 else "warning"
        # Per-template discriminator so each template tracks its own consecutive
        # count — sharing one key would let min_cycles be reached within a single
        # cycle when multiple templates match.
        if not _track_rule_hit(f"fleet_wide_issue_{th[:8]}", [0], min_cycles):
            continue
        await _find_or_create_incident(
            db,
            rule="fleet_wide_issue",
            title=f"Fleet-wide: {tpl_text} ({host_count} hosts)",
            severity=severity,
            host_ids=[0],
            event_type="fleet_pattern",
            summary=f"Template detected on {host_count} hosts simultaneously: {tpl_text}",
            detail=json.dumps({"hosts": issue.get("hosts", [])[:10]}),
        )


# ── Rule 8: Severity Trend ────────────────────────────────────────────────

async def _rule_severity_trend(db, min_cycles: int = 2):
    """Rising error templates create warning incidents."""
    from models.log_template import LogTemplate

    rising = (await db.execute(
        select(LogTemplate).where(
            LogTemplate.trend_direction == "rising",
            LogTemplate.trend_score > 0.1,
            LogTemplate.severity_mode.isnot(None),
            LogTemplate.severity_mode <= 3,
        )
    )).scalars().all()

    for tpl in rising:
        # Per-template discriminator so each rising template tracks its own
        # consecutive count rather than sharing one key.
        if not _track_rule_hit(f"severity_trend_{tpl.template_hash[:8]}", [0], min_cycles):
            continue
        await _find_or_create_incident(
            db,
            rule="severity_trend",
            title=f"Rising error trend: {tpl.template[:60]}",
            severity="warning",
            host_ids=[0],
            event_type="syslog_trend",
            summary=f"Template is trending up (+{round(tpl.trend_score * 100)}%/hr): {tpl.template[:100]}",
        )


# ── Rule 9: Content Anomaly ──────────────────────────────────────────────

async def _rule_content_anomaly(db, min_cycles: int = 2):
    """Detect new templates on stable hosts and severity upgrades."""
    from services.log_intelligence import detect_content_anomalies

    anomalies = await detect_content_anomalies(db)
    for anomaly in anomalies:
        if anomaly["type"] == "template_diversity_spike":
            # Per-source discriminator so each source_ip tracks its own
            # consecutive count rather than collapsing into one shared key.
            if not _track_rule_hit(
                f"content_anomaly_diversity_{anomaly['source_ip']}", [0], min_cycles
            ):
                continue
            await _find_or_create_incident(
                db,
                rule="content_anomaly",
                title=f"Template diversity spike: {anomaly['source_ip']}",
                severity="warning",
                host_ids=[0],
                event_type="content_anomaly",
                summary=f"{anomaly['source_ip']}: {anomaly['current']} distinct templates "
                        f"(baseline: {anomaly['baseline']})",
            )
        elif anomaly["type"] == "severity_upgrade":
            # Per-template discriminator so each upgraded template tracks its own
            # consecutive count rather than collapsing into one shared key.
            _sev_th = anomaly.get("template_hash", "")[:8]
            if not _track_rule_hit(
                f"content_anomaly_severity_{_sev_th}", [0], min_cycles
            ):
                continue
            await _find_or_create_incident(
                db,
                rule="content_anomaly",
                title=f"Severity upgrade: {anomaly.get('template', '')[:60]}",
                severity="warning",
                host_ids=[0],
                event_type="severity_upgrade",
                summary=f"Template normally at severity {anomaly.get('normal_severity')} "
                        f"now at severity {anomaly.get('current_severity')} "
                        f"({anomaly.get('count')} occurrences)",
            )


# ── Rule 10: Learned Precursor Pattern Observed (predictive) ────────────────

async def _rule_precursor_observed(
    db,
    min_confidence: float | None = None,
    min_occurrences: int | None = None,
    min_cycles: int = 2,
):
    """Predict incidents from learned precursor patterns.

    Thresholds default to the user-configurable settings
    ``predictor_min_confidence`` / ``predictor_min_occurrences`` when the
    caller does not override them.  Templates matching
    ``predictor_template_blacklist`` never fire.
    """
    if min_confidence is None:
        min_confidence = await get_min_confidence(db)
    if min_occurrences is None:
        min_occurrences = await get_min_occurrences(db)
    blacklist = await get_blacklist_regexes(db)

    # Hashes of templates seen in the last 2 minutes with their associated host_id
    window = datetime.utcnow() - timedelta(minutes=2)
    try:
        rows = await ch_query(
            "SELECT DISTINCT template_hash, host_id "
            "FROM syslog_messages "
            "WHERE timestamp >= {since:DateTime64(3)} "
            "  AND template_hash != '' "
            "LIMIT 1000",
            {"since": window},
        )
    except Exception as exc:
        log.debug("Precursor rule: CH query failed: %s", exc)
        return

    if not rows:
        return

    # Pull all high-confidence precursors in one query so we can join in Python
    precursor_q = (
        select(PrecursorPattern, LogTemplate.template_hash, LogTemplate.template)
        .join(LogTemplate, PrecursorPattern.template_id == LogTemplate.id)
        .where(
            PrecursorPattern.confidence >= min_confidence,
            PrecursorPattern.occurrence_count >= min_occurrences,
        )
    )
    precursor_rows = (await db.execute(precursor_q)).all()
    if not precursor_rows:
        return

    # Index by template_hash for fast lookup; skip blacklisted templates here
    by_hash: dict[str, list[tuple]] = {}
    for pp, tpl_hash, tpl_text in precursor_rows:
        if is_template_blacklisted(tpl_text or "", blacklist):
            continue  # defense-in-depth – ignore stale blacklisted rows
        by_hash.setdefault(tpl_hash, []).append((pp, tpl_text))

    # Match observed templates against the index
    for row in rows:
        observed_hash = row.get("template_hash")
        if not observed_hash:
            continue
        host_id = row.get("host_id")

        for pp, tpl_text in by_hash.get(observed_hash, []):
            # De-dup: don't fire repeatedly for the same template+host
            dedup_id = host_id if host_id is not None else 0
            if not _track_rule_hit(
                f"precursor_{pp.precedes_event}_{observed_hash[:8]}",
                [dedup_id],
                min_cycles,
            ):
                continue

            confidence_pct = round(pp.confidence * 100)
            lead_min = round((pp.avg_lead_time_sec or 0) / 60, 1) if pp.avg_lead_time_sec else None
            lead_str = f"~{lead_min}min lead time" if lead_min else "unknown lead time"
            event_label = pp.precedes_event.replace("_", " ").title()
            tpl_preview = (tpl_text or "")[:80]

            # When there is no host, host_ids=[] hashes to a constant value, so
            # distinct predicted-event types would collapse into ONE incident.
            # Make the rule name distinct per precedes_event to keep them apart.
            # _auto_resolve matches rule names with startswith("learned_precursor").
            incident_rule = (
                "learned_precursor"
                if dedup_id
                else f"learned_precursor_{pp.precedes_event}"
            )
            incident = await _find_or_create_incident(
                db,
                rule=incident_rule,
                title=f"Predicted: {event_label} ({confidence_pct}% confidence)",
                severity="warning",
                host_ids=[dedup_id] if dedup_id else [],
                event_type="predicted_incident",
                summary=(
                    f"Learned precursor template fired: \"{tpl_preview}\" — "
                    f"{event_label} typically follows in {lead_str} "
                    f"({pp.occurrence_count} historical observations, "
                    f"{confidence_pct}% confidence)."
                ),
            )
            # Persist the matched template text so 'noise' feedback can map back
            # to it and blacklist the noisy pattern.
            if incident is not None and tpl_text and not incident.precursor_template:
                incident.precursor_template = tpl_text


# ── Auto-Resolve ────────────────────────────────────────────────────────────

async def _auto_resolve(db) -> list[int]:
    """Auto-resolve incidents where all affected hosts are back online.

    Returns the ids of incidents that were resolved and should get a
    post-mortem. The caller spawns the post-mortem tasks AFTER the surrounding
    transaction commits — spawning them here would let the background task read
    the incident before commit (or after a rollback) from a fresh session.
    """
    postmortem_ids: list[int] = []
    open_incidents = (await db.execute(
        select(Incident).where(Incident.status.in_(["open", "acknowledged"]))
    )).scalars().all()

    if not open_incidents:
        return postmortem_ids

    # Get current offline host IDs
    offline_hosts = await _get_offline_hosts(db)
    offline_ids = {h.id for h in offline_hosts}

    for incident in open_incidents:
        # Skip syslog/fleet/trend/content/precursor rules – auto-resolve after timeout
        if incident.rule in ("syslog_spike", "log_anomaly", "fleet_wide_issue",
                             "severity_trend", "content_anomaly") \
                or incident.rule.startswith("learned_precursor") \
                or incident.rule.startswith("alert_rule_"):
            # Resolve if last update was > 10min ago (no new activity)
            if incident.updated_at < datetime.utcnow() - timedelta(minutes=10):
                incident.status = "resolved"
                incident.resolved_at = datetime.utcnow()
                db.add(IncidentEvent(
                    incident_id=incident.id,
                    event_type="resolved",
                    summary="Auto-resolved: error rate returned to normal",
                ))
                try:
                    from notifications import notify
                    await notify(
                        f"✅ Resolved: {incident.title}",
                        "Auto-resolved: error rate returned to normal",
                        severity="info",
                    )
                except Exception as exc:
                    log.warning("Failed to send resolve notification: %s", exc)
                postmortem_ids.append(incident.id)
            continue

        if not incident.host_ids_hash:
            continue

        # Check if ALL hosts from the original hash are back online
        # We find incidents by their hash, so we need to check current offline hosts
        # against what created this incident. Since we can't reverse the hash,
        # we check: if no offline hosts match this rule anymore, resolve it.
        should_resolve = True

        if incident.rule == "host_down_syslog":
            # If any offline host still has syslog errors, keep open
            window = datetime.utcnow() - timedelta(minutes=5)
            for host in offline_hosts:
                h = _host_ids_hash([host.id])
                if h == incident.host_ids_hash:
                    syslog_count = int(await ch_scalar(
                        "SELECT count() FROM syslog_messages WHERE host_id = {hid:Int32} AND severity <= 3 AND timestamp >= {t:DateTime64(3)}",
                        {"hid": host.id, "t": window},
                    ) or 0)
                    if syslog_count > 0:
                        should_resolve = False
                        break

        elif incident.rule == "multi_host_down":
            # Can't easily reverse the hash, so check if enough hosts recovered
            # Resolve if < 3 hosts offline now
            if len(offline_hosts) >= 3:
                should_resolve = False

        elif incident.rule == "integration_host":
            # If the host is still offline, keep open
            for host in offline_hosts:
                if _host_ids_hash([host.id]) == incident.host_ids_hash:
                    should_resolve = False
                    break

        elif incident.rule == "port_error":
            # Check if the host still has port_error
            port_error_hosts = (await db.execute(
                select(PingHost).where(
                    PingHost.enabled == True,
                    PingHost.port_error == True,
                )
            )).scalars().all()
            for host in port_error_hosts:
                if _host_ids_hash([host.id]) == incident.host_ids_hash:
                    should_resolve = False
                    break

        if should_resolve:
            incident.status = "resolved"
            incident.resolved_at = datetime.utcnow()
            db.add(IncidentEvent(
                incident_id=incident.id,
                event_type="resolved",
                summary="Auto-resolved: affected hosts are back online",
            ))
            try:
                from notifications import notify
                await notify(
                    f"✅ Resolved: {incident.title}",
                    "Auto-resolved: affected hosts are back online",
                    severity="info",
                )
            except Exception as exc:
                log.warning("Failed to send resolve notification: %s", exc)
            postmortem_ids.append(incident.id)

    return postmortem_ids


# ── Main entry point ────────────────────────────────────────────────────────

async def run_correlation():
    """Run all correlation rules. Called every 60s by scheduler."""
    postmortem_ids: list[int] = []
    async with AsyncSessionLocal() as db:
        try:
            # Load consecutive-failure settings
            from models.settings import get_setting
            try:
                min_failures = max(1, int(await get_setting(db, "correlation_min_failures", "3")))
            except (ValueError, TypeError):
                min_failures = 3
            try:
                min_cycles = max(1, int(await get_setting(db, "correlation_min_cycles", "2")))
            except (ValueError, TypeError):
                min_cycles = 2

            await _rule_host_down_syslog(db, min_failures, min_cycles)
            await _rule_multi_host_down(db, min_failures, min_cycles)
            await _rule_integration_host(db, min_failures, min_cycles)
            await _rule_port_error(db, min_cycles)
            await _rule_syslog_spike(db, min_cycles)
            await _rule_log_anomaly(db, min_cycles)
            await _rule_fleet_wide(db, min_cycles)
            await _rule_severity_trend(db, min_cycles)
            await _rule_content_anomaly(db, min_cycles)
            await _rule_precursor_observed(db, min_cycles=min_cycles)
            postmortem_ids = await _auto_resolve(db)
            _prune_stale_hits()
            await db.commit()
        except Exception as e:
            log.error("Correlation engine error: %s", e, exc_info=True)
            _current_cycle_hits.clear()
            await db.rollback()
            return

    # Spawn post-mortem tasks only after the transaction has committed, so the
    # background task (fresh session) reads incidents that are durably persisted.
    if postmortem_ids:
        try:
            from services.postmortem import generate_postmortem
            for _incident_id in postmortem_ids:
                asyncio.create_task(generate_postmortem(_incident_id))
        except Exception:
            log.warning("Failed to spawn post-mortem tasks", exc_info=True)
