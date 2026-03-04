import json
from collections import Counter
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from collectors.unifi import UnifiAPI
from database import PingHost, UnifiController, UnifiSnapshot, decrypt_value, encrypt_value, get_db

router = APIRouter(prefix="/unifi")
templates = Jinja2Templates(directory="templates")


def _fmt_bytes(b: int | float) -> str:
    """Format bytes/s into human-readable string."""
    b = b or 0
    for unit in ("B/s", "KB/s", "MB/s", "GB/s"):
        if b < 1000:
            return f"{b:.1f} {unit}" if unit != "B/s" else f"{int(b)} {unit}"
        b /= 1000
    return f"{b:.1f} TB/s"


def _fmt_uptime(seconds: int) -> str:
    if not seconds:
        return "—"
    d, r = divmod(int(seconds), 86400)
    h, r = divmod(r, 3600)
    m    = r // 60
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


templates.env.globals["fmt_bytes"]  = _fmt_bytes
templates.env.globals["fmt_uptime"] = _fmt_uptime


# ── List / overview ────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def unifi_list(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(UnifiController).order_by(UnifiController.name))
    controllers = result.scalars().all()

    controller_data = []
    for ctrl in controllers:
        snap = (await db.execute(
            select(UnifiSnapshot)
            .where(UnifiSnapshot.controller_id == ctrl.id, UnifiSnapshot.ok == True)
            .order_by(UnifiSnapshot.timestamp.desc())
            .limit(1)
        )).scalar_one_or_none()

        data = json.loads(snap.data_json) if snap else None
        controller_data.append({
            "ctrl":     ctrl,
            "snap":     snap,
            "data":     data,
            "totals":   data["totals"] if data else None,
            "wan":      data["wan"]    if data else None,
        })

    return templates.TemplateResponse("unifi.html", {
        "request": request,
        "controller_data": controller_data,
        "active_page": "unifi",
    })


# ── Detail ─────────────────────────────────────────────────────────────────────

@router.get("/{ctrl_id}", response_class=HTMLResponse)
async def unifi_detail(ctrl_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    ctrl = await db.get(UnifiController, ctrl_id)
    if not ctrl:
        return RedirectResponse(url="/unifi")

    # Latest successful snapshot
    snap = (await db.execute(
        select(UnifiSnapshot)
        .where(UnifiSnapshot.controller_id == ctrl_id, UnifiSnapshot.ok == True)
        .order_by(UnifiSnapshot.timestamp.desc())
        .limit(1)
    )).scalar_one_or_none()

    latest_err = (await db.execute(
        select(UnifiSnapshot)
        .where(UnifiSnapshot.controller_id == ctrl_id)
        .order_by(UnifiSnapshot.timestamp.desc())
        .limit(1)
    )).scalar_one_or_none()

    data = json.loads(snap.data_json) if snap else None

    # Historical snapshots (last 24h) for charts
    window_24h = datetime.utcnow() - timedelta(hours=24)
    hist_snaps = (await db.execute(
        select(UnifiSnapshot)
        .where(
            UnifiSnapshot.controller_id == ctrl_id,
            UnifiSnapshot.ok == True,
            UnifiSnapshot.timestamp >= window_24h,
        )
        .order_by(UnifiSnapshot.timestamp.asc())
    )).scalars().all()

    # Build chart data: clients over time + WAN throughput
    chart_labels, chart_clients, chart_wan_rx, chart_wan_tx = [], [], [], []
    for hs in hist_snaps:
        hd = json.loads(hs.data_json)
        chart_labels.append(hs.timestamp.strftime("%H:%M"))
        chart_clients.append(hd.get("totals", {}).get("clients_total", 0))
        chart_wan_rx.append(round((hd.get("wan", {}).get("rx_bytes_r", 0) or 0) / 1000, 1))  # KB/s
        chart_wan_tx.append(round((hd.get("wan", {}).get("tx_bytes_r", 0) or 0) / 1000, 1))

    # ── Ping host cross-reference ──────────────────────────────────────────────
    all_hosts = (await db.execute(select(PingHost))).scalars().all()
    # Build lookup: IP → host.id  and  MAC → host.id
    _host_by_ip:  dict[str, int] = {h.hostname: h.id for h in all_hosts if h.hostname}
    _host_by_mac: dict[str, int] = {
        h.mac_address.lower(): h.id for h in all_hosts if h.mac_address
    }

    def _ping_host_id(ip: str, mac: str) -> int | None:
        return (_host_by_ip.get((ip or "").strip())
                or _host_by_mac.get((mac or "").strip().lower()))

    # ── Analysis data ──────────────────────────────────────────────────────────
    clients_list = data["clients"] if data else []
    devices_list = data["devices"] if data else []
    events_list  = data.get("events", []) if data else []

    # Top 10 clients by total session traffic
    top_clients = sorted(
        clients_list,
        key=lambda c: (c.get("rx_bytes") or 0) + (c.get("tx_bytes") or 0),
        reverse=True,
    )[:10]

    # Clients per AP (wireless only)
    ap_counts: Counter = Counter(
        c["ap_name"] or c["ap_mac"] or "Unknown"
        for c in clients_list if c.get("is_wireless")
    )

    # Signal quality distribution
    sig_bins = {"Excellent (≥−50)": 0, "Good (−50 to −65)": 0,
                "Fair (−65 to −75)": 0, "Poor (<−75)": 0}
    for c in clients_list:
        if not c.get("is_wireless"):
            continue
        s = c.get("signal") or 0
        if s >= -50:
            sig_bins["Excellent (≥−50)"] += 1
        elif s >= -65:
            sig_bins["Good (−50 to −65)"] += 1
        elif s >= -75:
            sig_bins["Fair (−65 to −75)"] += 1
        else:
            sig_bins["Poor (<−75)"] += 1

    # VLAN distribution
    vlan_counts: Counter = Counter(
        f"VLAN {c.get('vlan', 1) or 1}" for c in clients_list
    )

    def _fmt_mb(b: int | float) -> str:
        b = b or 0
        mb = b / 1_000_000
        if mb >= 1000:
            return f"{mb/1000:.1f} GB"
        return f"{mb:.0f} MB"

    return templates.TemplateResponse("unifi_detail.html", {
        "request":      request,
        "ctrl":         ctrl,
        "snap":         snap,
        "latest_err":   latest_err,
        "data":         data,
        "devices":      devices_list,
        "clients":      clients_list,
        "events":       events_list,
        "totals":       data["totals"]  if data else {},
        "wan":          data["wan"]     if data else {},
        "lan":          data["lan"]     if data else {},
        "wlan":         data["wlan"]    if data else {},
        "chart_labels":  chart_labels,
        "chart_clients": chart_clients,
        "chart_wan_rx":  chart_wan_rx,
        "chart_wan_tx":  chart_wan_tx,
        # Analysis
        "ping_host_id":      _ping_host_id,
        "top_clients":       top_clients,
        "fmt_mb":            _fmt_mb,
        "ap_labels":         list(ap_counts.keys()),
        "ap_values":         list(ap_counts.values()),
        "sig_labels":        list(sig_bins.keys()),
        "sig_values":        list(sig_bins.values()),
        "vlan_labels":       list(vlan_counts.keys()),
        "vlan_values":       list(vlan_counts.values()),
        "active_page":  "unifi",
        "active_tab":   request.query_params.get("tab", "overview"),
        "saved":        request.query_params.get("saved"),
    })


# ── CRUD ───────────────────────────────────────────────────────────────────────

@router.post("/add")
async def add_controller(
    name:       str  = Form(...),
    host:       str  = Form(...),
    username:   str  = Form(...),
    password:   str  = Form(...),
    site:       str  = Form("default"),
    verify_ssl: str  = Form(""),
    is_udm:     str  = Form(""),
    db: AsyncSession = Depends(get_db),
):
    db.add(UnifiController(
        name         = name.strip(),
        host         = host.strip().rstrip("/"),
        username     = username.strip(),
        password_enc = encrypt_value(password),
        site         = site.strip() or "default",
        verify_ssl   = bool(verify_ssl),
        is_udm       = bool(is_udm),
    ))
    await db.commit()
    return RedirectResponse(url="/unifi", status_code=303)


@router.post("/{ctrl_id}/edit")
async def edit_controller(
    ctrl_id:    int,
    name:       str  = Form(...),
    host:       str  = Form(...),
    username:   str  = Form(...),
    password:   str  = Form(""),
    site:       str  = Form("default"),
    verify_ssl: str  = Form(""),
    is_udm:     str  = Form(""),
    db: AsyncSession = Depends(get_db),
):
    ctrl = await db.get(UnifiController, ctrl_id)
    if ctrl:
        ctrl.name       = name.strip()
        ctrl.host       = host.strip().rstrip("/")
        ctrl.username   = username.strip()
        ctrl.site       = site.strip() or "default"
        ctrl.verify_ssl = bool(verify_ssl)
        ctrl.is_udm     = bool(is_udm)
        if password.strip():
            ctrl.password_enc = encrypt_value(password)
        await db.commit()
    return RedirectResponse(url=f"/unifi/{ctrl_id}?tab=settings&saved=1", status_code=303)


@router.post("/{ctrl_id}/delete")
async def delete_controller(ctrl_id: int, db: AsyncSession = Depends(get_db)):
    ctrl = await db.get(UnifiController, ctrl_id)
    if ctrl:
        await db.delete(ctrl)
        await db.commit()
    return RedirectResponse(url="/unifi", status_code=303)


# ── Live test ──────────────────────────────────────────────────────────────────

@router.get("/api/{ctrl_id}/test")
async def test_connection(ctrl_id: int, db: AsyncSession = Depends(get_db)):
    ctrl = await db.get(UnifiController, ctrl_id)
    if not ctrl:
        return JSONResponse({"ok": False, "error": "Controller not found"})
    try:
        api = UnifiAPI(
            host       = ctrl.host,
            username   = ctrl.username,
            password   = decrypt_value(ctrl.password_enc),
            site       = ctrl.site,
            verify_ssl = ctrl.verify_ssl,
            is_udm     = ctrl.is_udm,
        )
        data = await api.fetch_all()
        return JSONResponse({
            "ok": True,
            "devices": data["totals"]["devices"],
            "clients": data["totals"]["clients_total"],
        })
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)})
