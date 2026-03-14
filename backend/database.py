"""
Database module – core models, session factory, and utility functions.

Models:
  - Setting, User, Session, PingHost, PingResult (defined here)
  - IntegrationConfig, Snapshot (in models/integration.py)
  - SyslogMessage (in models/syslog.py)
  - Incident, IncidentEvent (in models/incident.py)
"""
import base64
import hashlib
import json
from datetime import datetime
from typing import TYPE_CHECKING, AsyncGenerator

if TYPE_CHECKING:
    from fastapi import Request

from cryptography.fernet import Fernet
from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey,
    Integer, String, Text, select, text
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, relationship

from config import DATABASE_URL, SECRET_KEY


engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Setting(Base):
    __tablename__ = "settings"
    key = Column(String, primary_key=True)
    value = Column(Text, nullable=True)
    encrypted = Column(Boolean, default=False)


class PingHost(Base):
    __tablename__ = "ping_hosts"
    id                   = Column(Integer, primary_key=True, autoincrement=True)
    name                 = Column(String, nullable=False)
    hostname             = Column(String, nullable=False)
    enabled              = Column(Boolean, default=True)
    check_type           = Column(String, default="icmp")
    port                 = Column(Integer, nullable=True)
    latency_threshold_ms = Column(Float, nullable=True)
    maintenance          = Column(Boolean, default=False)
    maintenance_until    = Column(DateTime, nullable=True)
    ssl_expiry_days      = Column(Integer, nullable=True)
    port_error           = Column(Boolean, default=False)
    check_detail         = Column(Text, nullable=True)
    source               = Column(String, default="manual")
    source_detail        = Column(String, nullable=True)
    mac_address          = Column(String, nullable=True)
    parent_id            = Column(Integer, ForeignKey("ping_hosts.id"), nullable=True)
    created_at           = Column(DateTime, default=datetime.utcnow)
    results = relationship("PingResult", back_populates="host", cascade="all, delete-orphan")
    children = relationship("PingHost", foreign_keys="PingHost.parent_id", viewonly=True)


class PingResult(Base):
    __tablename__ = "ping_results"
    id = Column(Integer, primary_key=True, autoincrement=True)
    host_id = Column(Integer, ForeignKey("ping_hosts.id"), nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    success = Column(Boolean, nullable=False)
    latency_ms = Column(Float, nullable=True)
    host = relationship("PingHost", back_populates="results")


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(64), unique=True, nullable=False)
    password_hash = Column(String(128), nullable=False)
    role = Column(String(16), default="admin")
    created_at = Column(DateTime, default=datetime.utcnow)


class Session(Base):
    __tablename__ = "sessions"
    token = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    expires_at = Column(DateTime, nullable=False)


async def get_current_user(request: "Request", db: AsyncSession) -> "User | None":
    token = request.cookies.get("nodeglow_session")
    if not token:
        return None
    now = datetime.utcnow()
    result = await db.execute(
        select(Session).where(Session.token == token, Session.expires_at > now)
    )
    session = result.scalar_one_or_none()
    if not session:
        return None
    result = await db.execute(select(User).where(User.id == session.user_id))
    return result.scalar_one_or_none()


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        try:
            await conn.execute(text("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'admin'"))
        except Exception:
            pass
        migrations = [
            ("check_type",           "TEXT DEFAULT 'icmp'"),
            ("port",                 "INTEGER"),
            ("latency_threshold_ms", "REAL"),
            ("maintenance",          "INTEGER DEFAULT 0"),
            ("maintenance_until",    "TIMESTAMP"),
            ("ssl_expiry_days",      "INTEGER"),
            ("source",               "TEXT DEFAULT 'manual'"),
            ("source_detail",        "TEXT"),
            ("mac_address",          "TEXT"),
            ("parent_id",            "INTEGER REFERENCES ping_hosts(id)"),
            ("port_error",           "BOOLEAN DEFAULT FALSE"),
            ("check_detail",         "TEXT"),
        ]
        for col, definition in migrations:
            try:
                await conn.execute(text(f"ALTER TABLE ping_hosts ADD COLUMN {col} {definition}"))
            except Exception:
                pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


# ── Settings helpers ──────────────────────────────────────────────────────────

def _fernet() -> Fernet:
    key = hashlib.sha256(SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_value(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_value(value: str) -> str:
    return _fernet().decrypt(value.encode()).decode()


async def get_setting(db: AsyncSession, key: str, default=None):
    row = await db.get(Setting, key)
    if row is None:
        return default
    if row.encrypted and row.value:
        return decrypt_value(row.value)
    return row.value


async def set_setting(db: AsyncSession, key: str, value: str, encrypted: bool = False):
    row = await db.get(Setting, key)
    stored = encrypt_value(value) if encrypted else value
    if row:
        row.value = stored
        row.encrypted = encrypted
    else:
        db.add(Setting(key=key, value=stored, encrypted=encrypted))
    await db.commit()


async def is_setup_complete(db: AsyncSession) -> bool:
    val = await get_setting(db, "setup_complete")
    return val == "true"
