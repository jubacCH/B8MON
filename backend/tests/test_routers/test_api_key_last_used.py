"""Throttling of the api_keys.last_used write.

Every authenticated API request updated last_used and committed. On production
that made a two-row table the single most expensive statement in the database:

    380727 ms total | 17059 calls | 22 ms mean
    UPDATE api_keys SET last_used=$1 WHERE ...

Those 22 ms sit in the request path, so every page paid for them, and each write
left a dead tuple behind. last_used is an operational hint — "was this key used
recently" — not an audit trail, so a coarse resolution is enough.
"""
from datetime import datetime, timedelta

from routers.api_v1 import API_KEY_LAST_USED_RESOLUTION, should_record_use

NOW = datetime(2026, 8, 27, 12, 0, 0)


def test_first_use_is_always_recorded():
    assert should_record_use(None, NOW) is True


def test_second_use_immediately_after_is_not():
    assert should_record_use(NOW - timedelta(seconds=1), NOW) is False


def test_use_after_the_resolution_window_is_recorded():
    stale = NOW - API_KEY_LAST_USED_RESOLUTION - timedelta(seconds=1)
    assert should_record_use(stale, NOW) is True


def test_exactly_at_the_boundary_is_recorded():
    assert should_record_use(NOW - API_KEY_LAST_USED_RESOLUTION, NOW) is True


def test_a_clock_moving_backwards_does_not_wedge_it():
    """A timestamp in the future must not stop recording forever."""
    assert should_record_use(NOW + timedelta(hours=1), NOW) is True


def test_resolution_is_coarse_enough_to_matter():
    """Guards the intent: a resolution of seconds would not fix anything."""
    assert API_KEY_LAST_USED_RESOLUTION >= timedelta(minutes=1)
