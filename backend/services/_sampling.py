"""Choosing which rows of a time series to actually load.

Both the dashboard's anomaly baseline and the disk-full prediction walked entire
snapshot windows: 140 MB and over 1.6 GB of JSON respectively, parsed on the
request path. Statistical summaries converge long before that, but the newest
points must stay exact — callers look at the tail to decide whether something is
happening *right now*.
"""
from __future__ import annotations


def select_sample_indices(total: int, budget: int, keep_newest: int) -> list[int]:
    """Pick indices into a chronologically ordered sequence.

    The newest ``keep_newest`` are always included and stay contiguous; the rest
    of the budget is spread evenly across everything before them, so the sample
    describes the whole window instead of only its most recent slice.
    """
    if total <= 0:
        return []
    if total <= budget:
        return list(range(total))

    keep_newest = min(keep_newest, total)
    tail_start = total - keep_newest
    picked = set(range(tail_start, total))

    remaining = budget - keep_newest
    if remaining > 0 and tail_start > 0:
        step = tail_start / remaining
        picked.update(min(int(i * step), tail_start - 1) for i in range(remaining))

    return sorted(picked)
