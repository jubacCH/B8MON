import json
from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from collectors.proxmox import ProxmoxAPI, import_proxmox_hosts, parse_cluster_data
from database import PingHost, ProxmoxCluster, ProxmoxSnapshot, UnifiSnapshot, decrypt_value, encrypt_value, get_db

router = APIRouter(prefix="/proxmox")
templates = Jinja2Templates(directory="templates")


# ── API (JSON) — before /{cluster_id} ────────────────────────────────────────

@router.get("/api/{cluster_id}/status")
async def api_cluster_status(cluster_id: int, db: AsyncSession = Depends(get_db)):
    cluster = await db.get(ProxmoxCluster, cluster_id)
    if not cluster:
        return {"error": "Cluster not found"}

    # Return latest cached snapshot written by the background scheduler
    result = await db.execute(
        select(ProxmoxSnapshot)
        .where(ProxmoxSnapshot.cluster_id == cluster_id)
        .order_by(ProxmoxSnapshot.timestamp.desc())
        .limit(1)
    )
    snapshot = result.scalar_one_or_none()

    if snapshot:
        base = {"cluster_id": cluster_id, "name": cluster.name,
                 "cached_at": snapshot.timestamp.isoformat()}
        if snapshot.ok:
            return {"ok": True, **base, **json.loads(snapshot.data_json)}
        return {"ok": False, **base, "error": snapshot.error}

    # No snapshot yet (scheduler hasn't run) – fall back to a live fetch
    try:
        api = ProxmoxAPI(
            host=cluster.host,
            token_id=cluster.token_id,
            token_secret=decrypt_value(cluster.token_secret),
            verify_ssl=cluster.verify_ssl,
        )
        resources = await api.cluster_resources()
        status = await api.cluster_status()
        data = parse_cluster_data(resources, status)
        return {"ok": True, "cluster_id": cluster_id, "name": cluster.name, **data}
    except Exception as exc:
        return {"ok": False, "cluster_id": cluster_id, "name": cluster.name, "error": str(exc)}


# ── HTML views ────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def proxmox_list(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ProxmoxCluster).order_by(ProxmoxCluster.name))
    clusters = result.scalars().all()
    return templates.TemplateResponse("proxmox.html", {
        "request": request,
        "clusters": clusters,
        "active_page": "proxmox",
    })


@router.get("/{cluster_id}", response_class=HTMLResponse)
async def proxmox_detail(cluster_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    cluster = await db.get(ProxmoxCluster, cluster_id)
    if not cluster:
        return RedirectResponse(url="/proxmox")

    error = None
    data = None
    vm_history_json = "{}"

    # Use latest cached snapshot
    latest_snap = (await db.execute(
        select(ProxmoxSnapshot)
        .where(ProxmoxSnapshot.cluster_id == cluster_id, ProxmoxSnapshot.ok == True)
        .order_by(ProxmoxSnapshot.timestamp.desc())
        .limit(1)
    )).scalar_one_or_none()

    if latest_snap:
        data = json.loads(latest_snap.data_json)
    else:
        # Fall back to live fetch if no snapshot exists yet
        try:
            api = ProxmoxAPI(
                host=cluster.host,
                token_id=cluster.token_id,
                token_secret=decrypt_value(cluster.token_secret),
                verify_ssl=cluster.verify_ssl,
            )
            resources = await api.cluster_resources()
            status = await api.cluster_status()
            data = parse_cluster_data(resources, status)
        except Exception as e:
            error = str(e)

    # Compute IO rates from the two most recent snapshots
    if data:
        snap_pair = (await db.execute(
            select(ProxmoxSnapshot)
            .where(ProxmoxSnapshot.cluster_id == cluster_id, ProxmoxSnapshot.ok == True)
            .order_by(ProxmoxSnapshot.timestamp.desc())
            .limit(2)
        )).scalars().all()

        if len(snap_pair) == 2:
            snap_now, snap_prev = snap_pair[0], snap_pair[1]
            dt = (snap_now.timestamp - snap_prev.timestamp).total_seconds()
            if dt > 0:
                data_prev = json.loads(snap_prev.data_json)
                prev_map = {
                    g["id"]: g
                    for g in data_prev.get("vms", []) + data_prev.get("containers", [])
                    if "id" in g
                }

                def _rate(curr: dict, prev: dict, key: str) -> float | None:
                    delta = curr.get(key, 0) - prev.get(key, 0)
                    if delta < 0:
                        return None  # counter reset (reboot)
                    return round(delta / dt / 1024, 1)  # KB/s

                for g in data.get("vms", []) + data.get("containers", []):
                    pg = prev_map.get(g.get("id"))
                    if pg:
                        g["netin_kbs"]    = _rate(g, pg, "netin")
                        g["netout_kbs"]   = _rate(g, pg, "netout")
                        g["diskread_kbs"] = _rate(g, pg, "diskread")
                        g["diskwrite_kbs"]= _rate(g, pg, "diskwrite")

    # Build per-VM CPU/RAM time series from last 24h snapshots
    if data:
        window_24h = datetime.utcnow() - timedelta(hours=24)
        hist_snaps = (await db.execute(
            select(ProxmoxSnapshot)
            .where(
                ProxmoxSnapshot.cluster_id == cluster_id,
                ProxmoxSnapshot.ok == True,
                ProxmoxSnapshot.timestamp >= window_24h,
            )
            .order_by(ProxmoxSnapshot.timestamp.asc())
        )).scalars().all()

        vm_series: dict = defaultdict(lambda: {"labels": [], "cpu": [], "mem": []})
        for snap in hist_snaps:
            snap_data = json.loads(snap.data_json)
            label = snap.timestamp.strftime("%H:%M")
            for g in snap_data.get("vms", []) + snap_data.get("containers", []):
                key = str(g.get("id", g.get("name", "")))
                vm_series[key]["labels"].append(label)
                vm_series[key]["cpu"].append(round(g.get("cpu_pct", 0), 1))
                vm_series[key]["mem"].append(round(g.get("mem_pct", 0) if "mem_pct" in g else
                    (g.get("mem_used_gb", 0) / g.get("mem_total_gb", 1) * 100
                     if g.get("mem_total_gb") else 0), 1))

        vm_history_json = json.dumps(dict(vm_series))

    # Build hostname/name → PingHost.id map for linking VMs to their host objects
    all_ping_hosts = (await db.execute(select(PingHost))).scalars().all()
    ping_host_map: dict[str, int] = {}
    for h in all_ping_hosts:
        ping_host_map[h.hostname] = h.id
        ping_host_map.setdefault(h.name, h.id)  # name as fallback key

    # Fetch VM/LXC MAC addresses (from configs) for display
    vm_macs: dict[int, str] = {}
    if data:
        try:
            api = ProxmoxAPI(
                host=cluster.host,
                token_id=cluster.token_id,
                token_secret=decrypt_value(cluster.token_secret),
                verify_ssl=cluster.verify_ssl,
            )
            all_guests = data.get("vms", []) + data.get("containers", [])
            vm_macs = await api.fetch_guest_macs(all_guests)
        except Exception:
            pass

    # Build UniFi client lookup by MAC for cross-referencing
    unifi_by_mac: dict[str, dict] = {}
    unifi_snaps = (await db.execute(
        select(UnifiSnapshot).where(UnifiSnapshot.ok == True)
        .order_by(UnifiSnapshot.timestamp.desc())
        .limit(20)
    )).scalars().all()
    seen_ctrl: set[int] = set()
    for us in unifi_snaps:
        if us.controller_id in seen_ctrl:
            continue
        seen_ctrl.add(us.controller_id)
        try:
            ud = json.loads(us.data_json)
            for c in ud.get("clients", []):
                mac = (c.get("mac") or "").upper()
                if mac:
                    unifi_by_mac[mac] = c
        except Exception:
            pass

    return templates.TemplateResponse("proxmox_detail.html", {
        "request": request,
        "cluster": cluster,
        "data": data,
        "error": error,
        "vm_history_json": vm_history_json,
        "ping_host_map": ping_host_map,
        "vm_macs": vm_macs,
        "unifi_by_mac": unifi_by_mac,
        "active_page": "proxmox",
    })


# ── Host import ───────────────────────────────────────────────────────────────

@router.post("/{cluster_id}/import-hosts")
async def import_hosts(cluster_id: int, db: AsyncSession = Depends(get_db)):
    """Import all running VMs and LXC containers as PingHosts."""
    cluster = await db.get(ProxmoxCluster, cluster_id)
    if not cluster:
        return JSONResponse({"error": "Cluster not found"}, status_code=404)

    # Use latest snapshot; fall back to live fetch
    snap = (await db.execute(
        select(ProxmoxSnapshot)
        .where(ProxmoxSnapshot.cluster_id == cluster_id, ProxmoxSnapshot.ok == True)
        .order_by(ProxmoxSnapshot.timestamp.desc())
        .limit(1)
    )).scalar_one_or_none()

    if snap:
        data = json.loads(snap.data_json)
    else:
        try:
            api = ProxmoxAPI(
                host=cluster.host,
                token_id=cluster.token_id,
                token_secret=decrypt_value(cluster.token_secret),
                verify_ssl=cluster.verify_ssl,
            )
            data = parse_cluster_data(await api.cluster_resources(), await api.cluster_status())
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)

    result = await import_proxmox_hosts(cluster.name, data, db)
    return JSONResponse(result)


# ── CRUD actions ──────────────────────────────────────────────────────────────

@router.post("/add")
async def add_cluster(
    name: str = Form(...),
    host: str = Form(...),
    token_id: str = Form(...),
    token_secret: str = Form(...),
    verify_ssl: str = Form("off"),
    db: AsyncSession = Depends(get_db),
):
    db.add(ProxmoxCluster(
        name=name.strip(),
        host=host.strip().rstrip("/"),
        token_id=token_id.strip(),
        token_secret=encrypt_value(token_secret.strip()),
        verify_ssl=(verify_ssl == "on"),
    ))
    await db.commit()
    return RedirectResponse(url="/proxmox", status_code=303)


@router.post("/{cluster_id}/delete")
async def delete_cluster(cluster_id: int, db: AsyncSession = Depends(get_db)):
    cluster = await db.get(ProxmoxCluster, cluster_id)
    if cluster:
        await db.delete(cluster)
        await db.commit()
    return RedirectResponse(url="/proxmox", status_code=303)


@router.post("/{cluster_id}/edit")
async def edit_cluster(
    cluster_id: int,
    name: str = Form(...),
    host: str = Form(...),
    token_id: str = Form(...),
    token_secret: str = Form(""),
    verify_ssl: str = Form("off"),
    db: AsyncSession = Depends(get_db),
):
    cluster = await db.get(ProxmoxCluster, cluster_id)
    if cluster:
        cluster.name = name.strip()
        cluster.host = host.strip().rstrip("/")
        cluster.token_id = token_id.strip()
        cluster.verify_ssl = (verify_ssl == "on")
        if token_secret.strip():
            cluster.token_secret = encrypt_value(token_secret.strip())
        await db.commit()
    return RedirectResponse(url=f"/proxmox/{cluster_id}", status_code=303)
