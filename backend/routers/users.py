import bcrypt
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from templating import templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import User, get_db

router = APIRouter(prefix="/users")
api_router = APIRouter()

VALID_ROLES = {"admin", "editor", "readonly"}


@api_router.get("/api/v1/users")
async def list_users_api(request: Request, db: AsyncSession = Depends(get_db)):
    """JSON user list for the frontend (session-authenticated)."""
    user = getattr(request.state, "current_user", None)
    if not user or getattr(user, "role", None) != "admin":
        return JSONResponse({"error": "Admin access required"}, status_code=403)
    result = await db.execute(select(User).order_by(User.created_at))
    users = result.scalars().all()
    return JSONResponse([
        {
            "id": u.id,
            "username": u.username,
            "role": u.role or "admin",
            "created_at": u.created_at.isoformat() if u.created_at else None,
        }
        for u in users
    ])


@router.post("/me/password")
async def change_own_password(
    request: Request,
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """Allow any logged-in user to change their own password."""
    user = request.state.current_user
    if not user:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/login", status_code=303)
    db_user = await db.get(User, user.id)
    if db_user:
        db_user.password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        await db.commit()
    # Redirect back to referrer (validated) or dashboard
    ref = request.headers.get("referer", "/")
    if not ref.startswith("/") or ref.startswith("//"):
        ref = "/"
    return RedirectResponse(url=ref, status_code=303)


@router.get("")
async def users_page(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).order_by(User.created_at))
    users = result.scalars().all()
    return JSONResponse([
        {
            "id": u.id,
            "username": u.username,
            "role": u.role or "admin",
            "created_at": u.created_at.isoformat() if u.created_at else None,
        }
        for u in users
    ])


@router.post("/add")
async def add_user(
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form("readonly"),
    db: AsyncSession = Depends(get_db),
):
    if role not in VALID_ROLES:
        role = "readonly"
    existing = (await db.execute(select(User).where(User.username == username.strip()))).scalar_one_or_none()
    if existing:
        return RedirectResponse(url="/users?error=exists", status_code=303)
    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    db.add(User(username=username.strip(), password_hash=pw_hash, role=role))
    await db.commit()
    return RedirectResponse(url="/users?saved=1", status_code=303)


@router.post("/{user_id}/role")
async def update_role(
    user_id: int,
    role: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    if role not in VALID_ROLES:
        return RedirectResponse(url="/users?error=invalid_role", status_code=303)
    user = await db.get(User, user_id)
    if not user:
        return RedirectResponse(url="/users", status_code=303)
    if user.role == "admin" and role != "admin":
        admins = (await db.execute(select(User).where(User.role == "admin"))).scalars().all()
        if len(admins) <= 1:
            return RedirectResponse(url="/users?error=last_admin", status_code=303)
    user.role = role
    await db.commit()
    return RedirectResponse(url="/users?saved=1", status_code=303)


@router.post("/{user_id}/password")
async def reset_password(
    user_id: int,
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    user = await db.get(User, user_id)
    if not user:
        return RedirectResponse(url="/users", status_code=303)
    user.password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    await db.commit()
    return RedirectResponse(url="/users?saved=1", status_code=303)


@router.post("/{user_id}/delete")
async def delete_user(
    user_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    current = request.state.current_user
    if current and current.id == user_id:
        return RedirectResponse(url="/users?error=self_delete", status_code=303)
    user = await db.get(User, user_id)
    if not user:
        return RedirectResponse(url="/users", status_code=303)
    if user.role == "admin":
        admins = (await db.execute(select(User).where(User.role == "admin"))).scalars().all()
        if len(admins) <= 1:
            return RedirectResponse(url="/users?error=last_admin", status_code=303)
    await db.delete(user)
    await db.commit()
    return RedirectResponse(url="/users?saved=1", status_code=303)
