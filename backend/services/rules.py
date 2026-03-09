"""
Rule evaluation engine – evaluates user-defined alert rules against live data.

Supports dot-notation field paths into JSON snapshot data and multiple operators.
"""
import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.alert_rule import AlertRule
from models.integration import IntegrationConfig, Snapshot

logger = logging.getLogger(__name__)

# ── Operators ────────────────────────────────────────────────────────────────

OPERATORS = {
    "gt":           ("greater than",     lambda v, t: _num(v) > _num(t)),
    "lt":           ("less than",        lambda v, t: _num(v) < _num(t)),
    "gte":          ("greater or equal", lambda v, t: _num(v) >= _num(t)),
    "lte":          ("less or equal",    lambda v, t: _num(v) <= _num(t)),
    "eq":           ("equals",           lambda v, t: str(v).lower() == str(t).lower()),
    "ne":           ("not equals",       lambda v, t: str(v).lower() != str(t).lower()),
    "contains":     ("contains",         lambda v, t: str(t).lower() in str(v).lower()),
    "not_contains": ("not contains",     lambda v, t: str(t).lower() not in str(v).lower()),
    "is_true":      ("is true",          lambda v, _: _truthy(v)),
    "is_false":     ("is false",         lambda v, _: not _truthy(v)),
}


def _num(val) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _truthy(val) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.lower() in ("true", "1", "yes", "on")
    return bool(val)


# ── Field extraction ─────────────────────────────────────────────────────────

def extract_field(data: dict, path: str):
    """
    Extract a value from nested dict using dot-notation.
    Supports array indexing: 'raids.0.healthy', 'disks.*.temp' (first match).
    """
    parts = path.split(".")
    current = data
    for part in parts:
        if current is None:
            return None
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list):
            if part == "*":
                # Wildcard: return first non-None match from remaining path
                remaining = ".".join(parts[parts.index(part) + 1:])
                for item in current:
                    val = extract_field(item, remaining) if isinstance(item, dict) else None
                    if val is not None:
                        return val
                return None
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return current


def discover_fields(data: dict, prefix: str = "") -> list[dict]:
    """
    Recursively discover all leaf fields in a JSON data dict.
    Returns list of {path, value, type} for the UI field picker.
    """
    fields = []
    if not isinstance(data, dict):
        return fields
    for key, val in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(val, dict):
            fields.extend(discover_fields(val, path))
        elif isinstance(val, list):
            if val and isinstance(val[0], dict):
                # Show fields from first array element with wildcard
                fields.extend(discover_fields(val[0], f"{path}.*"))
            else:
                fields.append({"path": path, "value": str(val)[:100], "type": "list"})
        else:
            field_type = "number" if isinstance(val, (int, float)) else "boolean" if isinstance(val, bool) else "string"
            fields.append({"path": path, "value": val, "type": field_type})
    return fields


# ── Evaluation ───────────────────────────────────────────────────────────────

async def evaluate_rules(db: AsyncSession) -> int:
    """
    Evaluate all enabled rules. Returns number of rules that triggered.
    """
    rules = (await db.execute(
        select(AlertRule).where(AlertRule.enabled == True)
    )).scalars().all()

    if not rules:
        return 0

    triggered = 0
    now = datetime.utcnow()

    for rule in rules:
        try:
            # Cooldown check
            if rule.last_triggered_at:
                cooldown_end = rule.last_triggered_at + timedelta(minutes=rule.cooldown_minutes)
                if now < cooldown_end:
                    continue

            # Get data based on source type
            data = await _get_source_data(db, rule)
            if data is None:
                continue

            # Extract field value
            value = extract_field(data, rule.field_path)
            if value is None:
                continue

            # Apply operator
            op_fn = OPERATORS.get(rule.operator)
            if not op_fn:
                continue

            if op_fn[1](value, rule.threshold):
                triggered += 1
                await _fire_rule(db, rule, value, now)

        except Exception as exc:
            logger.warning("Rule %s (%s) evaluation failed: %s", rule.id, rule.name, exc)

    return triggered


async def _get_source_data(db: AsyncSession, rule: AlertRule) -> dict | None:
    """Fetch latest snapshot data for the rule's source."""
    if rule.source_type == "ping":
        return await _get_ping_data(db, rule.source_id)

    # Integration snapshot
    query = (
        select(Snapshot)
        .where(Snapshot.entity_type == rule.source_type)
        .order_by(Snapshot.timestamp.desc())
    )
    if rule.source_id:
        query = query.where(Snapshot.entity_id == rule.source_id)
    query = query.limit(1)

    snap = (await db.execute(query)).scalar_one_or_none()
    if not snap or not snap.data_json:
        return None

    try:
        return json.loads(snap.data_json)
    except (json.JSONDecodeError, TypeError):
        return None


async def _get_ping_data(db: AsyncSession, source_id: int | None) -> dict | None:
    """Build a dict from latest ping results for rule evaluation."""
    from database import PingHost, PingResult
    from sqlalchemy import func as sa_func

    query = select(PingHost)
    if source_id:
        query = query.where(PingHost.id == source_id)
    hosts = (await db.execute(query)).scalars().all()
    if not hosts:
        return None

    # Get latest result per host
    sub = (
        select(PingResult.host_id, sa_func.max(PingResult.timestamp).label("max_ts"))
        .group_by(PingResult.host_id)
        .subquery()
    )
    results = await db.execute(
        select(PingResult)
        .join(sub, (PingResult.host_id == sub.c.host_id) & (PingResult.timestamp == sub.c.max_ts))
    )
    result_map = {r.host_id: r for r in results.scalars().all()}

    if source_id:
        r = result_map.get(source_id)
        h = hosts[0] if hosts else None
        if not r or not h:
            return None
        return {
            "host": h.name, "hostname": h.hostname,
            "success": r.success, "latency_ms": r.latency_ms,
        }

    # Aggregate for "any ping"
    total = len(hosts)
    online = sum(1 for h in hosts if result_map.get(h.id) and result_map[h.id].success)
    return {
        "total_hosts": total, "online": online, "offline": total - online,
        "offline_pct": round((total - online) / total * 100, 1) if total else 0,
    }


async def _fire_rule(db: AsyncSession, rule: AlertRule, value, now: datetime):
    """Execute the rule's action (notification) and update last_triggered_at."""
    # Build message
    source_label = rule.source_type
    if rule.source_id:
        cfg = (await db.execute(
            select(IntegrationConfig.name).where(IntegrationConfig.id == rule.source_id)
        )).scalar_one_or_none()
        if cfg:
            source_label = f"{rule.source_type}/{cfg}"

    if rule.message_template:
        message = rule.message_template.format(
            value=value, field=rule.field_path, source=source_label,
            name=rule.name, threshold=rule.threshold,
        )
    else:
        op_label = OPERATORS.get(rule.operator, ("?",))[0]
        message = (
            f"Rule '{rule.name}' triggered: {rule.field_path} = {value} "
            f"({op_label} {rule.threshold or ''}) on {source_label}"
        )

    title = f"Rule: {rule.name}"

    # Send notification (to selected channels, or all if not specified)
    from notifications import notify
    channels = None
    if rule.notify_channels:
        channels = [c.strip() for c in rule.notify_channels.split(",") if c.strip()]
    await notify(title, message, rule.severity, channels=channels)

    # Update last_triggered_at
    await db.execute(
        update(AlertRule).where(AlertRule.id == rule.id).values(last_triggered_at=now)
    )
    await db.commit()

    logger.info("Rule triggered: %s (value=%s)", rule.name, value)


# ── CRUD helpers ─────────────────────────────────────────────────────────────

async def get_all_rules(db: AsyncSession) -> list[AlertRule]:
    return list((await db.execute(
        select(AlertRule).order_by(AlertRule.created_at.desc())
    )).scalars().all())


async def get_rule(db: AsyncSession, rule_id: int) -> AlertRule | None:
    return (await db.execute(
        select(AlertRule).where(AlertRule.id == rule_id)
    )).scalar_one_or_none()


async def delete_rule(db: AsyncSession, rule_id: int) -> bool:
    from sqlalchemy import delete as sa_delete
    result = await db.execute(
        sa_delete(AlertRule).where(AlertRule.id == rule_id)
    )
    await db.commit()
    return result.rowcount > 0


async def get_source_options(db: AsyncSession) -> list[dict]:
    """Return available source types and instances for the rule builder UI."""
    from integrations import get_registry

    sources = []

    # Integration sources
    registry = get_registry()
    configs = (await db.execute(
        select(IntegrationConfig).where(IntegrationConfig.enabled == True)
    )).scalars().all()

    type_groups: dict[str, list] = {}
    for cfg in configs:
        type_groups.setdefault(cfg.type, []).append({"id": cfg.id, "name": cfg.name})

    for int_type, instances in type_groups.items():
        cls = registry.get(int_type)
        label = cls.display_name if cls else int_type
        sources.append({
            "type": int_type,
            "label": label,
            "instances": instances,
        })

    # Ping source
    from database import PingHost
    hosts = (await db.execute(
        select(PingHost).where(PingHost.enabled == True).order_by(PingHost.name)
    )).scalars().all()
    if hosts:
        sources.append({
            "type": "ping",
            "label": "Ping",
            "instances": [{"id": h.id, "name": h.name} for h in hosts],
        })

    return sources


async def get_fields_for_source(db: AsyncSession, source_type: str, source_id: int | None = None) -> list[dict]:
    """Discover available fields from the latest snapshot of a source."""
    if source_type == "ping":
        # Return static ping fields
        return [
            {"path": "success", "value": True, "type": "boolean"},
            {"path": "latency_ms", "value": 12.5, "type": "number"},
            {"path": "host", "value": "example", "type": "string"},
        ]

    query = (
        select(Snapshot)
        .where(Snapshot.entity_type == source_type, Snapshot.ok == True)
        .order_by(Snapshot.timestamp.desc())
    )
    if source_id:
        query = query.where(Snapshot.entity_id == source_id)
    query = query.limit(1)

    snap = (await db.execute(query)).scalar_one_or_none()
    if not snap or not snap.data_json:
        return []

    try:
        data = json.loads(snap.data_json)
    except (json.JSONDecodeError, TypeError):
        return []

    return discover_fields(data)
