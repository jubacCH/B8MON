"""Reading a container's address out of its Proxmox config.

Discovery registered guests by name only. A name pings correctly just where
internal names resolve internally; without split-horizon DNS it resolves to the
site's public IP, the check fails on NAT hairpin, and a healthy container shows
as down. Three hosts sat red that way — one of them answered in 0.29 ms on its
real address.
"""
from integrations.proxmox import extract_ipv4

# Straight from `pct config 134` on the container that was showing as down.
REAL = "name=eth0,bridge=vmbr0,gw=10.10.30.1,hwaddr=BC:24:11:A3:B9:EE,ip=10.10.30.70/24,tag=30,type=veth"


def test_reads_the_address_from_a_real_config():
    assert extract_ipv4(REAL) == "10.10.30.70"


def test_dhcp_yields_nothing():
    """No static address to record; the hostname remains the only option."""
    assert extract_ipv4("name=eth0,bridge=vmbr0,ip=dhcp") is None


def test_missing_and_empty_configs():
    assert extract_ipv4(None) is None
    assert extract_ipv4("") is None


def test_ignores_an_address_without_a_prefix():
    """Proxmox always writes CIDR; anything else is not an address field."""
    assert extract_ipv4("name=eth0,description=ip=10.0.0.1 in docs") is None


def test_gateway_is_not_mistaken_for_the_address():
    assert extract_ipv4("name=eth0,gw=10.10.30.1,ip=10.10.30.70/24") == "10.10.30.70"


def test_ipv6_only_config_yields_nothing():
    assert extract_ipv4("name=eth0,ip6=2001:db8::1/64") is None
