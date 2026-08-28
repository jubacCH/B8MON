"""Rejecting speedtest readings that cannot be real.

speedtest-cli (unmaintained since 2021) reports ping = 1800000.0 on every run
against this connection — half an hour of latency. It was stored and displayed
as a measurement.

A monitoring product showing an invented number is worse than one showing
nothing: the whole value on offer is that its readings can be trusted.
"""
from integrations.speedtest import MAX_PLAUSIBLE_PING_MS, plausible_ping


def test_a_normal_ping_passes_through():
    assert plausible_ping(18.5) == 18.5


def test_the_broken_sentinel_is_rejected():
    """The exact value speedtest-cli produces."""
    assert plausible_ping(1800000.0) is None


def test_anything_beyond_the_ceiling_is_rejected():
    assert plausible_ping(MAX_PLAUSIBLE_PING_MS + 1) is None


def test_a_slow_but_real_link_still_passes():
    """Satellite and congested mobile links genuinely reach hundreds of ms."""
    assert plausible_ping(850.0) == 850.0


def test_zero_is_rejected_as_not_measured():
    """A round zero means the tool did not measure, not that latency vanished."""
    assert plausible_ping(0) is None


def test_negative_is_rejected():
    assert plausible_ping(-1) is None


def test_missing_and_unparseable_values_are_rejected():
    for bad in (None, "", "abc", {}):
        assert plausible_ping(bad) is None


def test_ceiling_is_generous_enough_for_real_links():
    """The bound exists to catch sentinels, not to judge slow connections."""
    assert MAX_PLAUSIBLE_PING_MS >= 5000
