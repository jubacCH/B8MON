"""Incident and IncidentEvent models for the correlation engine."""
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import relationship

from models.base import Base


class Incident(Base):
    __tablename__ = "incidents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    rule = Column(String(64), nullable=False)            # rule that created it
    title = Column(String(256), nullable=False)
    severity = Column(String(16), nullable=False, default="warning")  # critical | warning | info
    status = Column(String(16), nullable=False, default="open")       # open | acknowledged | resolved
    host_ids_hash = Column(String(64), nullable=True)    # for dedup: hash of sorted affected host IDs
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)
    acknowledged_by = Column(String(128), nullable=True)

    events = relationship("IncidentEvent", back_populates="incident",
                          cascade="all, delete-orphan", order_by="IncidentEvent.timestamp")

    __table_args__ = (
        Index("ix_incident_status", "status"),
        Index("ix_incident_rule_hash", "rule", "host_ids_hash"),
        Index("ix_incident_updated", "updated_at"),
    )


class IncidentEvent(Base):
    __tablename__ = "incident_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    incident_id = Column(Integer, ForeignKey("incidents.id"), nullable=False)
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow)
    event_type = Column(String(32), nullable=False)  # created | host_down | host_up | syslog_error | integration_error | resolved | acknowledged
    summary = Column(Text, nullable=False)
    detail = Column(Text, nullable=True)              # extra JSON or text

    incident = relationship("Incident", back_populates="events")

    __table_args__ = (
        Index("ix_incident_event_ts", "incident_id", timestamp.desc()),
    )
