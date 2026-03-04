"""
Collector registry – add new integrations here.

To add a new integration:
1. Create collectors/<name>.py with a BaseCollector subclass
2. Import and add it to COLLECTORS below
"""
from collectors.ping import PingCollector
from collectors.proxmox import ProxmoxCollector

COLLECTORS: dict = {
    "ping": PingCollector,
    "proxmox": ProxmoxCollector,
    # "unifi": UnifiCollector,   # coming soon
}
