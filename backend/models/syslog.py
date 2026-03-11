"""Syslog constants + saved views model. Syslog messages are stored in ClickHouse."""
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text

from models.base import Base


# RFC 5424 severity levels
SEVERITY_LABELS = {
    0: "Emergency",
    1: "Alert",
    2: "Critical",
    3: "Error",
    4: "Warning",
    5: "Notice",
    6: "Informational",
    7: "Debug",
}

SEVERITY_COLORS = {
    0: "red",
    1: "red",
    2: "red",
    3: "orange",
    4: "yellow",
    5: "blue",
    6: "green",
    7: "gray",
}

FACILITY_LABELS = {
    0: "kern", 1: "user", 2: "mail", 3: "daemon",
    4: "auth", 5: "syslog", 6: "lpr", 7: "news",
    8: "uucp", 9: "cron", 10: "authpriv", 11: "ftp",
    16: "local0", 17: "local1", 18: "local2", 19: "local3",
    20: "local4", 21: "local5", 22: "local6", 23: "local7",
}

# Retention mirrors ClickHouse TTL (informational only)
RETENTION_DAYS = {
    7: 1,    # Debug
    6: 3,    # Informational
    5: 7,    # Notice
    4: 30,   # Warning
    3: 90,   # Error
    2: 90,   # Critical
    1: 90,   # Alert
    0: 90,   # Emergency
}


class SyslogView(Base):
    """Saved syslog filter views (stored in PostgreSQL)."""
    __tablename__ = "syslog_views"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), nullable=False)
    filters_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, default=datetime.utcnow)
