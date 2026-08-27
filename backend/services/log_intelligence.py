"""
Log Intelligence Engine – template extraction, baseline learning, noise scoring,
auto-tagging, precursor detection, and burst detection.  Pure Python, no ML libraries.

Architecture:
- Template extraction runs on every incoming syslog message (in-memory, fast)
- Burst detection tracks per-template rate in a sliding 5-min window
- Baseline computation + precursor detection run periodically (scheduler)
- Noise scores are updated periodically based on template frequency patterns
"""
import hashlib
import logging
import re
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.log_template import HostBaseline, LogTemplate, PrecursorPattern
from services._stats import wilson_lower_bound
from services.clickhouse_client import query as ch_query
from services.predictor_config import get_blacklist_regexes, is_template_blacklisted

log = logging.getLogger("nodeglow.intelligence")

# ── Drain-lite: Template Extraction ───────────────────────────────────────────

# Patterns to replace with wildcards (order matters: more specific first)
_VARIABLE_PATTERNS = [
    # UUIDs
    (re.compile(r'\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b'), '<UUID>'),
    # Email addresses
    (re.compile(r'\b[\w.+-]+@[\w.-]+\.\w{2,}\b'), '<EMAIL>'),
    # URLs (http/https)
    (re.compile(r'https?://[^\s<>"{}|\\^`\[\]]+'), '<URL>'),
    # Docker container IDs (12 or 64 hex chars)
    (re.compile(r'\b[0-9a-f]{64}\b'), '<CONTAINER_ID>'),
    (re.compile(r'\b[0-9a-f]{12}\b'), '<SHORT_ID>'),
    # MAC addresses
    (re.compile(r'\b[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}\b'), '<MAC>'),
    # IPv6 (simplified)
    (re.compile(r'\b[0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{1,4}){7}\b'), '<IPv6>'),
    # IPv4
    (re.compile(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b'), '<IP>'),
    # ISO timestamps
    (re.compile(r'\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[\.\d]*[Z+\-\d:]*\b'), '<TS>'),
    # Date-like patterns
    (re.compile(r'\b\d{4}[-/]\d{2}[-/]\d{2}\b'), '<DATE>'),
    # Time-like patterns (HH:MM:SS)
    (re.compile(r'\b\d{2}:\d{2}:\d{2}\b'), '<TIME>'),
    # Hex strings (8+ chars)
    (re.compile(r'\b0x[0-9a-fA-F]{4,}\b'), '<HEX>'),
    (re.compile(r'\b[0-9a-fA-F]{8,}\b'), '<HEX>'),
    # File paths
    (re.compile(r'(?:/[\w.\-]+){2,}'), '<PATH>'),
    # Quoted strings (often variable content in logs)
    (re.compile(r"'[^']{2,60}'"), "'<*>'"),
    (re.compile(r'"[^"]{2,60}"'), '"<*>"'),
    # Usernames after common prefixes
    (re.compile(r'(?<=user[= ])\S+'), '<USER>'),
    (re.compile(r'(?<=for user )\S+'), '<USER>'),
    (re.compile(r'(?<=from user )\S+'), '<USER>'),
    # Numbers (3+ digits, standalone)
    (re.compile(r'\b\d{3,}\b'), '<NUM>'),
    # Port-like numbers after specific keywords
    (re.compile(r'(?<=port\s)\d+'), '<PORT>'),
    (re.compile(r'(?<=pid\s)\d+'), '<PID>'),
    (re.compile(r'(?<=pid=)\d+'), '<PID>'),
]


def extract_template(message: str) -> tuple[str, str]:
    """
    Extract a template from a log message using Drain-lite algorithm.
    Returns (template_string, template_hash).
    """
    if not message:
        return ("", hashlib.md5(b"").hexdigest()[:16])

    tpl = message
    for pattern, replacement in _VARIABLE_PATTERNS:
        tpl = pattern.sub(replacement, tpl)

    # Collapse repeated wildcards
    tpl = re.sub(r'(<\w+>)(\s*\1)+', r'\1', tpl)

    # Normalize whitespace
    tpl = ' '.join(tpl.split())

    h = hashlib.md5(tpl.encode()).hexdigest()[:16]
    return tpl, h


# ── Auto-Tagging ─────────────────────────────────────────────────────────────

_TAG_RULES = [
    # (tag, compiled_regex_pattern)
    ("security", re.compile(
        r'(?i)\b(failed\s+password|unauthorized|denied|authentication|'
        r'invalid\s+user|brute.?force|attack|intrusion|forbidden|'
        r'login\s+failed|access.?denied|permission|firewall|'
        r'blocked|malware|virus|exploit|scan|vulnerability|'
        r'injection|overflow|escalation|privilege)\b'
    )),
    ("hardware", re.compile(
        r'(?i)\b(disk|memory|temperature|temp|fan|sensor|cpu|'
        r'hardware|smart|i/?o\s+error|ecc|parity|thermal|voltage|power|'
        r'battery|ups|overclock|overheat|dimm|bios|uefi|pci|usb)\b'
    )),
    ("network", re.compile(
        r'(?i)\b(link\s+down|link\s+up|unreachable|timeout|connection\s+refused|'
        r'dns|dhcp|arp|route|interface|packet|dropped|retransmit|'
        r'network|carrier|negotiat|duplex|mtu|latency|bandwidth|'
        r'vlan|bridge|bond|lacp|spanning.?tree|bgp|ospf|vpn|wireguard|'
        r'tcp\s+reset|connection\s+closed|port\s+unreachable)\b'
    )),
    ("storage", re.compile(
        r'(?i)\b(zfs|zpool|raid|mdadm|lvm|mount|unmount|filesystem|'
        r'quota|inode|scrub|resilver|snapshot|backup|nfs|smb|iscsi|'
        r'ceph|btrfs|ext4|xfs|disk\s+full|no\s+space|trim|defrag)\b'
    )),
    ("service", re.compile(
        r'(?i)\b(started|stopped|restart|crashed|exited|failed|'
        r'systemd|service|unit|docker|container|supervisor|'
        r'enabling|disabling|loaded|activated|'
        r'oom.?kill|segfault|core\s+dump|signal|sigterm|sigkill)\b'
    )),
    ("update", re.compile(
        r'(?i)\b(upgrade|update|patch|install|dpkg|apt|yum|rpm|'
        r'package|version|firmware|release)\b'
    )),
    ("auth", re.compile(
        r'(?i)\b(login|logout|session|pam|sudo|su\b|ssh|'
        r'accepted\s+key|publickey|certificate|token|oauth|'
        r'ldap|radius|kerberos|saml|mfa|2fa|totp)\b'
    )),
    ("database", re.compile(
        r'(?i)\b(postgres|mysql|mariadb|sqlite|mongodb|redis|'
        r'query|transaction|deadlock|slow\s+query|replication|'
        r'connection\s+pool|vacuum|checkpoint|wal|tablespace)\b'
    )),
    ("web", re.compile(
        r'(?i)\b(nginx|apache|httpd|haproxy|traefik|caddy|'
        r'GET|POST|PUT|DELETE|status\s+[45]\d\d|'
        r'upstream|proxy|ssl|tls|handshake|cert\s+expir|'
        r'rate.?limit|throttl|cors|redirect)\b'
    )),
    ("cron", re.compile(
        r'(?i)\b(cron|anacron|at\b|scheduled|timer|'
        r'CMD\s*\(|CRON\[)\b'
    )),
    ("kernel", re.compile(
        r'(?i)\b(kernel|dmesg|panic|oops|bug:|call\s+trace|'
        r'out\s+of\s+memory|oom|segmentation|page\s+fault|'
        r'nmi|watchdog|hung_task|soft\s+lockup)\b'
    )),
]


# ── Structured Field Extraction ──────────────────────────────────────────────

_KV_RE = re.compile(r'(\w[\w.-]*)=((?:"[^"]*"|\S+))')
_JSON_RE = re.compile(r'\{[^{}]{5,}\}')
_CEF_RE = re.compile(
    r"CEF:\d+\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|(.*)"
)

_MAX_FIELDS = 20
_MAX_VALUE_LEN = 256


def extract_structured_fields(message: str) -> dict[str, str]:
    """Extract key=value pairs, embedded JSON, and CEF fields from a message.
    Returns a dict of field name → value (max 20 fields, 256 chars per value)."""
    fields: dict[str, str] = {}
    if not message:
        return fields

    # CEF format
    cef = _CEF_RE.match(message)
    if cef:
        fields["cef_vendor"] = cef.group(1)[:_MAX_VALUE_LEN]
        fields["cef_product"] = cef.group(2)[:_MAX_VALUE_LEN]
        fields["cef_event"] = cef.group(5)[:_MAX_VALUE_LEN]
        for m in _KV_RE.finditer(cef.group(7)):
            if len(fields) >= _MAX_FIELDS:
                break
            fields[m.group(1)] = m.group(2).strip('"')[:_MAX_VALUE_LEN]
        return fields

    # Embedded JSON
    for jm in _JSON_RE.finditer(message):
        try:
            import json as _json
            obj = _json.loads(jm.group())
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if len(fields) >= _MAX_FIELDS:
                        break
                    if isinstance(v, (str, int, float, bool)):
                        fields[str(k)] = str(v)[:_MAX_VALUE_LEN]
        except (ValueError, TypeError):
            pass

    # key=value pairs
    for m in _KV_RE.finditer(message):
        if len(fields) >= _MAX_FIELDS:
            break
        key, val = m.group(1), m.group(2).strip('"')
        if len(key) > 2 and not key.isdigit():
            fields[key] = val[:_MAX_VALUE_LEN]

    return fields


def auto_tag(message: str) -> list[str]:
    """Return auto-detected tags for a message."""
    tags = []
    for tag, pattern in _TAG_RULES:
        if pattern.search(message):
            tags.append(tag)
    return tags


# ── Noise Score Calculation ───────────────────────────────────────────────────

def compute_noise_score(
    count: int,
    hours_active: float,
    first_seen: datetime,
    severity: Optional[int] = None,
    tags: Optional[list[str]] = None,
    is_precursor: bool = False,
    avg_severity: Optional[float] = None,
) -> int:
    """
    Compute noise score 0-100 (0 = very interesting, 100 = total noise).

    Factors:
    - High frequency + consistent rate = noise
    - Low severity (info/debug) = more likely noise
    - Security/hardware/kernel tags = less likely noise
    - Recently first seen = interesting
    - Precursor templates = never noise
    - Average severity of the template matters
    """
    score = 50  # neutral start

    # Frequency factor: >100/hour sustained = very noisy
    rate = count / max(hours_active, 0.1)
    if rate > 100:
        score += 30
    elif rate > 50:
        score += 20
    elif rate > 10:
        score += 10
    elif rate < 1:
        score -= 10  # rare = interesting

    # Severity factor
    if severity is not None:
        if severity <= 2:  # emergency/alert/critical
            score -= 30
        elif severity == 3:  # error
            score -= 15
        elif severity == 4:  # warning
            score -= 5
        elif severity >= 6:  # info/debug
            score += 10

    # Tag factor — more categories now contribute
    if tags:
        if "security" in tags or "hardware" in tags or "kernel" in tags:
            score -= 15
        if "database" in tags:
            score -= 10
        if "service" in tags and "started" not in str(tags):
            score -= 5
        if "cron" in tags:
            score += 5  # cron output is usually expected noise
        # Multi-tag bonus: messages with 3+ tags are usually significant
        if len(tags) >= 3:
            score -= 10

    # Novelty factor: first seen < 24h ago
    age_hours = (datetime.utcnow() - first_seen).total_seconds() / 3600
    if age_hours < 1:
        score -= 25  # brand new = very interesting
    elif age_hours < 24:
        score -= 10

    # Precursor bonus
    if is_precursor:
        score -= 30

    return max(0, min(100, score))


# ── In-Memory Template Cache (for fast per-message extraction) ────────────────

_template_cache: dict[str, int] = {}  # hash -> template_id
_template_counts: dict[str, int] = defaultdict(int)  # hash -> count since last flush
_new_templates: dict[str, tuple[str, str, list[str]]] = {}  # hash -> (template, example, tags)
_FLUSH_INTERVAL = 30  # seconds
_last_flush: float = 0.0

# ── Burst Detection (sliding 5-min window per template) ───────────────────────
_BURST_WINDOW = 300  # 5 minutes
_BURST_THRESHOLD = 50  # messages in 5 min to count as burst
_burst_timestamps: dict[str, deque] = defaultdict(lambda: deque(maxlen=200))
_active_bursts: set[str] = set()  # hashes currently in burst state


_burst_last_cleanup: float = 0.0
_BURST_CLEANUP_INTERVAL = 600  # prune stale entries every 10 min

def _check_burst(h: str, now: float) -> bool:
    """Track template occurrence and detect bursts (>50 msgs in 5 min)."""
    global _burst_last_cleanup
    dq = _burst_timestamps[h]
    dq.append(now)
    # Evict old entries outside window
    while dq and dq[0] < now - _BURST_WINDOW:
        dq.popleft()
    count = len(dq)
    if count >= _BURST_THRESHOLD:
        if h not in _active_bursts:
            _active_bursts.add(h)
            return True  # new burst detected
    elif h in _active_bursts and count < _BURST_THRESHOLD // 2:
        _active_bursts.discard(h)  # burst ended
    # Periodic cleanup: remove hashes with no recent activity
    if now - _burst_last_cleanup > _BURST_CLEANUP_INTERVAL:
        _burst_last_cleanup = now
        cutoff = now - _BURST_WINDOW
        stale = [k for k, v in _burst_timestamps.items() if not v or v[-1] < cutoff]
        for k in stale:
            del _burst_timestamps[k]
            _active_bursts.discard(k)
    return False


def get_active_bursts() -> list[dict]:
    """Return currently active burst templates for display."""
    now = time.time()
    bursts = []
    for h in list(_active_bursts):
        dq = _burst_timestamps.get(h)
        if not dq:
            continue
        count = sum(1 for t in dq if t >= now - _BURST_WINDOW)
        if count < _BURST_THRESHOLD // 2:
            _active_bursts.discard(h)
            continue
        bursts.append({"template_hash": h, "count_5m": count, "rate_per_min": round(count / 5, 1)})
    return bursts


def process_message(message: str, severity: Optional[int] = None) -> dict:
    """
    Process a single message through the intelligence pipeline.
    Called for every incoming syslog message (must be fast).

    Returns enrichment dict: {template_hash, tags, is_new_template, noise_score, is_burst}
    """
    template, h = extract_template(message)
    tags = auto_tag(message)

    is_new = h not in _template_cache and h not in _new_templates

    _template_counts[h] += 1

    if is_new:
        _new_templates[h] = (template, message, tags)

    # Burst detection (sliding window)
    now = time.time()
    new_burst = _check_burst(h, now)
    is_burst = h in _active_bursts

    # Rough noise estimate for immediate use (refined later by periodic job)
    noise = 50
    if is_new:
        noise = 10  # new templates are interesting
    elif is_burst:
        noise = 85  # bursting templates are noise
    elif h in _template_cache:
        count = _template_counts.get(h, 0)
        if count > 100:
            noise = 80

    # Severity-aware adjustment (fast path)
    if severity is not None and severity <= 3:
        noise = max(0, noise - 20)  # errors are never pure noise

    # Structured field extraction (fast regex, ~5μs)
    extracted = extract_structured_fields(message)

    return {
        "template_hash": h,
        "tags": tags,
        "is_new_template": is_new,
        "noise_score": noise,
        "is_burst": is_burst,
        "extracted_fields": extracted,
    }


# ── Periodic DB Flush (called by scheduler or flush loop) ─────────────────────

async def flush_templates(db: AsyncSession):
    """Flush accumulated template counts and new templates to DB."""
    global _template_counts, _new_templates

    if not _template_counts and not _new_templates:
        return

    counts = dict(_template_counts)
    new_tpls = dict(_new_templates)
    _template_counts = defaultdict(int)
    _new_templates = {}

    now = datetime.utcnow()

    # Insert new templates
    for h, (template, example, tags) in new_tpls.items():
        existing = (await db.execute(
            select(LogTemplate).where(LogTemplate.template_hash == h)
        )).scalar_one_or_none()

        if not existing:
            tpl = LogTemplate(
                template_hash=h,
                template=template,
                example=example,
                count=counts.get(h, 1),
                first_seen=now,
                last_seen=now,
                tags=",".join(tags),
                noise_score=10,  # new = interesting
            )
            db.add(tpl)
            await db.flush()
            _template_cache[h] = tpl.id
            log.info("New log template: %s (tags: %s)", template[:80], ",".join(tags) or "none")
        else:
            _template_cache[h] = existing.id

    # Update counts for existing templates
    for h, count in counts.items():
        if h not in new_tpls:  # new ones already have their count
            await db.execute(
                update(LogTemplate)
                .where(LogTemplate.template_hash == h)
                .values(
                    count=LogTemplate.count + count,
                    last_seen=now,
                )
            )

    await db.commit()


async def load_template_cache(db: AsyncSession):
    """Load all template hashes into memory cache on startup."""
    global _template_cache
    rows = (await db.execute(select(LogTemplate.template_hash, LogTemplate.id))).all()
    _template_cache = {row.template_hash: row.id for row in rows}
    log.info("Loaded %d log templates into cache", len(_template_cache))


# ── Baseline Computation (periodic) ──────────────────────────────────────────

async def compute_baselines(db: AsyncSession):
    """
    Compute per-host hourly baselines from the last 7 days of syslog data.
    Uses source_ip as host_key.
    """
    now = datetime.utcnow()
    week_ago = now - timedelta(days=7)

    # Get hourly counts per source_ip from ClickHouse
    from services.clickhouse_client import query as ch_query
    ch_rows = await ch_query(
        """SELECT source_ip,
                  toDayOfWeek(timestamp) - 1 AS dow,
                  toHour(timestamp)          AS hour,
                  count()                    AS cnt
           FROM syslog_messages
           WHERE timestamp >= {t:DateTime64(3)}
           GROUP BY source_ip, dow, hour""",
        {"t": week_ago},
    )

    if not ch_rows:
        return

    # Wrap to match original field access pattern
    class _Row:
        def __init__(self, d):
            self.source_ip = d["source_ip"]
            self.dow = d["dow"]
            self.hour = d["hour"]
            self.cnt = d["cnt"]

    rows = [_Row(r) for r in ch_rows]
    if not rows:
        return

    # Group: (source_ip, dow, hour) -> [counts across weeks]
    grouped: dict[tuple, list] = defaultdict(list)
    for row in rows:
        key = (row.source_ip, int(row.dow), int(row.hour))
        grouped[key].append(row.cnt)

    # Upsert baselines
    for (host_key, dow, hour), counts in grouped.items():
        if not counts:
            continue
        avg = sum(counts) / len(counts)
        std = (sum((c - avg) ** 2 for c in counts) / len(counts)) ** 0.5 if len(counts) > 1 else 0

        existing = (await db.execute(
            select(HostBaseline).where(
                HostBaseline.host_key == host_key,
                HostBaseline.hour_of_day == hour,
                HostBaseline.day_of_week == dow,
            )
        )).scalar_one_or_none()

        if existing:
            existing.avg_rate = avg
            existing.std_rate = std
            existing.sample_count = len(counts)
            existing.updated_at = now
        else:
            db.add(HostBaseline(
                host_key=host_key,
                hour_of_day=hour,
                day_of_week=dow,
                avg_rate=avg,
                std_rate=std,
                sample_count=len(counts),
                updated_at=now,
            ))

    await db.commit()
    log.info("Baselines computed for %d host-hour combinations", len(grouped))


async def detect_baseline_anomalies(db: AsyncSession) -> list[dict]:
    """
    Check current hour's message rate against learned baselines.
    Returns list of anomaly dicts.
    """
    now = datetime.utcnow()
    hour = now.hour
    dow = now.weekday()  # 0=Mon
    window_start = now.replace(minute=0, second=0, microsecond=0)

    # Current hour counts per source_ip from ClickHouse
    from services.clickhouse_client import query as ch_query

    class _CRow:
        def __init__(self, d):
            self.source_ip = d["source_ip"]
            self.cnt = d["cnt"]

    ch_current = await ch_query(
        "SELECT source_ip, count() AS cnt FROM syslog_messages WHERE timestamp >= {t:DateTime64(3)} GROUP BY source_ip",
        {"t": window_start},
    )
    current_counts = [_CRow(r) for r in ch_current]

    if not current_counts:
        return []

    # Load baselines for this hour/dow
    baselines = (await db.execute(
        select(HostBaseline).where(
            HostBaseline.hour_of_day == hour,
            HostBaseline.day_of_week == dow,
        )
    )).scalars().all()
    baseline_map = {b.host_key: b for b in baselines}

    anomalies = []
    for row in current_counts:
        baseline = baseline_map.get(row.source_ip)
        if not baseline or baseline.sample_count < 3:
            continue

        # z-score: how many std devs above normal
        if baseline.std_rate > 0:
            z = (row.cnt - baseline.avg_rate) / baseline.std_rate
        elif row.cnt > baseline.avg_rate * 3:
            z = 5.0  # no variance but way above average
        else:
            continue

        if z >= 3.0:  # 3 sigma = significant
            anomalies.append({
                "source_ip": row.source_ip,
                "current_count": row.cnt,
                "expected": round(baseline.avg_rate, 1),
                "z_score": round(z, 1),
                "type": "rate_spike",
            })

        # Also detect silence (host normally sends logs but now silent)
    for host_key, baseline in baseline_map.items():
        if baseline.avg_rate > 10 and baseline.sample_count >= 3:
            current = next((r.cnt for r in current_counts if r.source_ip == host_key), 0)
            minutes_elapsed = max(1, now.minute + now.second / 60)
            projected_rate = current * (60.0 / minutes_elapsed)
            if projected_rate < baseline.avg_rate * 0.1:  # <10% of normal
                anomalies.append({
                    "source_ip": host_key,
                    "current_count": current,
                    "expected": round(baseline.avg_rate, 1),
                    "z_score": 0,
                    "type": "silent",
                })

    return anomalies


# ── Precursor Detection (periodic) ───────────────────────────────────────────

async def _learn_precursors_for_event(
    db: AsyncSession, event_type: str,
    events: list[tuple[int, datetime]], now: datetime,
):
    """Learn which templates appeared before a specific event type.
    Measures actual lead times instead of using hardcoded values."""
    template_before: dict[int, int] = defaultdict(int)
    template_lead_times: dict[int, list[float]] = defaultdict(list)
    total_events = 0

    for host_id, event_ts in events:
        window_start = event_ts - timedelta(minutes=5)
        msg_rows = await ch_query(
            """SELECT message, timestamp FROM syslog_messages
               WHERE host_id = {hid:Int32}
               AND timestamp >= {ts_start:DateTime64(3)}
               AND timestamp <= {ts_end:DateTime64(3)}
               AND severity <= 4
               LIMIT 50""",
            {"hid": host_id, "ts_start": window_start, "ts_end": event_ts},
        )

        if msg_rows:
            total_events += 1
            seen_templates = set()
            for row in msg_rows:
                _, h = extract_template(row["message"])
                tpl_id = _template_cache.get(h)
                if tpl_id and tpl_id not in seen_templates:
                    seen_templates.add(tpl_id)
                    template_before[tpl_id] += 1
                    # Measure actual lead time
                    msg_ts = row["timestamp"]
                    if isinstance(msg_ts, datetime):
                        delta = (event_ts - msg_ts).total_seconds()
                        if 0 < delta <= 300:
                            template_lead_times[tpl_id].append(delta)

    if not total_events:
        return

    blacklist = await get_blacklist_regexes(db)
    # Build a lookup of tpl_id → template text for the templates we touched,
    # so the blacklist check costs one query, not one per template.
    tpl_ids = list(template_before.keys())
    if tpl_ids:
        tpl_rows = (await db.execute(
            select(LogTemplate.id, LogTemplate.template).where(LogTemplate.id.in_(tpl_ids))
        )).all()
        tpl_text_by_id = {r[0]: r[1] for r in tpl_rows}
    else:
        tpl_text_by_id = {}

    for tpl_id, before_count in template_before.items():
        tpl_text = tpl_text_by_id.get(tpl_id, "")
        if is_template_blacklisted(tpl_text, blacklist):
            continue  # noise template – never let it become a precursor

        confidence = wilson_lower_bound(before_count, total_events)
        if confidence < 0.3:
            continue

        # Compute actual lead time stats
        deltas = template_lead_times.get(tpl_id, [])
        if deltas:
            avg_lead = int(sum(deltas) / len(deltas))
            min_lead = int(min(deltas))
            max_lead = int(max(deltas))
        else:
            avg_lead = 150
            min_lead = 0
            max_lead = 300

        existing = (await db.execute(
            select(PrecursorPattern).where(
                PrecursorPattern.template_id == tpl_id,
                PrecursorPattern.precedes_event == event_type,
            )
        )).scalar_one_or_none()

        if existing:
            existing.confidence = confidence
            existing.occurrence_count = before_count
            existing.total_checked = total_events
            existing.avg_lead_time_sec = avg_lead
            existing.min_lead_time_sec = min_lead
            existing.max_lead_time_sec = max_lead
            existing.updated_at = now
        else:
            db.add(PrecursorPattern(
                template_id=tpl_id,
                precedes_event=event_type,
                confidence=confidence,
                avg_lead_time_sec=avg_lead,
                min_lead_time_sec=min_lead,
                max_lead_time_sec=max_lead,
                occurrence_count=before_count,
                total_checked=total_events,
                updated_at=now,
            ))

    log.info("Precursor analysis (%s): %d templates, %d events",
             event_type, len(template_before), total_events)


async def cleanup_precursor_patterns(db: AsyncSession):
    """Drop blacklisted patterns + re-project naive confidences onto Wilson.

    Runs every intelligence cycle.  Idempotent: re-projecting an already-Wilson
    value gives the same value back, and blacklisted rows are simply absent on
    subsequent runs.
    """
    blacklist = await get_blacklist_regexes(db)

    rows = (await db.execute(
        select(PrecursorPattern, LogTemplate.template)
        .join(LogTemplate, PrecursorPattern.template_id == LogTemplate.id)
    )).all()

    removed = 0
    rescored = 0
    for pp, tpl_text in rows:
        if is_template_blacklisted(tpl_text or "", blacklist):
            await db.delete(pp)
            removed += 1
            continue
        if pp.total_checked and pp.total_checked > 0:
            new_conf = wilson_lower_bound(pp.occurrence_count, pp.total_checked)
            if abs((pp.confidence or 0.0) - new_conf) > 1e-6:
                pp.confidence = new_conf
                rescored += 1
    if removed or rescored:
        log.info("Precursor cleanup: removed=%d, rescored=%d", removed, rescored)


async def learn_precursors(db: AsyncSession):
    """
    Analyze which log templates appeared in the 5-minute window before
    host-down events, integration failures, and incidents.
    Build confidence scores over time.
    """
    await cleanup_precursor_patterns(db)

    from services.clickhouse_client import query as ch_query

    now = datetime.utcnow()
    lookback = now - timedelta(days=7)

    # 1. Host-down events from ClickHouse
    down_rows = await ch_query(
        "SELECT host_id, timestamp FROM ping_checks "
        "WHERE success = 0 AND timestamp >= {since:DateTime64(3)} "
        "ORDER BY timestamp",
        {"since": lookback},
    )
    down_events = [(int(r["host_id"]), r["timestamp"]) for r in down_rows]

    if down_events:
        await _learn_precursors_for_event(db, "host_down", down_events, now)

    # 2. Integration failures
    try:
        from models.integration import Snapshot
        fail_snaps = (await db.execute(
            select(Snapshot.entity_id, Snapshot.timestamp)
            .where(
                Snapshot.ok == False,
                Snapshot.timestamp >= lookback,
            )
            .order_by(Snapshot.timestamp)
        )).all()
        if fail_snaps:
            # entity_id is integration config ID, but we need host_id for syslog matching
            # Use entity_id as host_id=0 context (global syslog before integration failure)
            integration_events = [(0, ts) for _, ts in fail_snaps]
            await _learn_precursors_for_event(db, "integration_fail", integration_events, now)
    except Exception as exc:
        log.debug("Integration precursor learning skipped: %s", exc)

    # 3. Incidents (learn what templates precede manually-confirmed incidents)
    try:
        from models.incident import Incident
        incidents = (await db.execute(
            select(Incident)
            .where(
                Incident.created_at >= lookback,
                Incident.status.in_(["resolved", "acknowledged"]),
            )
        )).scalars().all()
        if incidents:
            incident_events = [(0, i.created_at) for i in incidents]
            await _learn_precursors_for_event(db, "incident", incident_events, now)
    except Exception as exc:
        log.debug("Incident precursor learning skipped: %s", exc)

    await db.commit()


# ── Noise Score Refresh (periodic) ───────────────────────────────────────────

# Rates below this delta are treated as unchanged, so float jitter alone never
# triggers a write.
NOISE_RATE_EPSILON = 0.01
# Rows per executemany batch when writing changed scores back.
NOISE_UPDATE_CHUNK = 1000


async def refresh_noise_scores(db: AsyncSession) -> int:
    """Recalculate noise scores for all templates. Returns the rows written.

    Reads only the columns the score depends on rather than whole ORM objects,
    and writes back just the rows whose values actually changed. On a fleet with
    hundreds of thousands of templates, materialising every row as an ORM
    instance and flushing it back made this the single most expensive query
    path in the database.
    """
    now = datetime.utcnow()

    # Batch-load all precursor template IDs to avoid N+1 queries
    precursor_ids = set(
        row[0] for row in (await db.execute(
            select(PrecursorPattern.template_id).where(
                PrecursorPattern.confidence >= 0.3,
            )
        )).all()
    )

    rows = (await db.execute(
        select(
            LogTemplate.id,
            LogTemplate.count,
            LogTemplate.first_seen,
            LogTemplate.tags,
            LogTemplate.noise_score,
            LogTemplate.avg_rate_per_hour,
        )
    )).all()

    changed: list[dict] = []
    for tpl_id, count, first_seen, tags_raw, old_score, old_rate in rows:
        if first_seen is None:
            continue
        count = count or 0
        hours_active = max(0.1, (now - first_seen).total_seconds() / 3600)

        score = compute_noise_score(
            count=count,
            hours_active=hours_active,
            first_seen=first_seen,
            tags=tags_raw.split(",") if tags_raw else [],
            is_precursor=tpl_id in precursor_ids,
        )
        rate = count / hours_active

        if score == old_score and abs(rate - (old_rate or 0.0)) < NOISE_RATE_EPSILON:
            continue

        changed.append({"id": tpl_id, "noise_score": score, "avg_rate_per_hour": rate})

    for start in range(0, len(changed), NOISE_UPDATE_CHUNK):
        await db.execute(
            update(LogTemplate),
            changed[start:start + NOISE_UPDATE_CHUNK],
            # Nothing is loaded into the session, so there is no identity map to
            # keep in sync — skipping it is the point of the bulk path.
            execution_options={"synchronize_session": None},
        )

    await db.commit()
    log.info(
        "Noise scores refreshed: %d of %d templates changed", len(changed), len(rows)
    )
    return len(changed)


# ── Severity Trend Detection (periodic) ──────────────────────────────────────

async def compute_severity_trends(db: AsyncSession):
    """Detect templates increasing in frequency or escalating severity.
    Updates trend_direction, trend_score, and severity_mode on LogTemplate."""
    from services.clickhouse_client import query_chunked as ch_query_chunked

    templates = (await db.execute(
        select(LogTemplate).where(LogTemplate.count >= 5)
    )).scalars().all()
    if not templates:
        return

    hashes = [t.template_hash for t in templates]
    # Hourly counts over last 48h per template + avg severity
    rows = await ch_query_chunked(
        """SELECT template_hash,
                  toStartOfHour(timestamp) AS h,
                  count() AS cnt,
                  avg(severity) AS avg_sev
           FROM syslog_messages
           WHERE template_hash IN ({hashes:Array(String)})
             AND timestamp >= now() - INTERVAL 48 HOUR
           GROUP BY template_hash, h
           ORDER BY template_hash, h""",
        list_param="hashes",
        values=hashes,
    )

    # Group by template_hash
    by_hash: dict[str, list] = defaultdict(list)
    sev_by_hash: dict[str, list] = defaultdict(list)
    for r in rows:
        by_hash[r["template_hash"]].append(r)
        sev_by_hash[r["template_hash"]].append(r["avg_sev"])

    tpl_map = {t.template_hash: t for t in templates}
    updated = 0

    for h, data_points in by_hash.items():
        tpl = tpl_map.get(h)
        if not tpl or len(data_points) < 4:
            continue

        # Linear regression on hourly counts
        counts = [d["cnt"] for d in data_points]
        n = len(counts)
        xs = list(range(n))
        sum_x = sum(xs)
        sum_y = sum(counts)
        sum_xy = sum(x * y for x, y in zip(xs, counts))
        sum_xx = sum(x * x for x in xs)
        denom = n * sum_xx - sum_x * sum_x

        if abs(denom) > 1e-10:
            slope = (n * sum_xy - sum_x * sum_y) / denom
        else:
            slope = 0.0

        avg_count = sum_y / n if n else 1
        # Normalize slope relative to average (% change per hour)
        rel_slope = slope / max(avg_count, 1.0)

        if rel_slope > 0.05:
            direction = "rising"
        elif rel_slope < -0.05:
            direction = "falling"
        else:
            direction = "stable"

        tpl.trend_direction = direction
        tpl.trend_score = round(rel_slope, 4)

        # Severity mode: most common severity
        sevs = sev_by_hash.get(h, [])
        if sevs:
            tpl.severity_mode = round(sum(sevs) / len(sevs))

        updated += 1

    await db.commit()
    if updated:
        log.info("Severity trends computed for %d templates", updated)


# ── Template Diversity Per Host (periodic) ───────────────────────────────────

async def compute_template_diversity(db: AsyncSession):
    """Count distinct templates per host and update baselines."""
    from services.clickhouse_client import query as ch_query

    rows = await ch_query(
        """SELECT source_ip,
                  countDistinct(template_hash) AS diversity,
                  countDistinctIf(template_hash, severity <= 3) AS error_diversity
           FROM syslog_messages
           WHERE timestamp >= now() - INTERVAL 1 HOUR
             AND template_hash != ''
           GROUP BY source_ip""",
    )

    if not rows:
        return

    now = datetime.utcnow()
    hour = now.hour
    dow = now.weekday()

    for r in rows:
        baseline = (await db.execute(
            select(HostBaseline).where(
                HostBaseline.host_key == r["source_ip"],
                HostBaseline.hour_of_day == hour,
                HostBaseline.day_of_week == dow,
            )
        )).scalar_one_or_none()

        if baseline:
            # Exponential moving average for template diversity
            alpha = 0.3
            baseline.avg_template_count = (
                alpha * r["diversity"] + (1 - alpha) * baseline.avg_template_count
            )
            # Update std using Welford's online algorithm (simplified)
            diff = r["diversity"] - baseline.avg_template_count
            baseline.std_template_count = max(
                1.0, (1 - alpha) * baseline.std_template_count + alpha * abs(diff)
            )

    await db.commit()
    log.info("Template diversity computed for %d hosts", len(rows))


# ── Cross-Host Correlation (fleet-wide issue detection) ──────────────────────

# Templates at or above this noise score are treated as benign chatter and
# never raise a fleet-wide incident, even when seen on many hosts at once.
FLEET_NOISE_MAX = 70


async def detect_fleet_patterns(db: AsyncSession) -> list[dict]:
    """Detect same template hash appearing on 3+ hosts simultaneously.

    Blacklisted templates and high-noise chatter (``noise_score >=
    FLEET_NOISE_MAX``) are skipped — vendor firmware noise that fires
    synchronously across a fleet (e.g. UniFi APs) is not an incident.
    """
    from services.clickhouse_client import query as ch_query
    from models.log_template import FleetPattern, LogTemplate

    rows = await ch_query(
        """SELECT template_hash,
                  countDistinct(source_ip) AS host_count,
                  groupArray(DISTINCT source_ip) AS hosts
           FROM syslog_messages
           WHERE timestamp >= now() - INTERVAL 10 MINUTE
             AND severity <= 4
             AND template_hash != ''
           GROUP BY template_hash
           HAVING host_count >= 3
           ORDER BY host_count DESC
           LIMIT 20""",
    )

    if not rows:
        return []

    # Batch-load template text + noise score for the candidate hashes so the
    # blacklist/noise gate costs one query, not one per template.
    candidate_hashes = [r["template_hash"] for r in rows if r.get("template_hash")]
    tpl_meta: dict[str, tuple[str, int]] = {}
    if candidate_hashes:
        meta_rows = (await db.execute(
            select(LogTemplate.template_hash, LogTemplate.template, LogTemplate.noise_score)
            .where(LogTemplate.template_hash.in_(candidate_hashes))
        )).all()
        tpl_meta = {h: (text or "", score if score is not None else 50) for h, text, score in meta_rows}

    blacklist = await get_blacklist_regexes(db)

    now = datetime.utcnow()
    fleet_issues = []

    for r in rows:
        th = r["template_hash"]
        host_count = r["host_count"]
        hosts = r["hosts"] if isinstance(r["hosts"], list) else []

        # Skip benign vendor chatter: blacklisted templates or high-noise
        # patterns never raise a fleet-wide incident. Unknown templates (no
        # LogTemplate row yet) are genuinely new and pass through.
        tpl_text, noise_score = tpl_meta.get(th, ("", 50))
        if is_template_blacklisted(tpl_text, blacklist) or noise_score >= FLEET_NOISE_MAX:
            continue

        # Check if this pattern is normally fleet-wide (baseline check)
        existing = (await db.execute(
            select(FleetPattern).where(
                FleetPattern.template_hash == th,
                FleetPattern.status == "active",
            )
        )).scalar_one_or_none()

        if existing:
            existing.host_count = host_count
            existing.source_ips = ",".join(hosts[:20])
            existing.last_checked = now
        else:
            # Check if this was fleet-wide yesterday (baseline)
            baseline_count = await ch_query(
                """SELECT countDistinct(source_ip) AS cnt
                   FROM syslog_messages
                   WHERE template_hash = {th:String}
                     AND timestamp >= now() - INTERVAL 25 HOUR
                     AND timestamp <= now() - INTERVAL 24 HOUR
                     AND severity <= 4""",
                {"th": th},
            )
            is_baseline = False
            if baseline_count and baseline_count[0]["cnt"] >= 3:
                is_baseline = True

            if not is_baseline:
                fp = FleetPattern(
                    template_hash=th,
                    host_count=host_count,
                    source_ips=",".join(hosts[:20]),
                    first_seen=now,
                    last_checked=now,
                    is_baseline=False,
                    status="active",
                )
                db.add(fp)
                fleet_issues.append({
                    "template_hash": th,
                    "host_count": host_count,
                    "hosts": hosts[:10],
                })

    # Auto-resolve old fleet patterns
    stale = (await db.execute(
        select(FleetPattern).where(
            FleetPattern.status == "active",
            FleetPattern.last_checked < now - timedelta(minutes=15),
        )
    )).scalars().all()
    for fp in stale:
        fp.status = "resolved"

    await db.commit()
    return fleet_issues


# ── Content-Based Anomalies ──────────────────────────────────────────────────

async def detect_content_anomalies(db: AsyncSession) -> list[dict]:
    """Detect new templates on stable hosts and severity upgrades."""
    from services.clickhouse_client import query_chunked as ch_query_chunked

    now = datetime.utcnow()
    hour = now.hour
    dow = now.weekday()
    anomalies = []

    # Get hosts with template diversity baselines
    baselines = (await db.execute(
        select(HostBaseline).where(
            HostBaseline.hour_of_day == hour,
            HostBaseline.day_of_week == dow,
            HostBaseline.avg_template_count > 0,
            HostBaseline.sample_count >= 3,
        )
    )).scalars().all()

    if not baselines:
        return anomalies

    stable_hosts = {
        b.host_key: b for b in baselines
        if b.avg_template_count < 10 and b.std_template_count < 3
    }

    if not stable_hosts:
        return anomalies

    # Current hour: per-host new templates (templates not seen before on this host)
    host_keys = list(stable_hosts.keys())
    rows = await ch_query_chunked(
        """SELECT source_ip, template_hash, min(severity) AS min_sev
           FROM syslog_messages
           WHERE timestamp >= now() - INTERVAL 1 HOUR
             AND source_ip IN ({ips:Array(String)})
             AND template_hash != ''
           GROUP BY source_ip, template_hash""",
        list_param="ips",
        values=host_keys,
    )

    # Count distinct templates per host
    host_templates: dict[str, list] = defaultdict(list)
    for r in rows:
        host_templates[r["source_ip"]].append(r)

    for sip, tpl_rows in host_templates.items():
        baseline = stable_hosts.get(sip)
        if not baseline:
            continue

        diversity = len(tpl_rows)
        threshold = baseline.avg_template_count + 3 * max(baseline.std_template_count, 1)

        if diversity > threshold and diversity > 5:
            anomalies.append({
                "source_ip": sip,
                "type": "template_diversity_spike",
                "current": diversity,
                "baseline": round(baseline.avg_template_count, 1),
            })

    # Severity upgrade detection: templates with severity much lower than normal
    templates_with_mode = (await db.execute(
        select(LogTemplate).where(
            LogTemplate.severity_mode.isnot(None),
            LogTemplate.severity_mode >= 5,  # normally info/debug
        )
    )).scalars().all()

    if templates_with_mode:
        sev_hashes = [t.template_hash for t in templates_with_mode]
        sev_rows = await ch_query_chunked(
            """SELECT template_hash, min(severity) AS min_sev, count() AS cnt
               FROM syslog_messages
               WHERE template_hash IN ({hashes:Array(String)})
                 AND timestamp >= now() - INTERVAL 2 HOUR
                 AND severity <= 3
               GROUP BY template_hash
               HAVING cnt >= 3""",
            list_param="hashes",
            values=sev_hashes,
        )
        tpl_map = {t.template_hash: t for t in templates_with_mode}
        for r in sev_rows:
            tpl = tpl_map.get(r["template_hash"])
            if tpl:
                anomalies.append({
                    "type": "severity_upgrade",
                    "template_hash": r["template_hash"],
                    "template": tpl.template[:100],
                    "normal_severity": tpl.severity_mode,
                    "current_severity": r["min_sev"],
                    "count": r["cnt"],
                })

    return anomalies


# ── Main periodic job (called by scheduler) ──────────────────────────────────

async def run_intelligence():
    """Fast path – flush buffered templates and refresh host baselines.

    Runs on the short scheduler tick. Both passes touch only the templates seen
    since the last run, so their cost scales with traffic, not with the size of
    the template table.
    """
    from models.base import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        try:
            await flush_templates(db)
            await compute_baselines(db)
        except Exception as e:
            log.error("Intelligence engine error: %s", e, exc_info=True)
            await db.rollback()


async def run_analytics():
    """Slow path – fleet-wide passes that scan the entire template table.

    Each of these walks every known template, so on a large fleet they cost
    orders of magnitude more than the fast path and must not share its tick.
    Noise scores, trends and diversity are statistical aggregates; a coarser
    interval does not change what they tell an operator.
    """
    from models.base import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        try:
            await learn_precursors(db)
            await refresh_noise_scores(db)
            await compute_severity_trends(db)
            await compute_template_diversity(db)
        except Exception as e:
            log.error("Analytics engine error: %s", e, exc_info=True)
            await db.rollback()


# ── Template Retention ───────────────────────────────────────────────────────

DEFAULT_TEMPLATE_RETENTION_DAYS = 90


async def cleanup_log_templates(db: AsyncSession, retention_days: int) -> int:
    """Delete templates that have not been seen within the retention window.

    Without this the table only ever grows. Measured on production after five
    months: 372'414 templates, of which 305'861 were seen exactly once and
    288'298 had not been seen for over 30 days — roughly 1 GB of rows that no
    longer describe anything the system is observing.

    Templates referenced by a learned precursor pattern are kept regardless of
    age: they carry the predictor's history and are foreign-key referenced.
    """
    if retention_days <= 0:
        return 0

    cutoff = datetime.utcnow() - timedelta(days=retention_days)

    protected = select(PrecursorPattern.template_id)
    result = await db.execute(
        delete(LogTemplate).where(
            LogTemplate.last_seen < cutoff,
            LogTemplate.id.notin_(protected),
        )
    )
    deleted = result.rowcount or 0
    if deleted:
        log.info("Pruned %d log templates older than %d days", deleted, retention_days)
    return deleted
