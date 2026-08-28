"""PingHost model — host configuration only.

Time-series ping check results live in ClickHouse `ping_checks` table; query
them via `services.clickhouse_client` helpers.
"""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text

from models.base import Base


class PingHost(Base):
    __tablename__ = "ping_hosts"
    id                   = Column(Integer, primary_key=True, autoincrement=True)
    name                 = Column(String, nullable=False)
    hostname             = Column(String, nullable=False)
    enabled              = Column(Boolean, default=True)
    check_type           = Column(String, default="icmp")   # icmp | http | https | tcp
    port                 = Column(Integer, nullable=True)
    latency_threshold_ms = Column(Float, nullable=True)
    maintenance          = Column(Boolean, default=False)
    maintenance_until    = Column(DateTime, nullable=True)
    ssl_expiry_days      = Column(Integer, nullable=True)
    port_error           = Column(Boolean, default=False)
    check_detail         = Column(Text, nullable=True)
    ip_address           = Column(String, nullable=True)
    source               = Column(String, default="manual", index=True)
    source_detail        = Column(String, nullable=True)
    mac_address          = Column(String, nullable=True)
    parent_id            = Column(Integer, ForeignKey("ping_hosts.id"), nullable=True)
    # Which probe checks this host. NULL means the core does, which is every
    # existing row — the feature stays inert until a host is assigned.
    probe_id             = Column(Integer, ForeignKey("agents.id", ondelete="SET NULL"),
                                  nullable=True, index=True)
    created_at           = Column(DateTime, default=datetime.utcnow)
