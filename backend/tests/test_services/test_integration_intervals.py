"""Per-integration polling intervals.

Every integration shared one setting (proxmox_interval, default 60 s). That is
right for a Proxmox poll and wrong for a speedtest, which saturates the line for
30-60 s: runs overlapped continuously, producing readings between 1.29 and
176 Mbps on the same connection within five minutes, and keeping the uplink busy
around the clock.
"""
from datetime import datetime, timedelta

from services.integration_schedule import (
    DEFAULT_INTERVAL_SECONDS,
    effective_interval,
    is_due,
)

NOW = datetime(2026, 8, 28, 12, 0, 0)


class FastIntegration:
    default_interval_seconds = 60


class SlowIntegration:
    default_interval_seconds = 3600


class UnspecifiedIntegration:
    pass


def test_falls_back_to_the_global_default():
    assert effective_interval(UnspecifiedIntegration, {}, 60) == 60


def test_class_default_wins_over_the_global_one():
    """A speedtest must not inherit the poll cadence meant for a hypervisor."""
    assert effective_interval(SlowIntegration, {}, 60) == 3600


def test_explicit_config_wins_over_the_class_default():
    assert effective_interval(SlowIntegration, {"poll_interval_seconds": 900}, 60) == 900


def test_config_value_may_be_a_string_from_a_form():
    assert effective_interval(FastIntegration, {"poll_interval_seconds": "300"}, 60) == 300


def test_nonsense_config_is_ignored_rather_than_crashing():
    for bad in ("", "abc", None, -5, 0):
        assert effective_interval(SlowIntegration, {"poll_interval_seconds": bad}, 60) == 3600


def test_never_returns_something_absurdly_small():
    """A one-second poll would hammer the target regardless of who configured it."""
    assert effective_interval(FastIntegration, {"poll_interval_seconds": 1}, 60) >= 10


def test_first_run_is_always_due():
    assert is_due(None, NOW, 3600) is True


def test_not_due_before_the_interval_elapses():
    assert is_due(NOW - timedelta(seconds=59), NOW, 3600) is False


def test_due_once_the_interval_has_elapsed():
    assert is_due(NOW - timedelta(seconds=3601), NOW, 3600) is True


def test_a_future_timestamp_does_not_block_forever():
    """A clock change or restored backup must not freeze collection."""
    assert is_due(NOW + timedelta(hours=2), NOW, 3600) is True


def test_default_matches_the_documented_fallback():
    assert DEFAULT_INTERVAL_SECONDS == 60
