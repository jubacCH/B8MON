"""Custom alert rule model for user-defined triggers."""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

from models.base import Base


class AlertRule(Base):
    __tablename__ = "alert_rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), nullable=False)
    enabled = Column(Boolean, default=True, nullable=False)

    # Source: what data to check
    source_type = Column(String(32), nullable=False)   # integration type, "ping", "syslog"
    source_id = Column(Integer, nullable=True)          # specific IntegrationConfig.id (null = any)

    # Condition
    field_path = Column(String(256), nullable=False)    # dot-notation path into JSON data
    operator = Column(String(24), nullable=False)       # gt, lt, eq, ne, contains, not_contains, is_true, is_false
    threshold = Column(String(256), nullable=True)      # comparison value (cast at eval time)

    # Action
    severity = Column(String(16), nullable=False, default="warning")  # critical, warning, info
    notify_channels = Column(String(256), nullable=True)  # comma-separated: "telegram,discord,email,webhook" (null = all)
    message_template = Column(Text, nullable=True)      # custom message (supports {value}, {field}, {source})

    # Cooldown
    cooldown_minutes = Column(Integer, nullable=False, default=5)
    last_triggered_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
