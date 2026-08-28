"""Parsing both speedtest tools.

speedtest-cli has been unmaintained since 2021 and reports ping = 1800000.0
against some connections. The Ookla CLI is the maintained replacement, but its
output shape differs in a way that is easy to get wrong: bandwidth is given in
*bytes* per second, while the legacy tool reported *bits*.

Both parsers are kept so an existing installation without the Ookla binary
keeps working rather than losing its speedtest on upgrade.
"""
from integrations.speedtest import parse_legacy_result, parse_ookla_result

# Trimmed from real `speedtest --format=json` output.
OOKLA = {
    "type": "result",
    "timestamp": "2026-08-28T08:19:37Z",
    "ping": {"jitter": 0.8, "latency": 18.5},
    "download": {"bandwidth": 26125000, "bytes": 300000000, "elapsed": 9000},
    "upload": {"bandwidth": 2612500, "bytes": 30000000, "elapsed": 9000},
    "isp": "Swisscom",
    "server": {"name": "Netzwerge GmbH", "location": "Hamburg", "country": "Germany"},
}

LEGACY = {
    "download": 209002567.35,
    "upload": 20530000.0,
    "ping": 18.5,
    "client": {"isp": "Swisscom"},
    "server": {"name": "Hamburg", "country": "Germany", "sponsor": "Netzwerge GmbH"},
    "timestamp": "2026-08-28T08:19:37.446939Z",
}


def test_ookla_bandwidth_is_bytes_per_second():
    """26'125'000 B/s is 209 Mbit/s. Reading it as bits would report 26."""
    result = parse_ookla_result(OOKLA)
    assert result["download_mbps"] == 209.0
    assert result["upload_mbps"] == 20.9


def test_ookla_ping_comes_from_the_nested_latency():
    assert parse_ookla_result(OOKLA)["ping_ms"] == 18.5


def test_ookla_server_and_isp():
    result = parse_ookla_result(OOKLA)
    assert result["server_name"] == "Hamburg, Germany"
    assert result["server_location"] == "Netzwerge GmbH"
    assert result["isp"] == "Swisscom"


def test_legacy_bandwidth_is_bits_per_second():
    result = parse_legacy_result(LEGACY)
    assert result["download_mbps"] == 209.0
    assert result["upload_mbps"] == 20.53


def test_legacy_broken_ping_is_still_rejected():
    """The reason for moving tools must not regress in the fallback path."""
    assert parse_legacy_result({**LEGACY, "ping": 1800000.0})["ping_ms"] is None


def test_both_parsers_produce_the_same_shape():
    assert set(parse_ookla_result(OOKLA)) == set(parse_legacy_result(LEGACY))


def test_missing_fields_do_not_raise():
    for parser in (parse_ookla_result, parse_legacy_result):
        result = parser({})
        assert result["download_mbps"] == 0.0
        assert result["ping_ms"] is None


def test_ookla_tolerates_a_missing_server_block():
    result = parse_ookla_result({**OOKLA, "server": None})
    assert result["server_name"] == ""
