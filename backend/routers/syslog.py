"""Syslog log viewer – filterable, searchable, sortable, paginated, live tail."""
import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from templating import templates, localtime
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import PingHost, get_db
from models.syslog import FACILITY_LABELS, RETENTION_DAYS, SEVERITY_LABELS, SyslogView
from services.clickhouse_client import query as ch_query, query_scalar as ch_scalar, _where_clauses

router = APIRouter(prefix="/syslog")

_PER_PAGE = 100

_SORT_COLS = {
    "time": "timestamp",
    "severity": "severity",
    "host": "hostname",
    "app": "app_name",
    "source": "source_ip",
}


# ── Row dataclass (drop-in for ORM objects in templates) ─────────────────────

@dataclass
class SyslogRow:
    timestamp: datetime
    received_at: datetime
    source_ip: str
    hostname: str
    host_id: Optional[int]
    facility: Optional[int]
    severity: int
    app_name: str
    message: str
    template_hash: str
    tags: str
    noise_score: int
    _dedup_count: int = field(default=1, repr=False)
    _dedup_last: Optional[datetime] = field(default=None, repr=False)
    _fields: dict = field(default_factory=dict, repr=False)
    _noise_score: int = field(default=50, repr=False)
    _tags: list = field(default_factory=list, repr=False)
    _template_hash: str = field(default="", repr=False)


def _row(d: dict) -> SyslogRow:
    return SyslogRow(
        timestamp=d.get("timestamp") or datetime.utcnow(),
        received_at=d.get("received_at") or datetime.utcnow(),
        source_ip=d.get("source_ip") or "",
        hostname=d.get("hostname") or "",
        host_id=d.get("host_id"),
        facility=d.get("facility"),
        severity=d.get("severity") if d.get("severity") is not None else 6,
        app_name=d.get("app_name") or "",
        message=d.get("message") or "",
        template_hash=d.get("template_hash") or "",
        tags=d.get("tags") or "",
        noise_score=d.get("noise_score") if d.get("noise_score") is not None else 50,
    )


# ── Helpers ──────────────────────────────────────────────────────────────────

def _extract_fields(message: str) -> dict:
    """Extract structured fields from CEF or key=value messages."""
    fields = {}
    cef = re.match(
        r"CEF:\d+\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|(.*)",
        message,
    )
    if cef:
        fields["vendor"] = cef.group(1)
        fields["product"] = cef.group(2)
        fields["event"] = cef.group(5)
        for m in re.finditer(r"(\w[\w.-]*)=((?:\"[^\"]*\"|\S+))", cef.group(7)):
            fields[m.group(1)] = m.group(2).strip('"')
    else:
        for m in re.finditer(r"(\w[\w.-]*)=((?:\"[^\"]*\"|\S+))", message):
            key, val = m.group(1), m.group(2).strip('"')
            if len(key) > 2 and not key.isdigit():
                fields[key] = val
    return fields


def _dedup_messages(messages: list[SyslogRow]) -> list[SyslogRow]:
    """Group consecutive identical messages (same source_ip + message + severity)."""
    if not messages:
        return messages
    result: list[SyslogRow] = []
    for msg in messages:
        if (
            result
            and result[-1].source_ip == msg.source_ip
            and result[-1].message == msg.message
            and result[-1].severity == msg.severity
        ):
            result[-1]._dedup_count += 1
            result[-1]._dedup_last = msg.timestamp
        else:
            msg._dedup_count = 1
            msg._dedup_last = None
            result.append(msg)
    return result


def _build_ip_map(ping_hosts) -> dict[str, int]:
    ip_to_host_id: dict[str, int] = {}
    for ph in ping_hosts:
        raw = ph.hostname
        for prefix in ("https://", "http://"):
            if raw.startswith(prefix):
                raw = raw[len(prefix):]
        raw = raw.split("/")[0].split(":")[0]
        ip_to_host_id[raw] = ph.id
        if ph.name:
            ip_to_host_id[ph.name.lower()] = ph.id
    return ip_to_host_id


# ── Main page ────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def syslog_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    severity: str = Query(""),
    facility: str = Query(""),
    host: str = Query(""),
    app: str = Query(""),
    q: str = Query(""),
    hours: int = Query(24),
    page: int = Query(1, ge=1),
    sort: str = Query("time"),
    order: str = Query("desc"),
):
    sev = int(severity) if severity not in ("", None) else None
    fac = int(facility) if facility not in ("", None) else None
    since = datetime.utcnow() - timedelta(hours=hours)

    where, params = _where_clauses(since, sev=sev, fac=fac, host=host, app=app, q=q)

    total = await ch_scalar(f"SELECT count() FROM syslog_messages WHERE {where}", params) or 0
    total = int(total)
    total_pages = max(1, (total + _PER_PAGE - 1) // _PER_PAGE)
    page = min(page, total_pages)

    sort_col = _SORT_COLS.get(sort, "timestamp")
    sort_dir = "ASC" if order == "asc" else "DESC"
    offset = (page - 1) * _PER_PAGE

    rows = await ch_query(
        f"""SELECT timestamp, received_at, source_ip, hostname, host_id,
                   facility, severity, app_name, message,
                   template_hash, tags, noise_score
            FROM syslog_messages
            WHERE {where}
            ORDER BY {sort_col} {sort_dir}
            LIMIT {_PER_PAGE} OFFSET {offset}""",
        params,
    )
    messages = _dedup_messages([_row(r) for r in rows])

    # Intelligence enrichment (template noise scores from PostgreSQL)
    try:
        from services.log_intelligence import extract_template, auto_tag
        from models.log_template import LogTemplate
        _tpl_hashes = set()
        for msg in messages:
            _, h = extract_template(msg.message)
            msg._template_hash = h
            _tpl_hashes.add(h)
        _noise_map: dict = {}
        _tags_map: dict = {}
        if _tpl_hashes:
            _tpl_rows = (await db.execute(
                select(LogTemplate.template_hash, LogTemplate.noise_score, LogTemplate.tags)
                .where(LogTemplate.template_hash.in_(_tpl_hashes))
            )).all()
            for r in _tpl_rows:
                _noise_map[r.template_hash] = r.noise_score
                _tags_map[r.template_hash] = r.tags
    except Exception:
        _noise_map = {}
        _tags_map = {}

    for msg in messages:
        msg._fields = _extract_fields(msg.message)
        h = msg._template_hash
        msg._noise_score = _noise_map.get(h, 50) if h else 50
        db_tags = _tags_map.get(h, "") if h else ""
        try:
            from services.log_intelligence import auto_tag
            msg._tags = [t.strip() for t in db_tags.split(",") if t.strip()] if db_tags else auto_tag(msg.message)
        except Exception:
            msg._tags = []

    # Severity counts for header pills
    sev_rows = await ch_query(
        f"SELECT severity, count() AS cnt FROM syslog_messages WHERE {where} GROUP BY severity",
        params,
    )
    severity_counts = {r["severity"]: r["cnt"] for r in sev_rows}

    # Known hosts dropdown
    host_rows = await ch_query(
        f"""SELECT source_ip, hostname FROM syslog_messages
            WHERE {where} GROUP BY source_ip, hostname
            ORDER BY source_ip LIMIT 200""",
        params,
    )
    known_hosts = [(r["source_ip"], r["hostname"]) for r in host_rows]

    # Known apps dropdown
    app_rows = await ch_query(
        f"""SELECT app_name FROM syslog_messages
            WHERE {where} AND app_name != ''
            GROUP BY app_name ORDER BY app_name LIMIT 100""",
        params,
    )
    known_apps = [r["app_name"] for r in app_rows]

    # IP → host_id map
    ping_hosts = (await db.execute(select(PingHost))).scalars().all()
    ip_to_host_id = _build_ip_map(ping_hosts)

    bucket_minutes = max(5, (hours * 60) // 60)
    rate_data = await _build_rate_chart(since, bucket_minutes)

    saved_views = (await db.execute(select(SyslogView).order_by(SyslogView.name))).scalars().all()
    alert_spike = await _check_severity_spike()

    intelligence = {"anomalies": [], "new_templates": [], "precursors": [], "bursts": []}
    try:
        from models.log_template import LogTemplate, PrecursorPattern
        from services.log_intelligence import detect_baseline_anomalies, get_active_bursts

        intelligence["anomalies"] = await detect_baseline_anomalies(db)
        intelligence["bursts"] = get_active_bursts()

        new_tpls = (await db.execute(
            select(LogTemplate)
            .where(LogTemplate.first_seen >= since, LogTemplate.noise_score < 30)
            .order_by(LogTemplate.first_seen.desc())
            .limit(5)
        )).scalars().all()
        intelligence["new_templates"] = [
            {"template": t.template[:120], "count": t.count, "tags": t.tags,
             "first_seen": localtime(t.first_seen, "%H:%M") if t.first_seen else ""}
            for t in new_tpls
        ]

        precs = (await db.execute(
            select(PrecursorPattern, LogTemplate)
            .join(LogTemplate, PrecursorPattern.template_id == LogTemplate.id)
            .where(PrecursorPattern.confidence >= 0.5)
            .order_by(PrecursorPattern.confidence.desc())
            .limit(5)
        )).all()
        intelligence["precursors"] = [
            {"template": tpl.template[:100], "event": p.precedes_event,
             "confidence": round(p.confidence * 100), "lead_time": p.avg_lead_time_sec}
            for p, tpl in precs
        ]
    except Exception:
        pass

    return templates.TemplateResponse("syslog.html", {
        "request": request,
        "active_page": "syslog",
        "messages": messages,
        "total": total,
        "page": page,
        "total_pages": total_pages,
        "severity_labels": SEVERITY_LABELS,
        "facility_labels": FACILITY_LABELS,
        "severity_counts": severity_counts,
        "known_hosts": known_hosts,
        "known_apps": known_apps,
        "ip_to_host_id": ip_to_host_id,
        "rate_chart": json.dumps(rate_data),
        "saved_views": saved_views,
        "retention_days": RETENTION_DAYS,
        "alert_spike": alert_spike,
        "intelligence": intelligence,
        "f_severity": sev,
        "f_facility": fac,
        "f_host": host or "",
        "f_app": app or "",
        "f_q": q or "",
        "f_hours": hours,
        "f_sort": sort,
        "f_order": order,
    })


# ── Log-rate chart ────────────────────────────────────────────────────────────

async def _build_rate_chart(since: datetime, bucket_min: int) -> dict:
    try:
        rows = await ch_query(
            """SELECT
                   toStartOfInterval(timestamp, INTERVAL {bm:UInt32} MINUTE) AS bucket,
                   severity AS sev,
                   count() AS cnt
               FROM syslog_messages
               WHERE timestamp >= {since:DateTime64(3)}
               GROUP BY bucket, sev
               ORDER BY bucket""",
            {"since": since, "bm": bucket_min},
        )
    except Exception:
        return {"labels": [], "err": [], "warn": [], "info": [], "debug": []}

    buckets: dict = {}
    for r in rows:
        ts_str = localtime(r["bucket"], "%H:%M") if r["bucket"] else "?"
        if ts_str not in buckets:
            buckets[ts_str] = {}
        buckets[ts_str][int(r["sev"])] = buckets[ts_str].get(int(r["sev"]), 0) + r["cnt"]

    labels = list(buckets.keys())
    err   = [sum(buckets[l].get(s, 0) for s in range(4)) for l in labels]
    warn  = [buckets[l].get(4, 0) for l in labels]
    info  = [sum(buckets[l].get(s, 0) for s in (5, 6)) for l in labels]
    debug = [buckets[l].get(7, 0) for l in labels]
    return {"labels": labels, "err": err, "warn": warn, "info": info, "debug": debug}


# ── Severity spike detection ──────────────────────────────────────────────────

async def _check_severity_spike() -> dict | None:
    try:
        now = datetime.utcnow()
        recent = int(await ch_scalar(
            "SELECT count() FROM syslog_messages WHERE severity <= 3 AND timestamp >= {t:DateTime64(3)}",
            {"t": now - timedelta(minutes=5)},
        ) or 0)
        hour_total = int(await ch_scalar(
            "SELECT count() FROM syslog_messages WHERE severity <= 3 AND timestamp >= {t:DateTime64(3)}",
            {"t": now - timedelta(hours=1)},
        ) or 0)
        avg_per_5min = hour_total / 12
        if avg_per_5min > 0 and recent >= 5 and recent > avg_per_5min * 5:
            return {"recent": recent, "avg": round(avg_per_5min, 1), "ratio": round(recent / avg_per_5min, 1)}
    except Exception:
        pass
    return None


# ── SSE Live tail ─────────────────────────────────────────────────────────────

@router.get("/stream")
async def syslog_stream(
    severity: str = Query(""),
    host: str = Query(""),
    app: str = Query(""),
):
    from services.syslog import subscribe, unsubscribe

    sev_filter = int(severity) if severity not in ("", None) else None

    async def event_generator():
        q = subscribe()
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=30)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue

                if sev_filter is not None and msg.get("severity") != sev_filter:
                    continue
                if host and host.lower() not in (msg.get("source_ip", "").lower() + msg.get("hostname", "").lower()):
                    continue
                if app and app.lower() not in (msg.get("app_name") or "").lower():
                    continue

                data = {
                    "timestamp": localtime(msg["timestamp"], "%m-%d %H:%M:%S") if isinstance(msg["timestamp"], datetime) else str(msg["timestamp"]),
                    "severity": msg.get("severity"),
                    "severity_label": SEVERITY_LABELS.get(msg.get("severity"), "?"),
                    "hostname": msg.get("hostname") or "",
                    "source_ip": msg.get("source_ip", ""),
                    "app_name": msg.get("app_name") or "",
                    "message": (msg.get("message") or "")[:500],
                    "host_id": msg.get("host_id"),
                    "fields": _extract_fields(msg.get("message", "")),
                    "tags": msg.get("tags", []),
                    "noise_score": msg.get("noise_score", 50),
                    "is_new_template": msg.get("is_new_template", False),
                }
                yield f"data: {json.dumps(data)}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            unsubscribe(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Saved views CRUD ──────────────────────────────────────────────────────────

@router.post("/views", response_class=HTMLResponse)
async def save_view(request: Request, db: AsyncSession = Depends(get_db)):
    form = await request.form()
    name = form.get("view_name", "").strip()
    if not name:
        return HTMLResponse("Name required", status_code=400)
    filters = {
        "severity": form.get("severity", ""),
        "facility": form.get("facility", ""),
        "host": form.get("host", ""),
        "app": form.get("app", ""),
        "q": form.get("q", ""),
        "hours": form.get("hours", "24"),
    }
    db.add(SyslogView(name=name, filters_json=json.dumps(filters)))
    await db.commit()
    qs = "&".join(f"{k}={v}" for k, v in filters.items() if v)
    return HTMLResponse("", status_code=303, headers={"Location": f"/syslog?{qs}"})


@router.post("/views/{view_id}/delete", response_class=HTMLResponse)
async def delete_view(view_id: int, db: AsyncSession = Depends(get_db)):
    await db.execute(delete(SyslogView).where(SyslogView.id == view_id))
    await db.commit()
    return HTMLResponse("", status_code=303, headers={"Location": "/syslog"})


# ── Host detail tab ───────────────────────────────────────────────────────────

@router.get("/api/host/{host_id}", response_class=HTMLResponse)
async def syslog_by_host(
    request: Request,
    host_id: int,
    db: AsyncSession = Depends(get_db),
    hours: int = Query(24),
    page: int = Query(1, ge=1),
    sev: str = Query(""),
    app: str = Query(""),
    q: str = Query(""),
    sort: str = Query("desc"),
):
    since = datetime.utcnow() - timedelta(hours=hours)

    # Resolve source_ip / hostname from PingHost for wider matching
    host_source_ip = ""
    host_name = ""
    ping_host = (await db.execute(select(PingHost).where(PingHost.id == host_id))).scalar()
    if ping_host:
        raw = ping_host.hostname
        for prefix in ("https://", "http://"):
            if raw.startswith(prefix):
                raw = raw[len(prefix):]
        host_source_ip = raw.split("/")[0].split(":")[0]
        host_name = ping_host.name or ""

    sev_list = None
    if sev:
        try:
            sev_list = [int(s.strip()) for s in sev.split(",") if s.strip()]
        except ValueError:
            pass

    where, params = _where_clauses(
        since,
        host_id=host_id,
        host_source_ip=host_source_ip,
        host_name=host_name,
        sev_list=sev_list,
        app=app,
        q=q,
    )
    sort_dir = "ASC" if sort == "asc" else "DESC"
    offset = (page - 1) * _PER_PAGE

    total = int(await ch_scalar(f"SELECT count() FROM syslog_messages WHERE {where}", params) or 0)
    total_pages = max(1, (total + _PER_PAGE - 1) // _PER_PAGE)
    page = min(page, total_pages)

    rows = await ch_query(
        f"""SELECT timestamp, received_at, source_ip, hostname, host_id,
                   facility, severity, app_name, message,
                   template_hash, tags, noise_score
            FROM syslog_messages
            WHERE {where}
            ORDER BY timestamp {sort_dir}
            LIMIT {_PER_PAGE} OFFSET {offset}""",
        params,
    )
    messages = [_row(r) for r in rows]

    app_rows = await ch_query(
        f"""SELECT DISTINCT app_name FROM syslog_messages
            WHERE {where} AND app_name != ''
            ORDER BY app_name LIMIT 100""",
        params,
    )
    app_names = [r["app_name"] for r in app_rows]

    return templates.TemplateResponse("partials/syslog_table.html", {
        "request": request,
        "messages": messages,
        "total": total,
        "page": page,
        "total_pages": total_pages,
        "severity_labels": SEVERITY_LABELS,
        "host_id": host_id,
        "hours": hours,
        "f_sev": sev,
        "f_app": app,
        "f_q": q,
        "f_sort": sort,
        "app_names": app_names,
    })


# ── Template Browser ──────────────────────────────────────────────────────────

@router.get("/templates", response_class=HTMLResponse)
async def template_browser(
    request: Request,
    db: AsyncSession = Depends(get_db),
    sort: str = Query("recent"),
    tag: str = Query(""),
    page: int = Query(1, ge=1),
):
    from models.log_template import LogTemplate, PrecursorPattern

    query = select(LogTemplate)
    count_query = select(func.count(LogTemplate.id))

    if tag:
        query = query.where(LogTemplate.tags.contains(tag))
        count_query = count_query.where(LogTemplate.tags.contains(tag))

    sort_map = {
        "recent": LogTemplate.last_seen.desc(),
        "count": LogTemplate.count.desc(),
        "noise": LogTemplate.noise_score.asc(),
        "new": LogTemplate.first_seen.desc(),
    }
    query = query.order_by(sort_map.get(sort, LogTemplate.last_seen.desc()))

    per_page = 50
    total = (await db.execute(count_query)).scalar() or 0
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, total_pages)

    tpls = (await db.execute(query.offset((page - 1) * per_page).limit(per_page))).scalars().all()

    tpl_ids = [t.id for t in tpls]
    precursor_map = {}
    if tpl_ids:
        precs = (await db.execute(
            select(PrecursorPattern)
            .where(PrecursorPattern.template_id.in_(tpl_ids), PrecursorPattern.confidence >= 0.3)
        )).scalars().all()
        for p in precs:
            precursor_map[p.template_id] = p

    all_tags_raw = (await db.execute(select(LogTemplate.tags).where(LogTemplate.tags != ""))).scalars().all()
    all_tags = sorted({t.strip() for raw in all_tags_raw for t in raw.split(",") if t.strip()})

    return templates.TemplateResponse("syslog_templates.html", {
        "request": request,
        "active_page": "syslog",
        "templates": tpls,
        "precursor_map": precursor_map,
        "total": total,
        "page": page,
        "total_pages": total_pages,
        "all_tags": all_tags,
        "f_sort": sort,
        "f_tag": tag,
    })


# ── Smart Feed API ────────────────────────────────────────────────────────────

# ── Root Cause Suggestions ────────────────────────────────────────────────

@router.get("/api/root-cause/{template_hash}")
async def root_cause_suggestions(
    template_hash: str,
    db: AsyncSession = Depends(get_db),
):
    """For a given log template, find historical occurrences and what happened
    afterwards on the same host — helps users understand root cause patterns."""
    from models.log_template import LogTemplate
    from services.log_intelligence import extract_template

    if not re.match(r"^[a-f0-9]{16}$", template_hash):
        return {"error": "Invalid template hash"}

    since = datetime.utcnow() - timedelta(days=30)

    # 1. How often did this template occur in last 30 days?
    total_count = int(await ch_scalar(
        "SELECT count() FROM syslog_messages "
        "WHERE template_hash = {th:String} AND timestamp >= {since:DateTime64(3)}",
        {"th": template_hash, "since": since},
    ) or 0)

    # 2. Get a sample of recent occurrences with host info (max 200 for analysis)
    occurrences = await ch_query(
        """SELECT timestamp, source_ip, hostname, host_id, severity, app_name
           FROM syslog_messages
           WHERE template_hash = {th:String} AND timestamp >= {since:DateTime64(3)}
           ORDER BY timestamp DESC
           LIMIT 200""",
        {"th": template_hash, "since": since},
    )

    if not occurrences:
        return {"total_count": 0, "aftermath": [], "hosts_affected": 0, "template": ""}

    # 3. Get the template text
    tpl_row = (await db.execute(
        select(LogTemplate.template).where(LogTemplate.template_hash == template_hash)
    )).scalar()

    # 4. For each occurrence, query what happened on the same host in the next 5 minutes
    #    Group by template_hash to find common aftermath patterns.
    #    Use a single batch query: for each (source_ip, timestamp), find follow-up messages.
    aftermath_counts: dict[str, dict] = {}  # template_hash -> {count, template, severity_avg, example}
    hosts_seen = set()

    # Sample up to 50 occurrences for aftermath analysis (avoid huge queries)
    sample = occurrences[:50]
    for occ in sample:
        hosts_seen.add(occ["source_ip"])
        ts = occ["timestamp"]
        ts_end = ts + timedelta(minutes=5) if isinstance(ts, datetime) else datetime.utcnow()

        follow_ups = await ch_query(
            """SELECT template_hash, message, severity
               FROM syslog_messages
               WHERE source_ip = {sip:String}
                 AND timestamp > {ts:DateTime64(3)}
                 AND timestamp <= {ts_end:DateTime64(3)}
                 AND template_hash != {th:String}
                 AND template_hash != ''
               ORDER BY timestamp
               LIMIT 20""",
            {"sip": occ["source_ip"], "ts": ts, "ts_end": ts_end, "th": template_hash},
        )

        for fu in follow_ups:
            fh = fu["template_hash"]
            if fh not in aftermath_counts:
                _, tpl_text = extract_template(fu["message"])
                aftermath_counts[fh] = {
                    "count": 0,
                    "example": fu["message"][:200],
                    "severity_sum": 0,
                    "template_hash": fh,
                }
            aftermath_counts[fh]["count"] += 1
            aftermath_counts[fh]["severity_sum"] += fu["severity"] if fu["severity"] is not None else 6

    # 5. Rank aftermath by frequency and severity
    aftermath_list = []
    for fh, info in aftermath_counts.items():
        pct = round(info["count"] / len(sample) * 100)
        avg_sev = info["severity_sum"] / info["count"] if info["count"] else 6
        aftermath_list.append({
            "template_hash": fh,
            "example": info["example"],
            "frequency": info["count"],
            "percentage": pct,
            "avg_severity": round(avg_sev, 1),
        })

    # Sort by frequency desc, then by severity asc (more severe first)
    aftermath_list.sort(key=lambda x: (-x["frequency"], x["avg_severity"]))

    # 6. Get first/last seen times
    first_seen = occurrences[-1]["timestamp"] if occurrences else None
    last_seen = occurrences[0]["timestamp"] if occurrences else None

    return {
        "total_count": total_count,
        "hosts_affected": len(hosts_seen),
        "template": tpl_row or "",
        "first_seen": first_seen.isoformat() if first_seen else None,
        "last_seen": last_seen.isoformat() if last_seen else None,
        "aftermath": aftermath_list[:10],  # top 10 patterns
        "sample_size": len(sample),
    }


@router.get("/api/smart-feed")
async def smart_feed(
    db: AsyncSession = Depends(get_db),
    hours: int = Query(24),
    max_noise: int = Query(30),
):
    from models.log_template import LogTemplate
    from services.log_intelligence import extract_template

    since = datetime.utcnow() - timedelta(hours=hours)
    where, params = _where_clauses(since)

    rows = await ch_query(
        f"""SELECT timestamp, received_at, source_ip, hostname, host_id,
                   facility, severity, app_name, message,
                   template_hash, tags, noise_score
            FROM syslog_messages
            WHERE {where}
            ORDER BY timestamp DESC
            LIMIT 500""",
        params,
    )

    tpl_scores: dict = {}
    tpls = (await db.execute(select(LogTemplate))).scalars().all()
    for t in tpls:
        tpl_scores[t.template_hash] = t.noise_score

    results = []
    for r in rows:
        _, h = extract_template(r.get("message", ""))
        noise = tpl_scores.get(h, 50)
        if noise > max_noise:
            continue
        results.append({
            "timestamp": localtime(r["timestamp"], "%m-%d %H:%M:%S"),
            "severity": r["severity"],
            "severity_label": SEVERITY_LABELS.get(r["severity"], "?"),
            "hostname": r["hostname"] or "",
            "source_ip": r["source_ip"],
            "app_name": r["app_name"] or "",
            "message": (r["message"] or "")[:300],
            "noise_score": noise,
            "host_id": r["host_id"],
        })
        if len(results) >= 100:
            break

    return results
