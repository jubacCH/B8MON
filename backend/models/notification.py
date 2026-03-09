"""Notification history model."""
from datetime import datetime

from sqlalchemy import Column, DateTime, Index, Integer, String, Text

from models.base import Base


class NotificationLog(Base):
    __tablename__ = "notification_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow)
    channel = Column(String(32), nullable=False)     # telegram | discord | webhook | email
    title = Column(String(256), nullable=False)
    message = Column(Text, nullable=True)
    severity = Column(String(16), nullable=False, default="info")
    status = Column(String(16), nullable=False, default="sent")  # sent | failed
    error = Column(Text, nullable=True)              # error message if failed

    __table_args__ = (
        Index("ix_notif_log_ts", timestamp.desc()),
        Index("ix_notif_log_channel", "channel"),
    )
