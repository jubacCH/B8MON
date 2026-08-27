"""Tests for self-monitoring — Nodeglow noticing that it stopped collecting.

A monitoring product that silently stops monitoring is the worst failure mode
there is: the UI still renders, so nobody looks. Two real cases motivated this:
the ClickHouse ping insert failed on every run for four months while the job
reported success, and disk_space_check failed 2729 times unnoticed.
"""
from services.self_check import (
    DataSource,
    evaluate_freshness,
    evaluate_job,
)

NOW = 1_000_000.0


# ── Data freshness ───────────────────────────────────────────────────────────

PING = DataSource(key="ping_checks", label="Ping checks", max_age_seconds=300)


def test_fresh_data_is_no_problem():
    assert evaluate_freshness(PING, last_seen=NOW - 60, now=NOW) is None


def test_stale_data_is_reported():
    problem = evaluate_freshness(PING, last_seen=NOW - 3600, now=NOW)

    assert problem is not None
    assert problem.key == "ping_checks"
    assert "Ping checks" in problem.title
    assert "1h" in problem.summary or "3600" in problem.summary


def test_data_that_never_arrived_is_reported():
    """The exact production case: the table was empty for four months."""
    problem = evaluate_freshness(PING, last_seen=None, now=NOW)

    assert problem is not None
    assert "no data" in problem.summary.lower()


def test_inactive_source_is_skipped():
    """A customer without syslog configured must not get syslog alarms."""
    assert evaluate_freshness(PING, last_seen=None, now=NOW, active=False) is None


def test_exactly_at_threshold_is_still_ok():
    assert evaluate_freshness(PING, last_seen=NOW - 300, now=NOW) is None


# ── Job health ───────────────────────────────────────────────────────────────

def test_recently_successful_job_is_no_problem():
    problem = evaluate_job(
        "ping_checks", last_success=NOW - 30, now=NOW,
        interval_seconds=60, process_start=NOW - 10_000,
    )
    assert problem is None


def test_job_that_stopped_succeeding_is_reported():
    """disk_space_check: ran every 30 min, never once succeeded."""
    problem = evaluate_job(
        "disk_space_check", last_success=None, now=NOW,
        interval_seconds=1800, process_start=NOW - 10_000,
    )

    assert problem is not None
    assert problem.key == "job:disk_space_check"
    assert "disk_space_check" in problem.title


def test_job_gets_grace_period_after_process_start():
    """A freshly started process must not alarm before the job could even run."""
    problem = evaluate_job(
        "ssl_expiry", last_success=None, now=NOW,
        interval_seconds=21600, process_start=NOW - 60,
    )
    assert problem is None


def test_job_late_beyond_grace_factor_is_reported():
    problem = evaluate_job(
        "correlation", last_success=NOW - 500, now=NOW,
        interval_seconds=60, process_start=NOW - 10_000,
    )
    assert problem is not None


def test_job_slightly_late_is_tolerated():
    """One skipped cycle is normal under load and must not page anyone."""
    problem = evaluate_job(
        "correlation", last_success=NOW - 90, now=NOW,
        interval_seconds=60, process_start=NOW - 10_000,
    )
    assert problem is None


# ── Wiring ───────────────────────────────────────────────────────────────────

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest


class FakeTrigger:
    def __init__(self, seconds):
        self.interval = timedelta(seconds=seconds)


class FakeJob:
    def __init__(self, job_id, seconds):
        self.id = job_id
        self.trigger = FakeTrigger(seconds)


class FakeCronTrigger:
    """A cron job has no fixed interval to judge against."""


class FakeCronJob:
    def __init__(self, job_id):
        self.id = job_id
        self.trigger = FakeCronTrigger()


class FakeScheduler:
    def __init__(self, jobs):
        self._jobs = jobs

    def get_jobs(self):
        return self._jobs


def test_job_intervals_reads_interval_jobs_and_skips_cron():
    from services.self_check import _job_intervals

    scheduler = FakeScheduler([
        FakeJob("ping_checks", 60),
        FakeJob("correlation", 60),
        FakeCronJob("cleanup"),
    ])

    intervals = _job_intervals(scheduler)

    assert intervals == {"ping_checks": 60.0, "correlation": 60.0}
    assert "cleanup" not in intervals


async def test_collect_problems_flags_empty_ping_table(db):
    """End-to-end over the wiring: the exact production failure."""
    from services.self_check import collect_problems

    scheduler = FakeScheduler([FakeJob("ping_checks", 60)])

    with patch("services.self_check._active_sources", new=AsyncMock(
                return_value={"ping_checks": True})), \
         patch("services.self_check._last_seen", new=AsyncMock(return_value=None)), \
         patch("prometheus_client.REGISTRY.get_sample_value", return_value=NOW - 30):
        problems = await collect_problems(
            db, scheduler, now=NOW, process_start=NOW - 10_000
        )

    keys = {p.key for p in problems}
    assert "ping_checks" in keys


async def test_collect_problems_quiet_when_healthy(db):
    """No incidents on a healthy install — this must not cry wolf."""
    from services.self_check import collect_problems

    scheduler = FakeScheduler([FakeJob("ping_checks", 60)])

    with patch("services.self_check._active_sources", new=AsyncMock(
                return_value={"ping_checks": True})), \
         patch("services.self_check._last_seen", new=AsyncMock(return_value=NOW - 30)), \
         patch("prometheus_client.REGISTRY.get_sample_value", return_value=NOW - 30):
        problems = await collect_problems(
            db, scheduler, now=NOW, process_start=NOW - 10_000
        )

    assert problems == []


async def test_unused_features_never_alarm(db):
    """An install without ping hosts or agents must stay silent."""
    from services.self_check import collect_problems

    with patch("services.self_check._active_sources", new=AsyncMock(return_value={})), \
         patch("services.self_check._last_seen", new=AsyncMock(return_value=None)):
        problems = await collect_problems(
            db, FakeScheduler([]), now=NOW, process_start=NOW - 10_000
        )

    assert problems == []


async def test_run_self_check_opens_and_later_resolves_incident(db):
    """A detected problem raises an incident; once it clears, it resolves.

    Without the resolve half, a one-off blip would leave an incident open
    forever and operators would learn to ignore the list.
    """
    from sqlalchemy import select

    from models.incident import Incident
    from services.self_check import SELF_CHECK_RULE, Problem, run_self_check

    scheduler = FakeScheduler([])
    broken = [Problem(key="ping_checks", title="Ping checks: no data recorded",
                      severity="critical", summary="nothing arriving")]

    with patch("services.self_check.collect_problems", new=AsyncMock(return_value=broken)), \
         patch("notifications.notify", new=AsyncMock()):
        await run_self_check(db, scheduler, NOW, NOW - 10_000)

    opened = (await db.execute(
        select(Incident).where(Incident.rule == SELF_CHECK_RULE)
    )).scalars().all()
    assert len(opened) == 1
    assert opened[0].status == "open"

    # Next pass: the problem is gone.
    with patch("services.self_check.collect_problems", new=AsyncMock(return_value=[])), \
         patch("notifications.notify", new=AsyncMock()):
        await run_self_check(db, scheduler, NOW + 600, NOW - 10_000)

    after = (await db.execute(
        select(Incident).where(Incident.rule == SELF_CHECK_RULE)
    )).scalars().all()
    assert len(after) == 1, "must not create a second incident"
    assert after[0].status == "resolved"
    assert after[0].resolved_at is not None


# ── Regressions from the first production day ────────────────────────────────

async def test_stale_agent_registrations_do_not_count_as_active(db):
    """Agents that stopped reporting months ago are decommissioned, not broken.

    Production had 14 registered agents last seen in April. Counting rows alone
    made the check alarm on missing agent metrics forever — a false positive
    that trains operators to ignore the incident list.
    """
    from datetime import datetime, timedelta

    from models.agent import Agent
    from services.self_check import _active_sources

    db.add(Agent(name="old", hostname="old.example", token="t1",
                 last_seen=datetime.utcnow() - timedelta(days=120)))
    await db.commit()

    active = await _active_sources(db)

    assert active["agent_metrics"] is False


async def test_recently_seen_agent_counts_as_active(db):
    """An agent reporting normally must still be watched."""
    from datetime import datetime, timedelta

    from models.agent import Agent
    from services.self_check import _active_sources

    db.add(Agent(name="live", hostname="live.example", token="t2",
                 last_seen=datetime.utcnow() - timedelta(hours=1)))
    await db.commit()

    active = await _active_sources(db)

    assert active["agent_metrics"] is True


async def test_correlation_leaves_self_check_incidents_alone(db):
    """Only run_self_check may resolve its own incidents.

    Self-check incidents carry no hosts, so the correlation engine's
    "are the hosts back online?" logic resolved them immediately. The result was
    an incident recreated every 5 minutes and resolved 60 seconds later, which
    is what operators actually saw: a permanent stream of down/up notifications.
    """
    from sqlalchemy import select

    from models.incident import Incident
    from services.correlation import _auto_resolve
    from services.self_check import SELF_CHECK_RULE

    incident = Incident(
        rule=SELF_CHECK_RULE,
        title="Ping checks: no data recorded",
        severity="critical",
        status="open",
        host_ids_hash="e3b0c44298fc1c14",  # sha256 of the empty host list
    )
    db.add(incident)
    await db.commit()

    await _auto_resolve(db)
    await db.commit()

    still_open = (await db.execute(
        select(Incident).where(Incident.rule == SELF_CHECK_RULE)
    )).scalar_one()
    assert still_open.status == "open", "correlation must not resolve self-check incidents"
