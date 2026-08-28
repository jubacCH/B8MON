"""Sampling for the anomaly baseline.

The dashboard loaded every proxmox snapshot of the last 24 hours to build its
baseline: 2830 rows, 140 MB of JSON, parsed on every single page load. That was
~1.5 s of the ~2.1 s the dashboard took.

Mean and standard deviation do not need 2830 points. The newest few do have to
be exact, because the sustained-anomaly check looks at the last five values.
"""
from routers.dashboard import select_baseline_indices


def test_returns_everything_when_below_the_budget():
    assert select_baseline_indices(total=40, budget=120, keep_newest=6) == list(range(40))


def test_caps_at_the_budget():
    picked = select_baseline_indices(total=2830, budget=120, keep_newest=6)
    assert len(picked) <= 120


def test_always_keeps_the_newest_points_contiguously():
    """The sustained check reads the last N values; they must be real neighbours."""
    picked = select_baseline_indices(total=2830, budget=120, keep_newest=6)
    assert picked[-6:] == [2824, 2825, 2826, 2827, 2828, 2829]


def test_result_is_sorted_and_unique():
    picked = select_baseline_indices(total=2830, budget=120, keep_newest=6)
    assert picked == sorted(picked)
    assert len(picked) == len(set(picked))


def test_spans_the_whole_window():
    """A baseline built only from the last hour would not describe the day."""
    picked = select_baseline_indices(total=2830, budget=120, keep_newest=6)
    assert picked[0] < 100, "sample must start near the beginning of the window"


def test_keeps_enough_points_for_the_minimum_series_length():
    """The caller requires at least 6 values before it computes anything."""
    picked = select_baseline_indices(total=500, budget=120, keep_newest=6)
    assert len(picked) >= 6


def test_handles_a_window_smaller_than_keep_newest():
    assert select_baseline_indices(total=3, budget=120, keep_newest=6) == [0, 1, 2]


def test_handles_an_empty_window():
    assert select_baseline_indices(total=0, budget=120, keep_newest=6) == []
