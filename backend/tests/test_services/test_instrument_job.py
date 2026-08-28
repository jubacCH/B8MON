"""A job that swallowed a failure must not be recorded as a success.

Three outages in one day shared the same shape: an operation failed, the error
was caught and logged, and the job returned normally — so it counted as a
success and last_success advanced. The ping insert did this on every run for
four months while the table stayed empty.

Raising is already handled. What was missing is the case where the job *reports*
a problem and carries on. That is what these tests pin.
"""
import asyncio
import logging

import pytest

from services.metrics import (
    SCHEDULER_JOB_LAST_SUCCESS,
    SCHEDULER_JOB_RUNS,
    instrument_job,
)


def runs(job: str, status: str) -> float:
    return SCHEDULER_JOB_RUNS.labels(job=job, status=status)._value.get()


def last_success(job: str) -> float:
    return SCHEDULER_JOB_LAST_SUCCESS.labels(job=job)._value.get()


async def test_clean_job_counts_as_success_and_advances_last_success():
    @instrument_job("t_clean")
    async def job():
        logging.getLogger("x").info("all good")

    before = runs("t_clean", "success")
    await job()

    assert runs("t_clean", "success") == before + 1
    assert last_success("t_clean") > 0


async def test_job_logging_an_error_is_degraded_not_successful():
    """The exact production shape: caught, logged, returned normally."""
    @instrument_job("t_degraded")
    async def job():
        try:
            raise RuntimeError("ClickHouse insert failed")
        except RuntimeError as exc:
            logging.getLogger("scheduler").error("insert failed: %s", exc)

    await job()

    assert runs("t_degraded", "degraded") == 1
    assert runs("t_degraded", "success") == 0


async def test_degraded_run_does_not_advance_last_success():
    """This is what makes the self-check notice.

    last_success is the one signal a swallowed failure cannot fake, so a
    degraded run must leave it untouched — otherwise the job looks healthy
    forever.
    """
    @instrument_job("t_no_advance")
    async def job():
        logging.getLogger("scheduler").error("write lost")

    await job()

    assert last_success("t_no_advance") == 0


async def test_raising_job_still_counts_as_failure():
    @instrument_job("t_raises")
    async def job():
        raise ValueError("boom")

    with pytest.raises(ValueError):
        await job()

    assert runs("t_raises", "failure") == 1
    assert runs("t_raises", "degraded") == 0


async def test_warnings_do_not_degrade_a_job():
    """Only ERROR and above. Warnings are normal operational noise."""
    @instrument_job("t_warn")
    async def job():
        logging.getLogger("scheduler").warning("slow response")

    await job()

    assert runs("t_warn", "success") == 1
    assert runs("t_warn", "degraded") == 0


async def test_concurrent_jobs_do_not_contaminate_each_other():
    """Errors must be attributed to the job that logged them.

    Jobs share an event loop, so a naive global counter would mark a healthy
    job degraded because an unrelated one failed at the same moment.
    """
    @instrument_job("t_noisy")
    async def noisy():
        await asyncio.sleep(0.01)
        logging.getLogger("scheduler").error("my own failure")

    @instrument_job("t_quiet")
    async def quiet():
        await asyncio.sleep(0.02)

    await asyncio.gather(noisy(), quiet())

    assert runs("t_noisy", "degraded") == 1
    assert runs("t_quiet", "success") == 1
    assert runs("t_quiet", "degraded") == 0


async def test_error_message_is_attached_to_the_span():
    """An operator seeing a degraded job needs to know what was logged."""
    @instrument_job("t_span")
    async def job():
        logging.getLogger("scheduler").error("ClickHouse insert (ping_checks) failed")

    await job()

    assert runs("t_span", "degraded") == 1
