"""Agent model — registered agent host configuration only.

Time-series snapshots live in ClickHouse `agent_metrics` table; query them
via `services.clickhouse_client` helpers.
"""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Index, Integer, String, Text, func

from models.base import Base


class Agent(Base):
    """A registered agent host that reports metrics."""
    __tablename__ = "agents"
    id         = Column(Integer, primary_key=True, autoincrement=True)
    name       = Column(String(128), nullable=False)
    hostname   = Column(String(256), nullable=True)
    token      = Column(String(64), nullable=False, unique=True, index=True)
    platform   = Column(String(32), nullable=True)
    arch       = Column(String(32), nullable=True)
    agent_version = Column(String(16), nullable=True)
    enabled    = Column(Boolean, default=True)
    log_levels = Column(String(32), nullable=True, default="1,2,3")
    log_channels = Column(Text, nullable=True, default="System,Application")
    log_file_paths = Column(Text, nullable=True)
    agent_log_level = Column(String(16), nullable=True, default="errors")
    pending_command = Column(String(32), nullable=True)
    # A probe is an agent that also runs checks for hosts in its network,
    # rather than only reporting on the machine it sits on.
    is_probe   = Column(Boolean, default=False, nullable=False)
    probe_interval_seconds = Column(Integer, nullable=True)
    watched_services = Column(Text, nullable=True)
    last_seen  = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_agents_hostname_lower", func.lower(hostname), unique=True),
    )
