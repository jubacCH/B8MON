# Nodeglow

A self-hosted infrastructure monitoring dashboard built with **FastAPI**, **PostgreSQL** (async via asyncpg), **Jinja2** templates and **Tailwind CSS**.

---

## Features

| Feature | Details |
|---|---|
| **Host monitoring** | ICMP Ping, HTTP/HTTPS, TCP — configurable per host |
| **30-day heatmap** | Visual uptime history per host |
| **SLA tracking** | Uptime % for 24h / 7d / 30d |
| **Health score** | Composite score (0–100%) from latency, uptime, CPU, RAM, disk, syslog errors |
| **Gravity well** | Animated particle visualization — healthy hosts orbit center, unhealthy drift outward |
| **Maintenance mode** | Pauses checks, hides host from alarms |
| **SSL expiry** | Badge + alert when certificate expires in <30 days |
| **Latency thresholds** | Per-host or global alarm when latency exceeds limit |
| **15 integrations** | Generic plugin system — see table below |
| **Syslog receiver** | UDP/TCP syslog (RFC 3164/5424) with auto-host assignment and full-text search |
| **Incident correlation** | Auto-detects related failures (multi-host down, syslog + ping, integration + host) |
| **Alerts page** | Offline hosts, integration errors, UPS on battery, SSL expiry |
| **Anomaly detection** | Proxmox VM CPU/RAM spike detection (statistical + threshold) |
| **System status** | Self-monitoring page with CPU, RAM, disk, DB stats, scheduler, logs |
| **Multi-user** | Admin / Editor / Read-only roles |
| **Notifications** | Telegram, Discord, Email (SMTP) |
| **Sparklines** | 2h latency sparklines in dashboard host cards |
| **SPA navigation** | Instant page transitions without full reload |
| **Data retention** | Configurable per integration type, automatic cleanup |

---

## Integrations

All integrations use a generic plugin system (`BaseIntegration` ABC). Adding a new integration = one Python file + one HTML template.

| Integration | What is monitored |
|---|---|
| **Proxmox** | Nodes, VMs, LXC containers — CPU, RAM, disk, IO rates |
| **UniFi** | APs, switches, clients, signal strength, port PoE |
| **UniFi NAS (UNAS)** | Storage, volumes, RAID |
| **Pi-hole** | Query stats, blocking %, top domains |
| **AdGuard Home** | Query stats, blocking %, filter lists |
| **Portainer** | Docker containers across all endpoints |
| **TrueNAS** | Pools, datasets, alerts, system info |
| **Synology DSM** | Volumes, shares, CPU, RAM, SMART |
| **pfSense / OPNsense** | Interface stats, rules, DHCP leases |
| **Home Assistant** | Entity states, system info |
| **Gitea** | Repos, users, issues, system stats |
| **phpIPAM** | IP subnets, address utilisation, auto-import to Hosts |
| **Speedtest** | Download, upload, latency — scheduled via `speedtest-cli` |
| **UPS / NUT** | Battery charge, status (on-line / on-battery), runtime |
| **Redfish / iDRAC** | Server hardware temps, fans, power, system info |

---

## Quick start

### Requirements

- Docker & Docker Compose
- Linux host (for ICMP ping via `NET_RAW` capability)

### Run

```bash
git clone https://github.com/jubacCH/Nodeglow.git nodeglow
cd nodeglow
docker compose up -d
```

Open **http://localhost:8000** — the setup wizard runs on first start.

> Data is stored in PostgreSQL (managed by Docker Compose). The `./data/` volume holds the encryption key.

---

## Configuration

All settings are available at **`/settings`**:

| Setting | Default | Description |
|---|---|---|
| Site name | NODEGLOW | Shown in page title and sidebar |
| Timezone | UTC | Display timezone |
| Ping interval | 60 s | How often hosts are checked |
| Integration interval | 60 s | How often integrations are polled |
| Ping retention | 30 days | How long ping results are kept |
| Integration retention | 7 days | How long integration snapshots are kept |
| Latency threshold (global) | — | Alarm when latency exceeds this (ms) |
| CPU/RAM/Disk threshold | 85 / 85 / 90 % | Threshold for anomaly alerts |
| Anomaly multiplier | 2.0× | Alert when metric > N× 24h avg |
| Syslog port | 1514 | UDP/TCP syslog listener port |

---

## Architecture

```
nodeglow/
├── backend/
│   ├── main.py                # FastAPI app, middleware, router registration
│   ├── config.py              # Environment config, secret key
│   ├── models/                # SQLAlchemy models
│   │   ├── base.py            # Engine, session factory, encryption helpers
│   │   ├── integration.py     # IntegrationConfig + Snapshot (generic)
│   │   ├── syslog.py          # SyslogMessage
│   │   └── incidents.py       # Incident + IncidentEvent
│   ├── integrations/          # Plugin system (one file per integration)
│   │   ├── _base.py           # BaseIntegration ABC, ConfigField, CollectorResult
│   │   ├── __init__.py        # Auto-discovery + registry
│   │   ├── proxmox.py
│   │   ├── unifi.py
│   │   └── ...                # 15 integration plugins
│   ├── services/              # Business logic layer
│   │   ├── snapshot.py        # Snapshot CRUD + batch queries
│   │   ├── integration.py     # Integration CRUD + encryption
│   │   ├── syslog.py          # UDP/TCP syslog server + parser
│   │   ├── correlation.py     # Incident correlation engine
│   │   └── log_intelligence.py # Template extraction + noise scoring
│   ├── routers/               # FastAPI routers (HTML + JSON)
│   │   ├── dashboard.py
│   │   ├── ping.py
│   │   ├── integrations.py    # Generic CRUD for all integrations
│   │   ├── system.py          # Self-monitoring status page
│   │   ├── syslog.py          # Syslog viewer
│   │   ├── incidents.py       # Incident management
│   │   └── ...
│   ├── scheduler.py           # APScheduler (ping, integrations, SSL, cleanup)
│   ├── templates/             # Jinja2 templates
│   │   ├── base.html          # Layout, sidebar, SPA navigation
│   │   ├── widgets/           # Dashboard widget templates (GridStack)
│   │   └── integrations/      # Generic list/detail templates
│   └── static/                # CSS, JS, icons
├── docker-compose.yml         # App + PostgreSQL
└── data/                      # Encryption key (Docker volume)
```

### Data flow

1. **Scheduler** (APScheduler, async) runs collector functions on configurable intervals.
2. Each collector stores a **snapshot** row in PostgreSQL (`data_json` column holds full JSON).
3. **Routers** read the latest snapshot on page load — no live API calls on every request.
4. **Syslog receiver** buffers incoming messages and batch-inserts with auto-host assignment.
5. **Correlation engine** (60s interval) detects related failures and creates incidents.
6. Background **cleanup job** (daily at 03:00) prunes data older than configured retention.

---

## License

MIT
