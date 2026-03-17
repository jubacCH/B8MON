"""
Generic router for all integrations.

Provides CRUD (list, detail, add, edit, delete), test-connection,
and JSON API endpoints for every registered integration.
Integration-specific custom routes can be added via BaseIntegration.get_router().
"""
from __future__ import annotations

import json
import logging

log = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from templating import templates
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select as sa_select

import ipaddress
import re
from urllib.parse import urlparse

from integrations import get_registry, get_integration
from integrations._base import BaseIntegration
from database import PingHost
from models.base import get_db
from services import integration as int_svc
from services import snapshot as snap_svc


def _require_editor(request: Request):
    """Return True (= blocked) if the current user is readonly."""
    user = getattr(request.state, "current_user", None)
    role = getattr(user, "role", "admin") or "admin"
    if role == "readonly":
        return True
    return False


def _validate_host(value: str) -> str | None:
    """Validate a host/URL config value against SSRF.

    Returns an error message if blocked, None if OK.
    Allows RFC1918 private ranges (homelab use case) but blocks
    loopback, link-local, and cloud metadata IPs.
    """
    if not value:
        return None

    # Extract hostname from URL or bare host
    host = value.strip()
    if "://" in host:
        parsed = urlparse(host)
        host = parsed.hostname or ""
    # Strip port
    host = re.sub(r":\d+$", "", host)

    if not host:
        return None

    # Block obvious localhost aliases
    if host.lower() in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        return "Loopback addresses are not allowed"

    # Try to parse as IP
    try:
        addr = ipaddress.ip_address(host)
        if addr.is_loopback:
            return "Loopback addresses are not allowed"
        if addr.is_link_local:
            return "Link-local addresses are not allowed (cloud metadata risk)"
    except ValueError:
        # It's a hostname — block metadata hostnames
        if host.lower() in ("metadata.google.internal", "instance-data"):
            return "Cloud metadata endpoints are not allowed"

    return None


def _validate_config_hosts(config_dict: dict, fields) -> str | None:
    """Validate all host/url fields in a config dict."""
    for f in fields:
        if f.key in ("host", "url", "base_url", "server", "address"):
            err = _validate_host(str(config_dict.get(f.key, "")))
            if err:
                return f"{f.label}: {err}"
    return None

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Helpers ───────────────────────────────────────────────────────────────────


def _parse_form_config(integration_cls: type[BaseIntegration], form: dict,
                       existing_config: dict | None = None) -> dict:
    """
    Extract config values from submitted form data based on config_fields.
    For edit operations, if a password field is empty, keep the existing value.
    """
    config = {}
    for field in integration_cls.config_fields:
        raw = form.get(field.key, "")
        if isinstance(raw, str):
            raw = raw.strip()

        if field.field_type == "checkbox":
            config[field.key] = raw in ("on", "true", "True", True, "1")
        elif field.field_type == "password":
            # Don't overwrite existing secret if form field is empty
            if not raw and existing_config:
                config[field.key] = existing_config.get(field.key, "")
            else:
                config[field.key] = raw
        elif field.field_type == "number":
            try:
                config[field.key] = int(raw) if raw else field.default
            except (ValueError, TypeError):
                config[field.key] = field.default
        else:
            config[field.key] = raw if raw else (field.default or "")
    return config


# ── List page ─────────────────────────────────────────────────────────────────


@router.get("/integration/{integration_type}", response_class=HTMLResponse)
async def list_instances(
    request: Request,
    integration_type: str,
    db: AsyncSession = Depends(get_db),
):
    integration_cls = get_integration(integration_type)
    if not integration_cls:
        return HTMLResponse("Integration not found", status_code=404)

    configs = await int_svc.get_all_configs(db, integration_type)
    snapshots = await snap_svc.get_latest_batch(db, integration_type)

    instances = []
    for cfg in configs:
        snap = snapshots.get(cfg.id)
        data = json.loads(snap.data_json) if snap and snap.data_json else None
        instances.append({
            "config": cfg,
            "snap": snap,
            "data": data,
        })

    template_name = f"integrations/{integration_type}.html"
    # Fall back to generic list template if integration-specific one doesn't exist
    try:
        templates.get_template(template_name)
    except Exception:
        template_name = "integrations/_list.html"

    return templates.TemplateResponse(template_name, {
        "request": request,
        "integration": integration_cls,
        "instances": instances,
        "active_page": integration_type,
        "saved": request.query_params.get("saved"),
    })


# ── Detail page ───────────────────────────────────────────────────────────────


@router.get("/integration/{integration_type}/{config_id}", response_class=HTMLResponse)
async def detail(
    request: Request,
    integration_type: str,
    config_id: int,
    db: AsyncSession = Depends(get_db),
):
    integration_cls = get_integration(integration_type)
    if not integration_cls:
        return HTMLResponse("Integration not found", status_code=404)

    cfg = await int_svc.get_config(db, config_id)
    if not cfg or cfg.type != integration_type:
        return HTMLResponse("Instance not found", status_code=404)

    snap = await snap_svc.get_latest(db, integration_type, config_id)
    data = None
    error = None

    if snap and snap.data_json:
        data = json.loads(snap.data_json)
    elif snap and snap.error:
        error = snap.error

    # If no snapshot exists, try a live fetch
    if data is None and error is None:
        try:
            config_dict = int_svc.decrypt_config(cfg.config_json)
            instance = integration_cls(config=config_dict)
            result = await instance.collect()
            if result.success:
                data = result.data
                await snap_svc.save(db, integration_type, config_id, True, data)
                await db.commit()
            else:
                error = result.error
        except Exception as exc:
            error = str(exc)

    config_dict = int_svc.decrypt_config(cfg.config_json)

    # Get extra context from integration
    extra_ctx = {}
    if data:
        try:
            instance = integration_cls(config=config_dict)
            extra_ctx = instance.get_detail_context(data, config_dict)
        except Exception:
            pass

    template_name = f"integrations/{integration_type}_detail.html"
    try:
        templates.get_template(template_name)
    except Exception:
        template_name = f"integrations/{integration_type}.html"
        try:
            templates.get_template(template_name)
        except Exception:
            template_name = "integrations/_detail.html"

    # Build IP/name → host_id map for linking devices to hosts
    ip_to_host_id: dict[str, int] = {}
    try:
        ping_hosts = (await db.execute(sa_select(PingHost))).scalars().all()
        for ph in ping_hosts:
            raw = ph.hostname
            for prefix in ("https://", "http://"):
                if raw.startswith(prefix):
                    raw = raw[len(prefix):]
            raw = raw.split("/")[0].split(":")[0]
            ip_to_host_id[raw] = ph.id
            if ph.name:
                ip_to_host_id[ph.name.lower()] = ph.id
    except Exception:
        pass

    ctx = {
        "request": request,
        "integration": integration_cls,
        "config": cfg,
        "config_dict": config_dict,
        "snap": snap,
        "data": data,
        "error": error,
        "active_page": integration_type,
        "active_tab": request.query_params.get("tab", "overview"),
        "saved": request.query_params.get("saved"),
        "ip_to_host_id": ip_to_host_id,
        **extra_ctx,
    }
    return templates.TemplateResponse(template_name, ctx)


# ── JSON API: config fields ──────────────────────────────────────────────────


@router.get("/api/integration/{integration_type}/fields")
async def api_config_fields(integration_type: str):
    """Return the config fields schema for an integration type."""
    integration_cls = get_integration(integration_type)
    if not integration_cls:
        return JSONResponse({"error": "Unknown integration type"}, status_code=404)
    fields = []
    for f in integration_cls.config_fields:
        fields.append({
            "key": f.key,
            "label": f.label,
            "field_type": f.field_type,
            "placeholder": f.placeholder or "",
            "required": f.required,
            "default": f.default if f.default is not None else "",
            "options": f.options if hasattr(f, "options") and f.options else None,
        })
    return JSONResponse({
        "type": integration_type,
        "display_name": integration_cls.display_name,
        "description": integration_cls.description,
        "fields": fields,
    })


# ── JSON API: create instance ────────────────────────────────────────────────


@router.post("/api/integration/{integration_type}/create")
async def api_create_instance(
    request: Request,
    integration_type: str,
    db: AsyncSession = Depends(get_db),
):
    """JSON API for creating an integration instance."""
    if _require_editor(request):
        return JSONResponse({"error": "Read-only access"}, status_code=403)
    integration_cls = get_integration(integration_type)
    if not integration_cls:
        return JSONResponse({"error": "Unknown integration type"}, status_code=404)

    body = await request.json()
    name = str(body.get("name", "")).strip()
    if not name:
        name = f"{integration_cls.display_name} Instance"
    config_dict = {}
    for field in integration_cls.config_fields:
        val = body.get(field.key, "")
        if field.field_type == "checkbox":
            config_dict[field.key] = bool(val)
        elif field.field_type == "number":
            try:
                config_dict[field.key] = int(val) if val else (field.default if field.default is not None else 0)
            except (ValueError, TypeError):
                config_dict[field.key] = field.default if field.default is not None else 0
        else:
            config_dict[field.key] = str(val).strip() if val else (str(field.default) if field.default is not None else "")
    host_err = _validate_config_hosts(config_dict, integration_cls.config_fields)
    if host_err:
        return JSONResponse({"error": host_err}, status_code=400)
    cfg = await int_svc.create_config(db, integration_type, name, config_dict)
    from main import invalidate_nav_cache
    invalidate_nav_cache()
    return JSONResponse({"ok": True, "id": cfg.id, "name": cfg.name})


# ── JSON API: edit instance ──────────────────────────────────────────────────


@router.patch("/api/integration/{integration_type}/{config_id}")
async def api_edit_instance(
    request: Request,
    integration_type: str,
    config_id: int,
    db: AsyncSession = Depends(get_db),
):
    """JSON API for editing an integration instance."""
    if _require_editor(request):
        return JSONResponse({"error": "Read-only access"}, status_code=403)
    integration_cls = get_integration(integration_type)
    if not integration_cls:
        return JSONResponse({"error": "Unknown integration type"}, status_code=404)

    cfg = await int_svc.get_config(db, config_id)
    if not cfg or cfg.type != integration_type:
        return JSONResponse({"error": "Instance not found"}, status_code=404)

    existing_config = int_svc.decrypt_config(cfg.config_json)
    body = await request.json()
    name = str(body.get("name", "")).strip() or cfg.name
    config_dict = {}
    for field in integration_cls.config_fields:
        val = body.get(field.key)
        if val is None:
            # Keep existing value if not provided
            config_dict[field.key] = existing_config.get(field.key, "")
            continue
        if field.field_type == "password" and val == "":
            # Don't overwrite password with empty string
            config_dict[field.key] = existing_config.get(field.key, "")
        elif field.field_type == "checkbox":
            config_dict[field.key] = bool(val)
        elif field.field_type == "number":
            try:
                config_dict[field.key] = int(val) if val else (field.default if field.default is not None else 0)
            except (ValueError, TypeError):
                config_dict[field.key] = field.default if field.default is not None else 0
        else:
            config_dict[field.key] = str(val).strip()

    host_err = _validate_config_hosts(config_dict, integration_cls.config_fields)
    if host_err:
        return JSONResponse({"error": host_err}, status_code=400)
    await int_svc.update_config(db, config_id, name=name, config_dict=config_dict)
    return JSONResponse({"ok": True, "id": config_id, "name": name})


# ── JSON API: delete instance ────────────────────────────────────────────────


@router.delete("/api/integration/{integration_type}/{config_id}")
async def api_delete_instance(
    request: Request,
    integration_type: str,
    config_id: int,
    db: AsyncSession = Depends(get_db),
):
    """JSON API for deleting an integration instance."""
    if _require_editor(request):
        return JSONResponse({"error": "Read-only access"}, status_code=403)
    await int_svc.delete_config(db, config_id)
    from main import invalidate_nav_cache
    invalidate_nav_cache()
    return JSONResponse({"ok": True})


# ── Add instance ──────────────────────────────────────────────────────────────


@router.post("/integration/{integration_type}/add")
async def add_instance(
    request: Request,
    integration_type: str,
    db: AsyncSession = Depends(get_db),
):
    if _require_editor(request):
        return HTMLResponse("Read-only access", status_code=403)
    integration_cls = get_integration(integration_type)
    if not integration_cls:
        return HTMLResponse("Integration not found", status_code=404)

    form = await request.form()
    name = str(form.get("name", "")).strip()
    if not name:
        name = f"{integration_cls.display_name} Instance"

    config_dict = _parse_form_config(integration_cls, dict(form))
    host_err = _validate_config_hosts(config_dict, integration_cls.config_fields)
    if host_err:
        return HTMLResponse(f"Validation error: {host_err}", status_code=400)
    await int_svc.create_config(db, integration_type, name, config_dict)
    from main import invalidate_nav_cache
    invalidate_nav_cache()
    return RedirectResponse(
        url=f"/integration/{integration_type}?saved=1",
        status_code=303,
    )


# ── Edit instance ─────────────────────────────────────────────────────────────


@router.post("/integration/{integration_type}/{config_id}/edit")
async def edit_instance(
    request: Request,
    integration_type: str,
    config_id: int,
    db: AsyncSession = Depends(get_db),
):
    if _require_editor(request):
        return HTMLResponse("Read-only access", status_code=403)
    integration_cls = get_integration(integration_type)
    if not integration_cls:
        return HTMLResponse("Integration not found", status_code=404)

    cfg = await int_svc.get_config(db, config_id)
    if not cfg or cfg.type != integration_type:
        return HTMLResponse("Instance not found", status_code=404)

    existing_config = int_svc.decrypt_config(cfg.config_json)
    form = await request.form()
    name = str(form.get("name", "")).strip() or cfg.name
    config_dict = _parse_form_config(integration_cls, dict(form), existing_config)
    host_err = _validate_config_hosts(config_dict, integration_cls.config_fields)
    if host_err:
        return HTMLResponse(f"Validation error: {host_err}", status_code=400)
    await int_svc.update_config(db, config_id, name=name, config_dict=config_dict)
    return RedirectResponse(
        url=f"/integration/{integration_type}/{config_id}?saved=1",
        status_code=303,
    )


# ── Delete instance ───────────────────────────────────────────────────────────


@router.post("/integration/{integration_type}/{config_id}/delete")
async def delete_instance(
    request: Request,
    integration_type: str,
    config_id: int,
    db: AsyncSession = Depends(get_db),
):
    if _require_editor(request):
        return HTMLResponse("Read-only access", status_code=403)
    await int_svc.delete_config(db, config_id)
    from main import invalidate_nav_cache
    invalidate_nav_cache()
    return RedirectResponse(
        url=f"/integration/{integration_type}?saved=deleted",
        status_code=303,
    )


# ── Test connection (JSON) ────────────────────────────────────────────────────


@router.get("/integration/{integration_type}/{config_id}/test")
async def test_connection(
    integration_type: str,
    config_id: int,
    db: AsyncSession = Depends(get_db),
):
    integration_cls = get_integration(integration_type)
    if not integration_cls:
        return JSONResponse({"ok": False, "error": "Unknown integration"}, status_code=404)

    cfg = await int_svc.get_config(db, config_id)
    if not cfg or cfg.type != integration_type:
        return JSONResponse({"ok": False, "error": "Instance not found"}, status_code=404)

    config_dict = int_svc.decrypt_config(cfg.config_json)
    instance = integration_cls(config=config_dict)

    try:
        ok = await instance.health_check()
        return JSONResponse({"ok": ok})
    except Exception as exc:
        log.error("Health check failed for %s/%s: %s", integration_type, config_id, exc)
        return JSONResponse({"ok": False, "error": "Connection failed. Check server logs."})


# ── Refresh (trigger immediate re-collect) ────────────────────────────────────


@router.post("/integration/{integration_type}/{config_id}/refresh")
async def refresh_instance(
    request: Request,
    integration_type: str,
    config_id: int,
    db: AsyncSession = Depends(get_db),
):
    integration_cls = get_integration(integration_type)
    if not integration_cls:
        return HTMLResponse("Integration not found", status_code=404)

    cfg = await int_svc.get_config(db, config_id)
    if not cfg or cfg.type != integration_type:
        return HTMLResponse("Instance not found", status_code=404)

    config_dict = int_svc.decrypt_config(cfg.config_json)
    instance = integration_cls(config=config_dict)

    try:
        result = await instance.collect()
        if result.success:
            await snap_svc.save(db, integration_type, config_id, True, result.data)
        else:
            await snap_svc.save(db, integration_type, config_id, False, error=result.error)
        await db.commit()
    except Exception as exc:
        await snap_svc.save(db, integration_type, config_id, False, error=str(exc))
        await db.commit()

    return RedirectResponse(
        url=f"/integration/{integration_type}/{config_id}",
        status_code=303,
    )


# ── JSON API: latest status ───────────────────────────────────────────────────


@router.get("/api/integration/{integration_type}/{config_id}/status")
async def api_status(
    integration_type: str,
    config_id: int,
    db: AsyncSession = Depends(get_db),
):
    cfg = await int_svc.get_config(db, config_id)
    if not cfg or cfg.type != integration_type:
        return JSONResponse({"error": "not found"}, status_code=404)

    snap = await snap_svc.get_latest(db, integration_type, config_id)
    if not snap:
        return JSONResponse({"error": "no data yet"}, status_code=404)

    data = json.loads(snap.data_json) if snap.data_json else None
    return JSONResponse({
        "ok": snap.ok,
        "data": data,
        "error": snap.error,
        "timestamp": snap.timestamp.isoformat() if snap.timestamp else None,
    })


# ── JSON API: list all integrations ──────────────────────────────────────────


@router.get("/api/integrations")
async def api_list_integrations():
    """Return metadata for all registered integrations."""
    registry = get_registry()
    return JSONResponse({
        name: {
            "display_name": cls.display_name,
            "icon": cls.icon,
            "description": cls.description,
        }
        for name, cls in registry.items()
    })
