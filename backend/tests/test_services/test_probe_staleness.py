"""A probe going quiet must never leave its hosts looking healthy.

This is the failure this feature could most easily introduce. A host's status is
the newest row in ``ping_checks``, with no age check. That holds while the core
is the only writer, because the self-check's global freshness source notices if
it stalls. Add probes and that reasoning breaks: one probe can die while the
core and the other probes keep writing, the global stream stays fresh, and every
host behind the dead probe keeps rendering its last known state.

If those hosts were up, they stay green — with nothing checking them, and
nothing saying so. These tests exist to make that impossible.
"""
import pytest

from services.probes import (
    DEFAULT_PROBE_INTERVAL_SECONDS,
    MIN_STALENESS_WINDOW_SECONDS,
    PROBE_GRACE_FACTOR,
    STATUS_DOWN,
    STATUS_UNKNOWN,
    STATUS_UP,
    ProbeState,
    host_status,
    is_stale,
    problems_for,
    staleness_window,
)

NOW = 1_800_000_000.0


def probe(last_report=NOW, interval=60, probe_id=7, name="kunde-a"):
    return ProbeState(
        probe_id=probe_id, name=name, interval_seconds=interval, last_report=last_report
    )


# ── The core case ────────────────────────────────────────────────────────────

def test_a_host_behind_a_silent_probe_is_unknown_not_up():
    """The whole point. Its last result said up; nobody is looking now."""
    dead = probe(last_report=NOW - 3600)
    assert host_status(True, result_age_seconds=3600, probe=dead, now=NOW) == STATUS_UNKNOWN


def test_a_host_behind_a_silent_probe_is_not_reported_down_either():
    """Down would be a false alarm — the host may be perfectly fine."""
    dead = probe(last_report=NOW - 3600)
    assert host_status(True, 3600, dead, NOW) != STATUS_DOWN
    assert host_status(False, 3600, dead, NOW) == STATUS_UNKNOWN


def test_a_reporting_probe_passes_its_results_through():
    live = probe(last_report=NOW - 10)
    assert host_status(True, 10, live, NOW) == STATUS_UP
    assert host_status(False, 10, live, NOW) == STATUS_DOWN


def test_a_probe_that_never_reported_is_stale_immediately():
    """Enrolled, assigned hosts, nothing came back. That is not 'fine so far'."""
    assert is_stale(probe(last_report=None), NOW) is True


def test_a_single_missed_report_is_tolerated():
    """One slow cycle is normal; the grace factor exists for this."""
    assert is_stale(probe(last_report=NOW - 61, interval=60), NOW) is False


def test_three_missed_reports_are_not_tolerated():
    assert is_stale(probe(last_report=NOW - 400, interval=60), NOW) is True


# ── Per-host freshness, independent of the probe's own liveness ──────────────

def test_a_live_probe_that_stopped_covering_one_host_reports_unknown_for_it():
    """The probe is healthy, but this host's own result went stale.

    Assignment changed, or the probe is skipping it. Either way nobody measured
    it recently, and 'up' would be a claim no one made.
    """
    live = probe(last_report=NOW - 5, interval=60)
    assert host_status(True, result_age_seconds=9999, probe=live, now=NOW) == STATUS_UNKNOWN


def test_a_host_with_no_result_at_all_is_unknown():
    assert host_status(None, None, probe(), NOW) == STATUS_UNKNOWN


# ── Core-checked hosts keep behaving exactly as before ───────────────────────

def test_core_checked_hosts_are_unaffected():
    """probe=None is every existing host. Their behaviour must not change."""
    assert host_status(True, 99999, None, NOW) == STATUS_UP
    assert host_status(False, 99999, None, NOW) == STATUS_DOWN


def test_core_checked_host_with_no_data_is_unknown():
    assert host_status(None, None, None, NOW) == STATUS_UNKNOWN


# ── The staleness window ─────────────────────────────────────────────────────

def test_window_follows_the_probes_own_cadence():
    assert staleness_window(600) == 600 * PROBE_GRACE_FACTOR


def test_a_fast_probe_does_not_flap_on_one_slow_round_trip():
    """Without a floor, a 10s probe would go unknown after 30s."""
    assert staleness_window(10) == MIN_STALENESS_WINDOW_SECONDS


def test_a_probe_without_a_configured_interval_gets_the_default():
    assert staleness_window(None) == max(
        MIN_STALENESS_WINDOW_SECONDS, DEFAULT_PROBE_INTERVAL_SECONDS * PROBE_GRACE_FACTOR
    )


# ── Incidents ────────────────────────────────────────────────────────────────

def test_a_silent_probe_with_hosts_raises_an_incident():
    problems = problems_for([probe(last_report=NOW - 3600)], {7: 12}, NOW)
    assert len(problems) == 1
    assert problems[0]["severity"] == "critical"
    assert "12 host" in problems[0]["summary"]


def test_the_incident_says_the_hosts_are_unknown_not_healthy():
    """An operator reading this must not conclude the site is fine."""
    problems = problems_for([probe(last_report=NOW - 3600)], {7: 3}, NOW)
    assert "unknown, not healthy" in problems[0]["summary"]


def test_an_enrolled_probe_with_no_hosts_is_not_alarmed_on():
    """Same false-positive lesson the self-check already learned."""
    assert problems_for([probe(last_report=NOW - 99999)], {7: 0}, NOW) == []


def test_a_reporting_probe_raises_nothing():
    assert problems_for([probe(last_report=NOW - 5)], {7: 9}, NOW) == []


def test_a_probe_that_never_reported_but_has_hosts_is_reported():
    problems = problems_for([probe(last_report=None)], {7: 4}, NOW)
    assert len(problems) == 1
    assert "never reported" in problems[0]["summary"]


def test_each_silent_probe_gets_its_own_incident():
    """Two customers' sites going dark are two separate facts."""
    probes = [
        probe(last_report=NOW - 3600, probe_id=1, name="kunde-a"),
        probe(last_report=NOW - 3600, probe_id=2, name="kunde-b"),
    ]
    problems = problems_for(probes, {1: 5, 2: 8}, NOW)
    assert {p["key"] for p in problems} == {"probe:1", "probe:2"}


# ── Work split between the core and its probes ───────────────────────────────

class _Host:
    def __init__(self, host_id, probe_id=None):
        self.id = host_id
        self.probe_id = probe_id


def test_the_core_skips_hosts_a_probe_is_responsible_for():
    """Checking them from here too would often fail — that is why a probe exists
    — and the wrong result would overwrite the probe's correct one."""
    from services.probes import core_checked

    hosts = [_Host(1), _Host(2, probe_id=7), _Host(3)]
    assert [h.id for h in core_checked(hosts)] == [1, 3]


def test_hosts_without_the_attribute_are_still_core_checked():
    """Rows loaded before the column existed must not silently drop out."""
    from services.probes import core_checked

    class Legacy:
        id = 9

    assert len(core_checked([Legacy()])) == 1


# ── The one place that answers "is this host up" ─────────────────────────────

def test_the_topology_expression_that_was_wrong_is_gone():
    """It read attributes PingHost does not have.

        online = h.status == "up" if hasattr(h, "status") \
                 else not getattr(h, "is_down", True)

    Neither exists on the model, so hasattr was always False and the fallback
    always evaluated to False. GET /api/v1/topology reported every node down,
    on every installation, regardless of the data. Verified against live rows
    before fixing.
    """
    import inspect

    from routers import api_v1

    src = inspect.getsource(api_v1)
    assert 'getattr(h, "is_down"' not in src
    assert "statuses_for" in src
