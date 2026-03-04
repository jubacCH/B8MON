from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ConfigField:
    """Definition of a single configuration field shown in the UI."""
    key: str
    label: str
    field_type: str = "text"   # text | password | number | checkbox | url
    placeholder: str = ""
    required: bool = True
    encrypted: bool = False    # store encrypted in DB
    default: Any = None


@dataclass
class CollectorResult:
    success: bool
    data: Any = None
    error: Optional[str] = None


class BaseCollector(ABC):
    """
    Base class for all integrations.

    To add a new integration:
    1. Create collectors/<name>.py and subclass BaseCollector
    2. Add it to collectors/registry.py
    """
    name: str = ""           # e.g. "proxmox"
    display_name: str = ""   # e.g. "Proxmox VE"
    description: str = ""
    icon: str = "🔌"

    def __init__(self, settings: dict[str, Any]):
        self.settings = settings

    @classmethod
    @abstractmethod
    def get_config_fields(cls) -> list[ConfigField]:
        """Return the list of config fields this integration needs."""

    @abstractmethod
    async def collect(self) -> CollectorResult:
        """Collect metrics / status and return structured data."""

    @abstractmethod
    async def health_check(self) -> bool:
        """Quick connectivity test (used by 'Test Connection' button)."""
