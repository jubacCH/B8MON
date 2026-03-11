"""
Models package – re-exports all models and helpers for easy importing.

Usage:
    from models import Base, IntegrationConfig, Snapshot, PingHost, ...
    from models import get_setting, set_setting, encrypt_value, decrypt_value
"""
from models.base import (
    Base,
    AsyncSessionLocal,
    engine,
    encrypt_value,
    decrypt_value,
    get_db,
)
from models.settings import (
    Setting,
    User,
    Session,
    get_current_user,
    get_setting,
    set_setting,
    is_setup_complete,
)
from models.ping import PingHost, PingResult
from models.integration import IntegrationConfig, Snapshot
from models.syslog import SyslogView
from models.incident import Incident, IncidentEvent
from models.log_template import LogTemplate, HostBaseline, PrecursorPattern
from models.agent import Agent, AgentSnapshot
from models.scanner import SubnetScanSchedule, SubnetScanLog
from models.credential import Credential
from models.snmp import SnmpMib, SnmpOid, SnmpHostConfig, SnmpResult
from models.api_key import ApiKey
from models.notification import NotificationLog
from models.alert_rule import AlertRule


async def init_db():
    """Create all tables and PostgreSQL-specific objects (triggers, etc.)."""
    from sqlalchemy import text
    from config import DATABASE_URL

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Syslog messages are stored in ClickHouse — no PostgreSQL trigger needed


__all__ = [
    "Base", "AsyncSessionLocal", "engine",
    "encrypt_value", "decrypt_value", "get_db",
    "Setting", "User", "Session",
    "get_current_user", "get_setting", "set_setting", "is_setup_complete",
    "PingHost", "PingResult",
    "IntegrationConfig", "Snapshot",
    "SyslogView",
    "Incident", "IncidentEvent",
    "LogTemplate", "HostBaseline", "PrecursorPattern",
    "Agent", "AgentSnapshot",
    "SubnetScanSchedule", "SubnetScanLog",
    "Credential",
    "SnmpMib", "SnmpOid", "SnmpHostConfig", "SnmpResult",
    "ApiKey",
    "NotificationLog",
    "AlertRule",
    "init_db",
]
