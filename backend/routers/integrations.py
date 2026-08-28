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

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select as sa_select

import ipaddress
import re
from urllib.parse import urlparse

from integrations import get_registry, get_integration
from integrations._base import BaseIntegration
from models.api_key import ApiKey
from models.base import get_db
from ratelimit import rate_limit
from routers.api_v1 import require_admin
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
        # Block cloud provider metadata IPs
        _metadata_ips = (
            ipaddress.ip_address("169.254.169.254"),  # AWS / GCP metadata
            ipaddress.ip_address("168.63.129.16"),     # Azure wireserver
            ipaddress.ip_address("100.100.100.200"),   # Alibaba Cloud metadata
        )
        if addr in _metadata_ips:
            return "Cloud metadata endpoints are not allowed"
    except ValueError:
        # It's a hostname — block metadata hostnames
        _metadata_hosts = (
            "metadata.google.internal",
            "instance-data",
            "metadata.internal",
            "kubernetes.default.svc",
        )
        if host.lower() in _metadata_hosts:
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

    # Not declared per class (see api_config_fields), so extract it here too.
    # Empty means "use the integration's default", which effective_interval
    # resolves — storing 0 or "" would be indistinguishable from an explicit
    # choice, so the key is simply omitted.
    raw_interval = form.get("poll_interval_seconds", "")
    if isinstance(raw_interval, str):
        raw_interval = raw_interval.strip()
    if raw_interval not in ("", None):
        try:
            parsed = int(raw_interval)
            if parsed > 0:
                config["poll_interval_seconds"] = parsed
        except (TypeError, ValueError):
            pass  # leave unset; the default applies
    return config



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

    # Offered on every integration rather than declared in each class: how often
    # to poll is a property of the deployment, not of the integration. The
    # placeholder shows what it would do if left empty.
    default_interval = getattr(integration_cls, "default_interval_seconds", 60)
    fields.append({
        "key": "poll_interval_seconds",
        "label": "Poll interval (seconds)",
        "field_type": "number",
        "placeholder": f"Default: {default_interval}",
        "required": False,
        "default": "",
        "options": None,
    })
    return JSONResponse({
        "type": integration_type,
        "display_name": integration_cls.display_name,
        "description": integration_cls.description,
        "fields": fields,
    })


# ── JSON API: create instance ────────────────────────────────────────────────


@router.post("/api/integration/{integration_type}/create")
@rate_limit(max_requests=10, window_seconds=60)
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
    cluster_group = str(body.get("cluster_group", "")).strip() or None
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
    cfg = await int_svc.create_config(
        db, integration_type, name, config_dict, cluster_group=cluster_group,
    )
    from main import invalidate_nav_cache
    invalidate_nav_cache()
    return JSONResponse({"ok": True, "id": cfg.id, "name": cfg.name})


# ── JSON API: edit instance ──────────────────────────────────────────────────


@router.patch("/api/integration/{integration_type}/{config_id}")
@rate_limit(max_requests=10, window_seconds=60)
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

    # Tri-state: only thread cluster_group when the body explicitly contains
    # the key. Empty string or null clear it; missing key leaves it untouched.
    update_kwargs: dict = {"name": name, "config_dict": config_dict}
    if "cluster_group" in body:
        update_kwargs["cluster_group"] = (str(body.get("cluster_group") or "").strip() or None)

    await int_svc.update_config(db, config_id, **update_kwargs)
    return JSONResponse({"ok": True, "id": config_id, "name": name})


# ── JSON API: delete instance ────────────────────────────────────────────────


@router.delete("/api/integration/{integration_type}/{config_id}")
@rate_limit(max_requests=10, window_seconds=60)
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
async def api_list_integrations(db: AsyncSession = Depends(get_db)):
    """Return metadata for all registered integrations with config counts."""
    registry = get_registry()
    from services.integration import count_all_by_type
    counts = await count_all_by_type(db)
    return JSONResponse([
        {
            "name": name,
            "display_name": cls.display_name,
            "icon": cls.icon,
            "icon_svg": cls.icon_svg or "",
            "color": cls.color,
            "description": cls.description,
            "single_instance": cls.single_instance,
            "configured": counts.get(name, 0),
        }
        for name, cls in sorted(registry.items(), key=lambda x: x[1].display_name)
    ])


# ── Proxmox: Deploy Syslog Config ───────────────────────────────────────────


@router.post("/api/v1/integrations/proxmox/{config_id}/deploy-agent")
async def deploy_agent_to_lxcs(
    config_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _key: ApiKey = Depends(require_admin),
):
    """Deploy Nodeglow agent to all running LXCs via SSH → pct exec.

    The agent auto-enrolls, collects system metrics, system logs (journalctl),
    and Docker container logs — then reports everything to Nodeglow.
    """
    import asyncio
    import socket
    import tempfile
    import os
    from database import get_setting
    from integrations.proxmox import ProxmoxAPI
    from models.integration import IntegrationConfig
    import services.integration as int_svc

    cfg = await db.get(IntegrationConfig, config_id)
    if not cfg or cfg.type != "proxmox":
        return JSONResponse({"error": "Proxmox config not found"}, status_code=404)

    config_dict = int_svc.decrypt_config(cfg.config_json)

    api = ProxmoxAPI(
        host=config_dict["host"],
        token_id=config_dict["token_id"],
        token_secret=config_dict["token_secret"],
        verify_ssl=config_dict.get("verify_ssl", True),
    )

    resources = await api.cluster_resources()

    # Detect Nodeglow's LXC IP — find the PingHost for this Nodeglow instance
    # (Docker-internal IPs like 172.18.x.x are NOT reachable from LXCs)
    from models.ping import PingHost
    nodeglow_ip = await get_setting(db, "nodeglow_ip", "")
    if not nodeglow_ip:
        # Find by source_detail matching this Proxmox config, hostname containing "monitoring" or "nodeglow"
        ph_result = await db.execute(
            sa_select(PingHost).where(
                PingHost.source == "proxmox",
                PingHost.hostname.ilike("%monitoring%"),
            )
        )
        ph = ph_result.scalar_one_or_none()
        if ph:
            # Resolve hostname to IP
            try:
                nodeglow_ip = socket.gethostbyname(ph.hostname)
            except Exception:
                nodeglow_ip = ph.hostname
        if not nodeglow_ip:
            nodeglow_ip = "10.10.30.52"

    nodeglow_url = f"http://{nodeglow_ip}:8000"

    # Exclude the Nodeglow LXC itself
    _self_names = set()
    try:
        resolved = socket.getfqdn(nodeglow_ip)
        _self_names.add(resolved.lower())
        try:
            rev = socket.gethostbyaddr(nodeglow_ip)[0]
            _self_names.add(rev.lower())
        except Exception:
            pass
    except Exception:
        pass

    lxcs = []
    skipped_self = None
    for r in resources:
        if r.get("type") != "lxc" or r.get("status") != "running":
            continue
        name = (r.get("name") or "").lower()
        if name and name in _self_names:
            skipped_self = r.get("name", f"ct-{r.get('vmid')}")
            continue
        lxcs.append(r)

    if not lxcs:
        return JSONResponse({"ok": True, "results": [], "deployed": 0, "failed": 0,
                             "message": "No running LXCs found",
                             "manual_script": None, "nodeglow_url": nodeglow_url})

    # Install command: download + run install script from Nodeglow
    install_cmd = f"curl -sSL {nodeglow_url}/install/linux 2>/dev/null | bash"

    ssh_key = config_dict.get("ssh_private_key", "").strip()
    ssh_user = config_dict.get("ssh_user", "root").strip() or "root"
    proxmox_host = config_dict["host"].split("://")[-1].split(":")[0].split("/")[0]

    # ── SSH deploy (automatic) ───────────────────────────────────────────
    if ssh_key:
        results = []
        deployed = 0
        failed = 0

        key_file = tempfile.NamedTemporaryFile(mode="w", suffix=".key", delete=False)
        key_file.write(ssh_key if ssh_key.endswith("\n") else ssh_key + "\n")
        key_file.close()
        os.chmod(key_file.name, 0o600)

        try:
            for lxc in lxcs:
                vmid = lxc.get("vmid")
                name = lxc.get("name", f"ct-{vmid}")
                cmd = f'pct exec {vmid} -- bash -c "{install_cmd}"'

                try:
                    proc = await asyncio.create_subprocess_exec(
                        "ssh", "-i", key_file.name,
                        "-o", "StrictHostKeyChecking=no",
                        "-o", "ConnectTimeout=10",
                        "-o", "IdentitiesOnly=yes",
                        f"{ssh_user}@{proxmox_host}",
                        cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
                    output = stdout.decode().strip()
                    if proc.returncode == 0:
                        results.append({"vmid": vmid, "name": name, "status": "ok",
                                        "detail": output[-200:] if len(output) > 200 else output})
                        deployed += 1
                    else:
                        err = stderr.decode().strip() or output or f"exit code {proc.returncode}"
                        results.append({"vmid": vmid, "name": name, "status": "failed",
                                        "error": err[-200:]})
                        failed += 1
                except asyncio.TimeoutError:
                    results.append({"vmid": vmid, "name": name, "status": "failed",
                                    "error": "Timeout (60s)"})
                    failed += 1
                except Exception as exc:
                    results.append({"vmid": vmid, "name": name, "status": "failed",
                                    "error": str(exc)})
                    failed += 1
        finally:
            os.unlink(key_file.name)

        return JSONResponse({
            "ok": True,
            "mode": "ssh",
            "deployed": deployed,
            "failed": failed,
            "results": results,
            "manual_script": None,
            "nodeglow_url": nodeglow_url,
            "skipped_self": skipped_self,
        })

    # ── No SSH → generate script ─────────────────────────────────────────
    vmids = " ".join(str(lxc.get("vmid")) for lxc in lxcs)
    results = [
        {"vmid": lxc.get("vmid"), "name": lxc.get("name", f"ct-{lxc.get('vmid')}")}
        for lxc in lxcs
    ]

    manual_script = (
        f'# Run on your Proxmox node shell\n'
        f'# Installs Nodeglow agent on all running LXCs\n'
        f'NODEGLOW="{nodeglow_url}"\n'
        f'\n'
        f'for VMID in {vmids}; do\n'
        f'  echo "=== Installing agent on CT $VMID ==="\n'
        f'  pct exec $VMID -- bash -c "curl -sSL $NODEGLOW/install/linux | bash"\n'
        f'  echo "  Done"\n'
        f'done\n'
        f'echo "\\nAll done! Agents will auto-enroll and appear in Nodeglow within 30 seconds."'
    )

    return JSONResponse({
        "ok": True,
        "mode": "script",
        "deployed": 0,
        "failed": 0,
        "results": results,
        "manual_script": manual_script,
        "nodeglow_url": nodeglow_url,
        "skipped_self": skipped_self,
    })
