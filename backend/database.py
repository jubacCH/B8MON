import base64
import hashlib
import json
from datetime import datetime
from typing import AsyncGenerator

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
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    hostname = Column(String, nullable=False)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    results = relationship("PingResult", back_populates="host", cascade="all, delete-orphan")


class PingResult(Base):
    __tablename__ = "ping_results"
    id = Column(Integer, primary_key=True, autoincrement=True)
    host_id = Column(Integer, ForeignKey("ping_hosts.id"), nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    success = Column(Boolean, nullable=False)
    latency_ms = Column(Float, nullable=True)
    host = relationship("PingHost", back_populates="results")


class ProxmoxCluster(Base):
    __tablename__ = "proxmox_clusters"
    id           = Column(Integer, primary_key=True, autoincrement=True)
    name         = Column(String, nullable=False)
    host         = Column(String, nullable=False)   # e.g. https://proxmox.local:8006
    verify_ssl   = Column(Boolean, default=False)
    token_id     = Column(String, nullable=False)   # user@realm!tokenid
    token_secret = Column(String, nullable=False)   # stored encrypted
    created_at   = Column(DateTime, default=datetime.utcnow)


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


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
