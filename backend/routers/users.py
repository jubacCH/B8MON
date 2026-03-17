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


def _require_admin(request: Request) -> bool:
    """Return True (= blocked) if the current user is not an admin."""
    user = getattr(request.state, "current_user", None)
    role = getattr(user, "role", "admin") or "admin"
    return role != "admin"


@api_router.get("/api/users")
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


@api_router.post("/api/users")
async def create_user_api(request: Request, db: AsyncSession = Depends(get_db)):
    """Create a new user (admin only, JSON API)."""
    current = getattr(request.state, "current_user", None)
    if not current or getattr(current, "role", None) != "admin":
        return JSONResponse({"error": "Admin access required"}, status_code=403)
    body = await request.json()
    username = (body.get("username") or "").strip()
    password = body.get("password", "")
    role = body.get("role", "readonly")
    if not username or not password:
        return JSONResponse({"error": "Username and password required"}, status_code=400)
    if role not in VALID_ROLES:
        role = "readonly"
    existing = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
    if existing:
        return JSONResponse({"error": "Username already exists"}, status_code=409)
    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    user = User(username=username, password_hash=pw_hash, role=role)
    db.add(user)
    await db.commit()
    return JSONResponse({"ok": True, "id": user.id, "username": user.username, "role": user.role})


@api_router.delete("/api/users/{user_id}")
async def delete_user_api(user_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    """Delete a user (admin only, JSON API)."""
    current = getattr(request.state, "current_user", None)
    if not current or getattr(current, "role", None) != "admin":
        return JSONResponse({"error": "Admin access required"}, status_code=403)
    if current.id == user_id:
        return JSONResponse({"error": "Cannot delete yourself"}, status_code=400)
    user = await db.get(User, user_id)
    if not user:
        return JSONResponse({"error": "User not found"}, status_code=404)
    if user.role == "admin":
        admins = (await db.execute(select(User).where(User.role == "admin"))).scalars().all()
        if len(admins) <= 1:
            return JSONResponse({"error": "Cannot delete the last admin"}, status_code=400)
    await db.delete(user)
    await db.commit()
    return JSONResponse({"ok": True})


@api_router.patch("/api/users/{user_id}")
async def update_user_api(user_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    """Update user role or password (admin only, JSON API)."""
    current = getattr(request.state, "current_user", None)
    if not current or getattr(current, "role", None) != "admin":
        return JSONResponse({"error": "Admin access required"}, status_code=403)
    user = await db.get(User, user_id)
    if not user:
        return JSONResponse({"error": "User not found"}, status_code=404)
    body = await request.json()
    if "role" in body:
        new_role = body["role"]
        if new_role not in VALID_ROLES:
            return JSONResponse({"error": "Invalid role"}, status_code=400)
        if user.role == "admin" and new_role != "admin":
            admins = (await db.execute(select(User).where(User.role == "admin"))).scalars().all()
            if len(admins) <= 1:
                return JSONResponse({"error": "Cannot demote the last admin"}, status_code=400)
        user.role = new_role
    if "password" in body and body["password"]:
        user.password_hash = bcrypt.hashpw(body["password"].encode(), bcrypt.gensalt()).decode()
    await db.commit()
    return JSONResponse({"ok": True})


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
    if _require_admin(request):
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


@router.post("/add")
async def add_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form("readonly"),
    db: AsyncSession = Depends(get_db),
):
    if _require_admin(request):
        return JSONResponse({"error": "Admin access required"}, status_code=403)
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
    request: Request,
    user_id: int,
    role: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    if _require_admin(request):
        return JSONResponse({"error": "Admin access required"}, status_code=403)
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
    request: Request,
    user_id: int,
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    if _require_admin(request):
        return JSONResponse({"error": "Admin access required"}, status_code=403)
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
    if _require_admin(request):
        return JSONResponse({"error": "Admin access required"}, status_code=403)
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
