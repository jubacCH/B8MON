import json
from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from collectors.firewall import (
    OPNsenseAPI,
    PfsenseAPI,
    parse_opnsense_data,
    parse_pfsense_data,
)
from database import (
    FirewallInstance,
    FirewallSnapshot,
    decrypt_value,
    encrypt_value,
    get_db,
)

router = APIRouter(prefix="/firewall")
templates = Jinja2Templates(directory="templates")


def _load_snapshot_data(snap: FirewallSnapshot | None) -> tuple[dict | None, str | None]:
    """Return (data_dict, error_str) from a snapshot row."""
    if snap is None:
        return None, None
    if snap.ok and snap.data_json:
        return json.loads(snap.data_json), None
    return None, snap.error


# ── List ──────────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def firewall_list(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(FirewallInstance).order_by(FirewallInstance.name)
    )
    instances = result.scalars().all()

    instance_data = []
    for inst in instances:
        snap = (await db.execute(
            select(FirewallSnapshot)
            .where(FirewallSnapshot.instance_id == inst.id)
            .order_by(FirewallSnapshot.timestamp.desc())
            .limit(1)
        )).scalar_one_or_none()

        data, error = _load_snapshot_data(snap)
        instance_data.append({
            "instance": inst,
            "data": data,
            "error": error,
            "last_checked": snap.timestamp if snap else None,
        })

    return templates.TemplateResponse("firewall_list.html", {
        "request": request,
        "instance_data": instance_data,
        "active_page": "firewall",
    })


# ── Detail ────────────────────────────────────────────────────────────────────

@router.get("/{instance_id}", response_class=HTMLResponse)
async def firewall_detail(
    instance_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    instance = await db.get(FirewallInstance, instance_id)
    if not instance:
        return RedirectResponse("/firewall")

    error = None
    data = None

    snap = (await db.execute(
        select(FirewallSnapshot)
        .where(
            FirewallSnapshot.instance_id == instance_id,
            FirewallSnapshot.ok == True,
        )
        .order_by(FirewallSnapshot.timestamp.desc())
        .limit(1)
    )).scalar_one_or_none()

    if snap and snap.data_json:
        data = json.loads(snap.data_json)
    else:
        # Fall back to a live fetch
        try:
            data = await _live_fetch(instance)
        except Exception as exc:
            error = str(exc)

    last_checked = snap.timestamp if snap else None

    return templates.TemplateResponse("firewall_detail.html", {
        "request": request,
        "instance": instance,
        "data": data,
        "error": error,
        "last_checked": last_checked,
        "active_page": "firewall",
    })


async def _live_fetch(instance: FirewallInstance) -> dict:
    """Perform a live fetch for the given firewall instance."""
    if instance.fw_type == "opnsense":
        api_key = decrypt_value(instance.api_key_enc) if instance.api_key_enc else ""
        api_secret = decrypt_value(instance.api_secret_enc) if instance.api_secret_enc else ""
        api = OPNsenseAPI(
            host=instance.host,
            api_key=api_key,
            api_secret=api_secret,
            verify_ssl=instance.verify_ssl,
        )
        raw = await api.fetch_all()
        parsed = parse_opnsense_data(raw["firmware"], raw["status"])
        # Enrich interface list from raw data
        ifaces_raw = raw.get("interfaces", {})
        if isinstance(ifaces_raw, dict):
            parsed["interfaces"] = [
                {"name": k, "description": v, "ipv4": "", "status": "up"}
                for k, v in ifaces_raw.items()
            ]
        return parsed
    else:
        username = instance.username or ""
        password = decrypt_value(instance.password_enc) if instance.password_enc else ""
        api = PfsenseAPI(
            host=instance.host,
            username=username,
            password=password,
            verify_ssl=instance.verify_ssl,
        )
        raw = await api.fetch_all()
        parsed = parse_pfsense_data(raw.get("sys_info", {}))
        # Enrich interfaces from raw data
        ifaces_raw = raw.get("interfaces", {})
        data_ifaces = ifaces_raw.get("data", ifaces_raw) if isinstance(ifaces_raw, dict) else {}
        if isinstance(data_ifaces, dict):
            parsed["interfaces"] = [
                {
                    "name": k,
                    "description": v.get("descr", k) if isinstance(v, dict) else str(v),
                    "ipv4": v.get("ipaddr", "") if isinstance(v, dict) else "",
                    "status": v.get("status", "up") if isinstance(v, dict) else "up",
                }
                for k, v in data_ifaces.items()
            ]
        return parsed


# ── Add ───────────────────────────────────────────────────────────────────────

@router.post("/add")
async def add_instance(
    name: str = Form(...),
    host: str = Form(...),
    fw_type: str = Form("opnsense"),
    api_key: str = Form(""),
    api_secret: str = Form(""),
    username: str = Form(""),
    password: str = Form(""),
    verify_ssl: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    db.add(FirewallInstance(
        name=name.strip(),
        host=host.strip().rstrip("/"),
        fw_type=fw_type,
        username=username.strip() or None,
        password_enc=encrypt_value(password) if password.strip() else None,
        api_key_enc=encrypt_value(api_key) if api_key.strip() else None,
        api_secret_enc=encrypt_value(api_secret) if api_secret.strip() else None,
        verify_ssl=(verify_ssl == "on"),
    ))
    await db.commit()
    return RedirectResponse("/firewall", status_code=303)


# ── Edit ──────────────────────────────────────────────────────────────────────

@router.post("/{instance_id}/edit")
async def edit_instance(
    instance_id: int,
    name: str = Form(...),
    host: str = Form(...),
    fw_type: str = Form("opnsense"),
    api_key: str = Form(""),
    api_secret: str = Form(""),
    username: str = Form(""),
    password: str = Form(""),
    verify_ssl: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    instance = await db.get(FirewallInstance, instance_id)
    if instance:
        instance.name = name.strip()
        instance.host = host.strip().rstrip("/")
        instance.fw_type = fw_type
        instance.username = username.strip() or None
        instance.verify_ssl = (verify_ssl == "on")
        if password.strip():
            instance.password_enc = encrypt_value(password.strip())
        if api_key.strip():
            instance.api_key_enc = encrypt_value(api_key.strip())
        if api_secret.strip():
            instance.api_secret_enc = encrypt_value(api_secret.strip())
        await db.commit()
    return RedirectResponse(f"/firewall/{instance_id}", status_code=303)


# ── Delete ────────────────────────────────────────────────────────────────────

@router.post("/{instance_id}/delete")
async def delete_instance(instance_id: int, db: AsyncSession = Depends(get_db)):
    instance = await db.get(FirewallInstance, instance_id)
    if instance:
        await db.delete(instance)
        await db.commit()
    return RedirectResponse("/firewall", status_code=303)
