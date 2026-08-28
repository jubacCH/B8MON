"""Deciding how often each integration should be polled.

Every integration used to share a single setting. That works for a hypervisor
poll and fails badly for anything expensive: a speedtest saturates the uplink
for 30-60 s, so a 60 s cadence meant runs overlapped permanently — readings
swung between 1.29 and 176 Mbps on the same connection, and the line was never
idle.

Resolution order, most specific first:

1. ``poll_interval_seconds`` in the integration's own config
2. ``default_interval_seconds`` on the integration class
3. the global fallback
"""
from __future__ import annotations

from datetime import datetime

DEFAULT_INTERVAL_SECONDS = 60

# Even an explicit configuration should not be allowed to hammer a target.
MIN_INTERVAL_SECONDS = 10


def effective_interval(integration_cls, config: dict | None, global_default: int) -> int:
    """Resolve the polling interval for one configured integration."""
    configured = (config or {}).get("poll_interval_seconds")
    try:
        value = int(configured)
        if value > 0:
            return max(value, MIN_INTERVAL_SECONDS)
    except (TypeError, ValueError):
        pass  # unset or unparseable — fall through to the defaults

    class_default = getattr(integration_cls, "default_interval_seconds", None)
    if isinstance(class_default, int) and class_default > 0:
        return max(class_default, MIN_INTERVAL_SECONDS)

    return max(global_default, MIN_INTERVAL_SECONDS)


def is_due(last_run: datetime | None, now: datetime, interval_seconds: int) -> bool:
    """Whether enough time has passed since the last collection."""
    if last_run is None:
        return True
    # A timestamp in the future (clock adjustment, restored backup) must not
    # stall collection until real time catches up.
    if last_run > now:
        return True
    return (now - last_run).total_seconds() >= interval_seconds
