"""The platform's address for a guest wins over anything DNS returns.

Recording the address was not enough on its own. A separate DNS pass also sets
``ip_address``, both paths guarded on the field being empty, and whichever ran
first won. For guests whose names resolve outside the LAN, DNS won and stored
an address belonging to a different machine:

    enshrouded.b8n.ch  → 178.192.27.12   (the site's WAN address)
    castaway.b8n.ch    → 178.192.27.12
    menue.b8n.ch       → 188.114.97.12   (a Cloudflare edge)

The first two read as down while answering in 0.2 ms internally. The third read
as *up* — the CDN edge answers ICMP whether or not the container behind it is
running, so the check could never have gone red. A monitoring product showing a
false green is the worse half of this bug, and nothing in the product would have
revealed it.

These tests pin both halves of the fix: discovery overwrites, and DNS keeps its
hands off hosts whose address the platform already knows.
"""
import pytest

from integrations.proxmox import import_proxmox_hosts
from models.ping import PingHost
from scheduler import AUTHORITATIVE_ADDRESS_SOURCES

# The address public DNS returns for these names: the site's WAN interface,
# which does not answer ICMP from inside the LAN.
WAN = "178.192.27.12"
# What Proxmox reports for the same guest.
INTERNAL = "10.10.30.70"


def _cluster(ip=INTERNAL, vmid=134, name="enshrouded.b8n.ch"):
    return {
        "cluster_name": "prxmxcl01",
        "nodes": [],
        "vms": [],
        "containers": [
            {"id": vmid, "name": name, "ip": ip, "running": True, "status": "running"},
        ],
    }


async def test_guest_config_overwrites_an_address_dns_supplied(db):
    """The exact production row, and the fix that corrects it."""
    db.add(PingHost(
        name="enshrouded.b8n.ch", hostname="enshrouded.b8n.ch",
        ip_address=WAN, check_type="icmp", enabled=True,
        source="proxmox", source_detail="prxmxcl01:134",
    ))
    await db.commit()

    await import_proxmox_hosts("prxmxcl01", _cluster(), db)

    host = (await db.execute(
        PingHost.__table__.select().where(PingHost.name == "enshrouded.b8n.ch")
    )).first()
    assert host.ip_address == INTERNAL


async def test_a_new_guest_is_registered_with_its_address(db):
    await import_proxmox_hosts("prxmxcl01", _cluster(), db)

    host = (await db.execute(
        PingHost.__table__.select().where(PingHost.name == "enshrouded.b8n.ch")
    )).first()
    assert host.ip_address == INTERNAL


async def test_a_guest_without_a_static_address_keeps_what_it_had(db):
    """DHCP guests report no address; overwriting with nothing would be a loss."""
    db.add(PingHost(
        name="enshrouded.b8n.ch", hostname="enshrouded.b8n.ch",
        ip_address="10.10.30.99", check_type="icmp", enabled=True,
        source="proxmox", source_detail="prxmxcl01:134",
    ))
    await db.commit()

    await import_proxmox_hosts("prxmxcl01", _cluster(ip=None), db)

    host = (await db.execute(
        PingHost.__table__.select().where(PingHost.name == "enshrouded.b8n.ch")
    )).first()
    assert host.ip_address == "10.10.30.99"


async def test_a_manual_host_that_is_really_a_guest_is_adopted(db):
    """Matching by name means it *is* that guest, so it gets the guest's address."""
    db.add(PingHost(
        name="enshrouded.b8n.ch", hostname="enshrouded.b8n.ch",
        ip_address=WAN, check_type="icmp", enabled=True,
        source="manual", source_detail=None,
    ))
    await db.commit()

    await import_proxmox_hosts("prxmxcl01", _cluster(), db)

    host = (await db.execute(
        PingHost.__table__.select().where(PingHost.name == "enshrouded.b8n.ch")
    )).first()
    assert host.source == "proxmox"
    assert host.ip_address == INTERNAL


async def test_a_manual_host_matching_no_guest_is_left_alone(db):
    """google.ch is deliberately a public address and must stay one."""
    db.add(PingHost(
        name="google.ch", hostname="google.ch",
        ip_address="74.125.29.94", check_type="icmp", enabled=True,
        source="manual", source_detail=None,
    ))
    await db.commit()

    await import_proxmox_hosts("prxmxcl01", _cluster(), db)

    host = (await db.execute(
        PingHost.__table__.select().where(PingHost.name == "google.ch")
    )).first()
    assert host.source == "manual"
    assert host.ip_address == "74.125.29.94"


@pytest.mark.parametrize("source", ["proxmox", "unifi"])
def test_platform_sourced_hosts_are_off_limits_to_dns(source):
    """DNS must not supply an address where the platform reports one."""
    assert source in AUTHORITATIVE_ADDRESS_SOURCES


@pytest.mark.parametrize("source", ["manual", "scanner", None])
def test_dns_remains_the_fallback_everywhere_else(source):
    """Hosts with no authoritative source still depend on this resolution."""
    assert source not in AUTHORITATIVE_ADDRESS_SOURCES
