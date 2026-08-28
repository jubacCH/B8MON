"""Every scheduled job must be visible to the watcher that guards it.

The self-check looked up a job's metrics by its scheduler id. Four jobs are
registered under an id that differs from the label they record under:

    id="disk_space"   records as  "disk_space_check"
    id="dns_resolve"  records as  "dns_resolution"
    id="cleanup"      records as  "cleanup_old_results"
    id="ch_cleanup"   records as  "clickhouse_log_cleanup"

For those, the lookup found nothing under all three statuses, the watcher read
that as "this job was never instrumented", and skipped it — permanently, not
just until the first run. The disk check was one of them: the job whose 2729
unnoticed consecutive failures are the reason this module was written.

The existing suite could not catch it. It builds ``FakeJob("disk_space_check")``
— the metric name — and so tests a scheduler that does not exist. These tests
read the real one.
"""
import pytest

from services.metrics import metric_name_of
from services.self_check import (
    JOB_CHECK_EXCLUDE,
    JOB_ID_PREFIXES,
    metric_name_of_job,
)


@pytest.fixture
async def registered_jobs(db, monkeypatch):
    """Register the real job set without starting the scheduler."""
    import scheduler as sched

    async def fake_get_setting(_db, key, default=None):
        return default

    monkeypatch.setattr("database.get_setting", fake_get_setting, raising=False)
    monkeypatch.setattr(sched.scheduler, "start", lambda *a, **kw: None)
    monkeypatch.setattr(sched, "seed_default_oids", None, raising=False)

    try:
        await sched.start_scheduler()
    except Exception:
        # Startup does more than register jobs (SNMP seeding, monitoring-source
        # discovery). Those may fail against the test database; the jobs that
        # were registered before that point are still what we assert on.
        pass

    jobs = list(sched.scheduler.get_jobs())
    yield jobs
    sched.scheduler.remove_all_jobs()


def _interval_jobs(jobs):
    out = []
    for job in jobs:
        interval = getattr(getattr(job.trigger, "interval", None), "total_seconds", None)
        if interval is None:
            continue  # cron jobs carry no cadence to judge against
        if metric_name_of_job(job) in JOB_CHECK_EXCLUDE:
            continue
        out.append(job)
    return out


async def test_every_interval_job_resolves_to_the_label_it_records_under(registered_jobs):
    """The decisive one: no job may resolve to a name its metrics never use."""
    mismatched = []
    for job in _interval_jobs(registered_jobs):
        recorded = metric_name_of(job.func)
        if recorded is None:
            continue  # genuinely uninstrumented; covered by the test below
        if metric_name_of_job(job) != recorded:
            mismatched.append((job.id, metric_name_of_job(job), recorded))

    assert mismatched == [], (
        "These jobs would be invisible to the self-check — it looks up the "
        "second name while the job records under the third: " + repr(mismatched)
    )


async def test_the_jobs_that_were_blind_are_watched(registered_jobs):
    """Named explicitly, because these are the ones that were actually skipped."""
    by_id = {job.id: job for job in registered_jobs}
    for job_id, expected in (
        ("disk_space", "disk_space_check"),
        ("dns_resolve", "dns_resolution"),
    ):
        assert job_id in by_id, f"{job_id} is no longer registered — update this test"
        assert metric_name_of_job(by_id[job_id]) == expected


async def test_every_watched_job_resolves_by_attribute_or_by_convention(registered_jobs):
    """A job the watcher judges but that records nothing can never be judged.

    MonitoringSource jobs are the one legitimate exception to carrying the
    attribute: ``run_source`` applies ``instrument_job`` at call time so the
    label follows the source's own name, and the scheduler registers a plain
    closure. For those the documented ``src:`` prefix rule is the correct
    resolution, so they are excluded here rather than made to carry it.
    """
    unresolvable = [
        job.id for job in _interval_jobs(registered_jobs)
        if metric_name_of(job.func) is None
        and not job.id.startswith(JOB_ID_PREFIXES)
    ]
    assert unresolvable == [], (
        "These jobs are watched but carry no @instrument_job and follow no "
        "naming convention, so the watcher skips them silently: "
        + repr(unresolvable)
    )
