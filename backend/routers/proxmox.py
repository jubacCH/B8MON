from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from collectors.proxmox import ProxmoxAPI, parse_cluster_data
from database import ProxmoxCluster, decrypt_value, encrypt_value, get_db

router = APIRouter(prefix="/proxmox")
templates = Jinja2Templates(directory="templates")


# ── API (JSON) — before /{cluster_id} ────────────────────────────────────────

@router.get("/api/{cluster_id}/status")
async def api_cluster_status(cluster_id: int, db: AsyncSession = Depends(get_db)):
    cluster = await db.get(ProxmoxCluster, cluster_id)
    if not cluster:
        return {"error": "Cluster not found"}
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
    except Exception as e:
        return {"ok": False, "error": str(e)}


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

    return templates.TemplateResponse("proxmox_detail.html", {
        "request": request,
        "cluster": cluster,
        "data": data,
        "error": error,
        "active_page": "proxmox",
    })


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
