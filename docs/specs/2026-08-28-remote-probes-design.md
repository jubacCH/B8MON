# Remote probes

Status: design · 2026-08-28

## Why

Every active check runs from the backend container: ICMP, TCP, HTTP/S and SSL
all originate in `run_ping_checks` → `check_host()`. Integrations poll remote
APIs from the same process. The agent is not a probe — it reports its own host's
metrics and logs, and for agent-sourced hosts "online" is derived from its
heartbeat (`last_seen` under 120s), not from a check it performed.

So monitoring a customer network requires the backend to reach into it: a VPN
per customer, or one full stack per site. That is the single largest obstacle to
running Nodeglow as a service for several customers, and it is the reason PRTG,
Auvik and Domotz all lead with a probe/collector model.

## What a probe is here

A probe is an **agent with the probe capability enabled**. It is not a new kind
of thing to enrol, update and authenticate.

The agent already provides everything the transport needs: an outbound-only
connection with TLS verification on by default, per-install enrolment tokens
with expiry and revocation, a permanent bearer token, signed and verified
auto-update, a command channel on the heartbeat response, and builds for Linux
(musl, static) and Windows. What it lacks is the ability to *perform* a check
rather than report on itself.

Consequence: the customer installs the same agent they already install, and one
flag decides whether it also runs checks for the site.

## Model

```
agents.is_probe        bool, default false
ping_hosts.probe_id    nullable FK -> agents.id
```

`probe_id IS NULL` means the core checks the host. That is every existing row,
so the feature is inert until someone assigns a host, and no installation
changes behaviour on upgrade.

ClickHouse `ping_checks` gains `probe_id UInt32 DEFAULT 0`, where 0 is the core.
Existing installs get it through the established `_PHASE3_ALTERS` path
(`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`), which is why the column is
nullable-by-default rather than part of a new sort key — the MergeTree ORDER BY
stays `(host_id, timestamp)` and needs no table rewrite.

## Transport

No new endpoint and no second authentication path. The existing heartbeat
carries it:

```
POST /api/agent/report
  request  += check_results: [{host_id, success, latency_ms, detail, ts}]
  response += checks: [{host_id, hostname, ip, check_type, port, timeout_seconds}]
```

The probe executes its assigned checks between heartbeats and hands the results
up on the next one. The response tells it what it is responsible for, so
assignment changes take effect within one interval with no push channel.

That delivery order has a consequence worth stating: a result is always up to
one interval older than the heartbeat that carried it. The staleness allowance
for an individual result is therefore one interval wider than the one applied to
the probe itself, or a healthy probe's hosts would read as unobserved at the
tightest cadence.

## The failure mode this must not have

A probe that stops reporting is the dangerous case, and it is not the same
problem the self-check already solves.

Today a host's status is the newest row in `ping_checks` for it, with **no age
check** (`get_latest_ping_per_host` takes `argMax(success, timestamp)`). Nothing
stops a stale row from rendering as a current state. That is survivable now only
because the one writer is the core, so if it stops writing, the global
`ping_checks` freshness source in the self-check notices and raises an incident.

Probes break that reasoning. With several writers, one probe can die while the
core and the other probes keep writing, so the **global stream stays fresh** and
the existing freshness check sees nothing wrong. The hosts behind the dead probe
keep rendering their last known state — green, if they were up. That is the
false-green failure this codebase has already been bitten by twice, and adding
probes without addressing it would reintroduce it by construction.

Three things follow, and none is optional:

1. **Freshness is evaluated per probe, not globally.** Each probe is a data
   source in its own right; the self-check raises an incident naming the probe
   when its results go stale.
2. **A host whose newest result is older than its probe's staleness threshold
   reports `unknown`, not its last value.** Absence of data must be visible as
   absence. The API and the UI distinguish "up", "down" and "not currently
   observed".
3. **The core never fills in for a silent probe.** Writing a fabricated result,
   in either direction, would be a lie about a machine nobody measured.

The threshold is the probe's report interval times the same grace factor the
job watcher uses, so one missed heartbeat is tolerated and three are not.

## Non-goals

- **Multi-tenancy.** Probes make one instance able to *see* several sites; they
  do not isolate their data. That remains a separate, larger piece of work, and
  probes reduce its urgency rather than replacing it.
- **Probe-side integrations.** Proxmox, UniFi and the rest keep polling from the
  core. Only host checks move.
- **Probe-side syslog.** Syslog is already push-based and works across networks.
- **High availability per site.** One probe per site to begin with; assigning a
  host to several probes is a later question about quorum, not a first cut.

## Stages

1. Core: model, migration, assignment API, scheduler split, per-probe freshness,
   `unknown` status. Inert until a probe exists.
2. Agent: probe mode — accept assignments, run ICMP/TCP/HTTP checks, return
   results.
3. UI: probe list, host assignment, and the `unknown` state rendered as its own
   thing rather than as a shade of down.
