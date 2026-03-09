"""Scheduled subnet scan model."""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

from models.base import Base


class SubnetScanSchedule(Base):
    __tablename__ = "subnet_scan_schedules"
    id          = Column(Integer, primary_key=True, autoincrement=True)
    name        = Column(String(128), nullable=False)
    cidr        = Column(String(64), nullable=False)
    interval_m  = Column(Integer, default=60)           # scan interval in minutes
    auto_add    = Column(Boolean, default=True)          # auto-add discovered hosts
    enabled     = Column(Boolean, default=True)
    last_scan   = Column(DateTime, nullable=True)
    last_alive  = Column(Integer, nullable=True)         # alive count from last scan
    last_total  = Column(Integer, nullable=True)         # total count from last scan
    last_added  = Column(Integer, default=0)             # hosts added in last scan
    created_at  = Column(DateTime, default=datetime.utcnow)
