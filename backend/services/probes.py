"""Remote probes: assignment, and noticing when one goes quiet.

A probe is an agent that also runs checks for hosts in its network, so a site
can be monitored without the core reaching into it.

The part that needs care is not distributing the work — it is what happens when
a probe stops reporting.

A host's status is the newest row in ``ping_checks`` for it, with no age check.
That is survivable while the core is the only writer: if it stops writing, the
global freshness source in the self-check notices and raises an incident. Probes
break that reasoning. With several writers, one probe can die while the core and
the other probes keep writing, so the global stream stays fresh and the existing
check sees nothing wrong — while every host behind the dead probe keeps
rendering its last known state. If they were up, they stay green forever.

That is the false-green failure this codebase has already been bitten by, so the
rules here are deliberate:

- freshness is judged per probe, never globally;
- a host nobody is currently observing reports ``unknown``, not its last value;
- the core never invents a result for a silent probe, in either direction.

The evaluation logic is pure so it can be tested without a database or a probe.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger("nodeglow.probes")

# How long a probe may be silent before what it watched counts as unobserved.
# Same shape as the job watcher: one missed report is normal, three are not.
PROBE_GRACE_FACTOR = 3.0

# Fallback cadence for a probe that has not been given one.
DEFAULT_PROBE_INTERVAL_SECONDS = 60

# Floor for the staleness window, so a very fast probe does not flap on a single
# slow network round-trip.
MIN_STALENESS_WINDOW_SECONDS = 120

PROBE_RULE = "probe_health"

# Host status values. UNKNOWN is not a degree of DOWN — it says nobody is
# currently looking, which is a different fact and must not resolve to either.
STATUS_UP = "up"
STATUS_DOWN = "down"
STATUS_UNKNOWN = "unknown"


@dataclass(frozen=True)
class ProbeState:
    """What a probe reported, and when."""

    probe_id: int
    name: str
    interval_seconds: int | None
    last_report: float | None


def staleness_window(interval_seconds: int | None) -> float:
    """How old a probe's data may be before it stops counting as current."""
    interval = interval_seconds or DEFAULT_PROBE_INTERVAL_SECONDS
    return max(MIN_STALENESS_WINDOW_SECONDS, interval * PROBE_GRACE_FACTOR)


def is_stale(probe: ProbeState, now: float) -> bool:
    """Has this probe been quiet long enough that its hosts are unobserved?

    A probe that has never reported is stale. It was enrolled and assigned
    hosts, and nothing has come back — treating that as "fine so far" is how a
    site ends up monitored on paper only.
    """
    if probe.last_report is None:
        return True
    return (now - probe.last_report) > staleness_window(probe.interval_seconds)


def host_status(
    success: bool | None,
    result_age_seconds: float | None,
    probe: ProbeState | None,
    now: float,
) -> str:
    """The status to report for a host, given its newest result.

    ``probe`` is None for a host the core checks, where the existing global
    freshness check already covers a stall and the last result stands.
    """
    if probe is None:
        if success is None:
            return STATUS_UNKNOWN
        return STATUS_UP if success else STATUS_DOWN

    if success is None:
        # Assigned to a probe that has never returned anything for it.
        return STATUS_UNKNOWN

    if is_stale(probe, now):
        return STATUS_UNKNOWN

    # The probe is reporting, but this particular host's result is old — it was
    # assigned recently, or the probe is skipping it. Either way it is not being
    # observed, and saying "up" would be a claim nobody made.
    if result_age_seconds is not None and result_age_seconds > staleness_window(
        probe.interval_seconds
    ):
        return STATUS_UNKNOWN

    return STATUS_UP if success else STATUS_DOWN


def problems_for(probes: list[ProbeState], host_counts: dict[int, int], now: float) -> list[dict]:
    """Incidents to raise for probes that have gone quiet.

    A probe with no hosts assigned is not alarmed on: it is enrolled but unused,
    and alarming on it is the same false positive the self-check already learned
    to avoid for features nobody configured.
    """
    problems = []
    for probe in probes:
        watched = host_counts.get(probe.probe_id, 0)
        if not watched:
            continue
        if not is_stale(probe, now):
            continue

        if probe.last_report is None:
            summary = (
                f"Probe '{probe.name}' has {watched} host(s) assigned but has "
                f"never reported. Those hosts are not being checked by anything."
            )
        else:
            age = int(now - probe.last_report)
            summary = (
                f"Probe '{probe.name}' last reported {age}s ago and watches "
                f"{watched} host(s). Their status is unknown, not healthy — "
                f"nothing is checking them while the probe is silent."
            )

        problems.append({
            "key": f"probe:{probe.probe_id}",
            "title": f"Probe not reporting: {probe.name}",
            "severity": "critical",
            "summary": summary,
        })
    return problems


def core_checked(hosts: list) -> list:
    """The hosts this instance checks itself.

    A host assigned to a probe is checked from that probe's network. The core
    does not also check it: it would often fail from here (that is the whole
    reason for a probe) and would then overwrite the probe's correct result with
    a wrong one.
    """
    return [h for h in hosts if getattr(h, "probe_id", None) is None]
