"""Self-monitoring: detect when Nodeglow itself stops collecting data.

A monitoring product failing loudly is recoverable. One that keeps rendering a
healthy-looking UI while quietly collecting nothing is not — nobody goes
looking. Two production incidents motivated this module:

- The ClickHouse ping insert raised on every single run for four months. The
  error was caught, logged, and the job still reported success; the table held
  zero rows and latency/status/uptime rendered empty.
- ``disk_space_check`` failed 2729 consecutive times. Disk monitoring was blind
  the whole time and nothing said so.

Both were visible in metrics that existed already. What was missing was
something that looks at them. This module is that watcher: it turns "a data
source went quiet" and "a job stopped succeeding" into ordinary incidents, in
the same list operators already watch.

The evaluation logic here is pure so it can be tested without a database; the
scheduler job wires it to real data.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

log = logging.getLogger("nodeglow.self_check")

# How many missed cycles before a job counts as broken. One skipped cycle is
# normal under load; three in a row is not.
JOB_GRACE_FACTOR = 3.0

SELF_CHECK_RULE = "self_check"

# An agent that has not reported within this window counts as decommissioned
# rather than broken, so its absence is not alarmed on.
AGENT_ACTIVE_DAYS = 7


@dataclass(frozen=True)
class DataSource:
    """A stream that must keep receiving data while the feature is in use."""

    key: str
    label: str
    max_age_seconds: int


@dataclass(frozen=True)
class Problem:
    """Something Nodeglow found wrong with itself."""

    key: str
    title: str
    severity: str
    summary: str


def _humanise(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def evaluate_freshness(
    source: DataSource,
    last_seen: float | None,
    now: float,
    active: bool = True,
) -> Problem | None:
    """Report a source that should be receiving data but is not.

    ``active`` guards against false alarms on features a customer does not use —
    an installation without syslog configured must never be told syslog is
    stale.
    """
    if not active:
        return None

    if last_seen is None:
        return Problem(
            key=source.key,
            title=f"{source.label}: no data recorded",
            severity="critical",
            summary=(
                f"{source.label} has no data at all, but the feature is in use. "
                f"Collection is not reaching storage."
            ),
        )

    age = now - last_seen
    if age > source.max_age_seconds:
        return Problem(
            key=source.key,
            title=f"{source.label}: data is stale",
            severity="critical",
            summary=(
                f"Last {source.label.lower()} datapoint is {_humanise(age)} old "
                f"(expected within {_humanise(source.max_age_seconds)})."
            ),
        )
    return None


def evaluate_job(
    name: str,
    last_success: float | None,
    now: float,
    interval_seconds: float,
    process_start: float,
    grace_factor: float = JOB_GRACE_FACTOR,
) -> Problem | None:
    """Report a scheduled job that has stopped completing successfully.

    A job that raises is already counted as a failure, but one that fails
    *internally* — swallowing an exception and returning normally — still looks
    successful. What cannot be faked is the timestamp of the last success, so
    that is what this checks.
    """
    deadline = interval_seconds * grace_factor

    # Right after a restart there has not been time for a verdict.
    if now - process_start < deadline:
        return None

    reference = last_success if last_success else process_start
    age = now - reference

    if age <= deadline:
        return None

    if not last_success:
        summary = (
            f"Job '{name}' has not completed successfully since startup "
            f"({_humanise(age)} ago), although it runs every "
            f"{_humanise(interval_seconds)}."
        )
    else:
        summary = (
            f"Job '{name}' last succeeded {_humanise(age)} ago, "
            f"but runs every {_humanise(interval_seconds)}."
        )

    return Problem(
        key=f"job:{name}",
        title=f"Scheduled job not completing: {name}",
        severity="critical",
        summary=summary,
    )


# ── Wiring to live data ──────────────────────────────────────────────────────

# Streams that must keep flowing while the corresponding feature is in use.
# max_age is generous: this catches "collection is broken", not "one cycle was
# slow". Each entry names the query used to decide whether the feature is in
# use at all, so unused features never alarm.
DATA_SOURCES = [
    DataSource(key="ping_checks", label="Ping checks", max_age_seconds=600),
    DataSource(key="agent_metrics", label="Agent metrics", max_age_seconds=1800),
    DataSource(key="syslog_messages", label="Syslog messages", max_age_seconds=3600),
]

# Jobs whose failure is not worth an incident on its own — they are either
# opportunistic or depend on external services the operator may not have set up.
JOB_CHECK_EXCLUDE = {
    "weekly_digest", "daily_ai_summary", "geoip_update", "legacy_api_key_cleanup",
}


def _job_intervals(scheduler) -> dict[str, float]:
    """Read interval jobs straight off the running scheduler.

    Taking the intervals from the scheduler rather than a hardcoded table means
    this cannot drift when someone retunes a job.
    """
    intervals: dict[str, float] = {}
    for job in scheduler.get_jobs():
        trigger = job.trigger
        seconds = getattr(getattr(trigger, "interval", None), "total_seconds", None)
        if seconds is None:
            continue  # cron jobs: no fixed cadence to judge against
        try:
            intervals[job.id] = seconds()
        except Exception:  # noqa: BLE001
            continue
    return intervals


async def _last_seen(key: str) -> float | None:
    """Newest datapoint for a ClickHouse-backed source, as a unix timestamp."""
    from services.clickhouse_client import query_scalar

    value = await query_scalar(f"SELECT toUnixTimestamp(max(timestamp)) FROM {key}")
    # ClickHouse returns 0 for an empty table, which must read as "never".
    return float(value) if value else None


async def _active_sources(db) -> dict[str, bool]:
    """Decide which streams are actually in use on this installation."""
    from sqlalchemy import func, select

    from database import PingHost
    from models.agent import Agent

    active: dict[str, bool] = {}

    ping_hosts = await db.scalar(
        select(func.count()).select_from(PingHost).where(PingHost.enabled == True)  # noqa: E712
    )
    active["ping_checks"] = bool(ping_hosts)

    # A registration that stopped reporting months ago is not a feature in use.
    # Counting rows alone made an installation whose agents were decommissioned
    # alarm forever — the exact false positive this guard exists to prevent.
    agent_cutoff = datetime.utcnow() - timedelta(days=AGENT_ACTIVE_DAYS)
    agents = await db.scalar(
        select(func.count()).select_from(Agent).where(Agent.last_seen >= agent_cutoff)
    )
    active["agent_metrics"] = bool(agents)

    # Syslog has no config row — infer from whether anything ever arrived.
    # A customer not forwarding syslog then never gets syslog alarms, while one
    # who was forwarding and stopped does.
    try:
        from services.clickhouse_client import query_scalar
        recent = await query_scalar(
            "SELECT count() FROM syslog_messages "
            "WHERE timestamp >= now() - INTERVAL 7 DAY"
        )
        active["syslog_messages"] = bool(recent)
    except Exception:  # noqa: BLE001
        active["syslog_messages"] = False

    return active


async def collect_problems(db, scheduler, now: float, process_start: float) -> list[Problem]:
    """Gather everything currently wrong with Nodeglow itself."""
    problems: list[Problem] = []

    # 1. Data streams that went quiet.
    try:
        active = await _active_sources(db)
    except Exception as exc:  # noqa: BLE001
        log.warning("Self-check could not determine active sources: %s", exc)
        active = {}

    for source in DATA_SOURCES:
        if not active.get(source.key):
            continue
        try:
            last = await _last_seen(source.key)
        except Exception as exc:  # noqa: BLE001
            log.warning("Self-check freshness query failed for %s: %s", source.key, exc)
            continue
        problem = evaluate_freshness(source, last, now, active=True)
        if problem:
            problems.append(problem)

    # 2. Jobs that stopped completing.
    try:
        from prometheus_client import REGISTRY

        for name, interval in _job_intervals(scheduler).items():
            if name in JOB_CHECK_EXCLUDE:
                continue
            last_success = REGISTRY.get_sample_value(
                "nodeglow_scheduler_job_last_success_timestamp", {"job": name}
            )
            problem = evaluate_job(
                name, last_success or None, now, interval, process_start
            )
            if problem:
                problems.append(problem)
    except Exception as exc:  # noqa: BLE001
        log.warning("Self-check job health pass failed: %s", exc)

    return problems


async def run_self_check(db, scheduler, now: float, process_start: float) -> list[Problem]:
    """Raise incidents for self-detected problems, resolve those that cleared."""
    from sqlalchemy import select

    from models.incident import Incident
    from services.correlation import _find_or_create_incident

    problems = await collect_problems(db, scheduler, now, process_start)
    seen_titles = {p.title for p in problems}

    for problem in problems:
        await _find_or_create_incident(
            db,
            rule=SELF_CHECK_RULE,
            title=problem.title,
            severity=problem.severity,
            host_ids=[],
            event_type="self_check",
            summary=problem.summary,
        )

    # Clear self-check incidents whose cause is gone.
    open_self = (await db.execute(
        select(Incident).where(
            Incident.rule == SELF_CHECK_RULE,
            Incident.status.in_(["open", "acknowledged"]),
        )
    )).scalars().all()

    from datetime import datetime

    for incident in open_self:
        if incident.title not in seen_titles:
            incident.status = "resolved"
            incident.resolved_at = datetime.utcnow()

    await db.commit()

    if problems:
        log.warning("Self-check found %d problem(s)", len(problems))
    return problems
