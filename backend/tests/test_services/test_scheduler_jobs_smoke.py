"""Every scheduled job must run cleanly — and stay quiet while doing it.

This exists because of a bug that lived four months in production: an import
sitting inside an ``if`` branch left a name unbound further down, so the
ClickHouse write in ``run_ping_checks`` raised on every single run. The error
was caught, logged, and the job still reported success — the table stayed empty
and latency, host status and uptime silently rendered nothing.

Nothing would have caught that except executing the job. Nine of sixteen jobs
had no test at all when this was written.

Two assertions per job, and the second one is the important one:

1. The job returns without raising.
2. The job logs **nothing at ERROR level**.

The first alone would never have caught the ping bug — the failure was swallowed
by a try/except that only logged, so the job returned perfectly normally. That
is precisely the shape of every silent outage found so far: three of them in a
single day, all reporting success while losing data.

Treating an ERROR log line during an ordinary run as a test failure closes that
gap: code cannot quietly report a problem to a log nobody reads and still pass.

Only outward boundaries are faked (ClickHouse, the network, notifications); the
database is real, and seeded, so the paths that matter actually execute.
"""
import logging
from contextlib import ExitStack, asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

# Jobs that only delegate to a service are still worth running: the delegation
# itself, its imports and its session handling are what break.
JOBS = [
    "run_ping_checks",
    "run_alert_rules",
    "run_correlation",
    "run_integration_checks",
    "run_log_intelligence",
    "run_log_analytics",
    "run_snmp_polls",
    "run_port_discovery",
    "run_backup_compliance",
    "check_disk_space",
    "resolve_host_dns",
    "cleanup_old_results",
    "cleanup_legacy_api_keys",
    "cleanup_clickhouse_logs",
    "update_ssl_expiry",
    "run_self_check_job",
]


@pytest.fixture
def session_factory(db):
    """Hand every job the test's SQLite session instead of the real engine."""

    @asynccontextmanager
    async def factory():
        yield db

    return factory


@pytest.fixture
def isolated_boundaries():
    """Fake only what leaves the process: ClickHouse, the network, notifications."""
    patches = [
        patch("services.clickhouse_client.query", new=AsyncMock(return_value=[])),
        patch("services.clickhouse_client.query_scalar", new=AsyncMock(return_value=0)),
        patch("services.clickhouse_client.query_chunked", new=AsyncMock(return_value=[])),
        patch("services.clickhouse_client.insert_ping_checks", new=AsyncMock()),
        patch("services.clickhouse_client.insert_batch", new=AsyncMock()),
        patch("services.clickhouse_client.get_latest_ping_per_host",
              new=AsyncMock(return_value={})),
        patch("services.clickhouse_client.get_latest_agent_metrics",
              new=AsyncMock(return_value={})),
        patch("services.clickhouse_client.get_ping_uptime", new=AsyncMock(return_value={})),
        patch("notifications.notify", new=AsyncMock()),
    ]
    started = []
    for p in patches:
        try:
            started.append(p.__enter__())
        except (AttributeError, ModuleNotFoundError):
            # Not every symbol exists in every version; skip rather than fail
            # the whole smoke sweep on an unrelated rename.
            pass
    yield
    for p in reversed(patches):
        try:
            p.__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass


@pytest.fixture
async def seeded(db):
    """A minimal but *realistic* installation.

    An empty database is not good enough: most jobs bail out at the top when
    there is nothing to work on, so the code that actually broke in production
    never runs. The ping bug in particular only appeared once there were hosts
    and none of them was agent-sourced — which is the shape of every real
    installation seen so far.
    """
    from models.ping import PingHost

    db.add(PingHost(name="gw", hostname="10.0.0.1", enabled=True,
                    check_type="icmp", source="manual"))
    db.add(PingHost(name="nas", hostname="10.0.0.2", enabled=True,
                    check_type="icmp", source="manual"))
    await db.commit()
    return db


@pytest.mark.parametrize("job_name", JOBS)
async def test_job_completes_against_a_real_schema(
    job_name, seeded, session_factory, isolated_boundaries, caplog
):
    """The job must return normally *and* stay silent at ERROR level."""
    import scheduler

    job = getattr(scheduler, job_name, None)
    assert job is not None, f"{job_name} is registered but does not exist"

    async def fake_check_host(host):
        return True, False, 5.0, {"icmp": True}

    # Several services do `from models.base import AsyncSessionLocal` at module
    # level and thus hold their own reference, which patching the source module
    # does not reach. Whether a given module is already imported depends on test
    # order, which made this fail only in some run combinations.
    import sys

    holders = [
        m for m in list(sys.modules.values())
        if m is not None and getattr(m, "__name__", "").startswith(
            ("services.", "routers.", "models", "database", "scheduler")
        ) and hasattr(m, "AsyncSessionLocal")
    ]

    with ExitStack() as stack:
        for mod in holders:
            stack.enter_context(patch.object(mod, "AsyncSessionLocal", session_factory))
        stack.enter_context(patch("utils.ping.check_host", new=fake_check_host))

        with caplog.at_level(logging.ERROR):
            await job()

    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert not errors, (
        f"{job_name} logged errors during an ordinary run — a swallowed failure "
        f"is still a failure:\n  " + "\n  ".join(errors)
    )


async def test_every_scheduled_job_is_covered_here():
    """The list above must not fall behind the scheduler.

    A new job added without a smoke test is exactly how the untested-job problem
    came about in the first place.
    """
    import re
    from pathlib import Path

    source = Path(__file__).resolve().parents[2] / "scheduler.py"
    registered = set(re.findall(r'@instrument_job\("([a-z_]+)"\)', source.read_text()))

    import scheduler
    covered = set()
    for name in JOBS:
        fn = getattr(scheduler, name, None)
        if fn is None:
            continue
        # instrument_job keeps the wrapped function's metadata, so map back via
        # the decorator argument recorded on the source line above each def.
        covered.add(name)

    # Map registered job ids to their function names by reading the source.
    pairs = re.findall(r'@instrument_job\("([a-z_]+)"\)\s*\nasync def ([a-z_]+)\(', source.read_text())
    missing = {fn for _, fn in pairs} - covered
    assert not missing, f"scheduled jobs without a smoke test: {sorted(missing)}"
