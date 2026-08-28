"""The shared time-series sampler.

Two request-path hotspots walked entire snapshot windows: the dashboard's
anomaly baseline (2830 rows / 140 MB) and the disk-full prediction (18'443 rows
/ 909 MB per integration, ~9.6 s). Both summarise a trend, and a trend does not
need every sample — but the newest points must stay exact, because both callers
look at the tail to judge what is happening now.
"""
from services._sampling import select_sample_indices


def test_small_window_is_returned_whole():
    assert select_sample_indices(50, 200, 10) == list(range(50))


def test_large_window_respects_the_budget():
    picked = select_sample_indices(18443, 200, 10)
    assert len(picked) <= 200


def test_newest_points_are_kept_exactly_and_contiguously():
    picked = select_sample_indices(18443, 200, 10)
    assert picked[-10:] == list(range(18433, 18443))


def test_sample_reaches_the_start_of_the_window():
    """A trend line built only from the last hour would not describe a week."""
    picked = select_sample_indices(18443, 200, 10)
    assert picked[0] < 200


def test_indices_are_sorted_and_unique():
    picked = select_sample_indices(18443, 200, 10)
    assert picked == sorted(picked)
    assert len(set(picked)) == len(picked)


def test_spacing_is_roughly_even():
    """Uneven clustering would bias the regression toward one part of the week."""
    picked = select_sample_indices(18443, 200, 10)
    body = picked[:-10]
    gaps = [b - a for a, b in zip(body, body[1:])]
    assert max(gaps) - min(gaps) <= 2


def test_empty_and_tiny_windows():
    assert select_sample_indices(0, 200, 10) == []
    assert select_sample_indices(1, 200, 10) == [0]
    assert select_sample_indices(5, 200, 10) == [0, 1, 2, 3, 4]
