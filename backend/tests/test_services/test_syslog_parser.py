"""Tests for syslog message parsing (RFC 3164, 5424, fallbacks)."""
import pytest
from services.syslog import parse_syslog


# ── RFC 3164 (BSD syslog) ─────────────────────────────────────────────────────

def test_rfc3164_basic():
    raw = "<14>Mar  7 10:30:15 myhost sshd[1234]: Failed password for root"
    result = parse_syslog(raw, "10.0.0.1")
    assert result is not None
    assert result["hostname"] == "myhost"
    assert result["app_name"] == "sshd"
    assert result["message"] == "Failed password for root"
    assert result["facility"] == 1   # user
    assert result["severity"] == 6   # informational
    assert result["source_ip"] == "10.0.0.1"


def test_rfc3164_no_pid():
    raw = "<38>Mar  7 12:00:00 router kernel: eth0: link down"
    result = parse_syslog(raw, "192.168.1.1")
    assert result["hostname"] == "router"
    assert result["app_name"] == "kernel"
    assert result["message"] == "eth0: link down"
    assert result["facility"] == 4   # auth
    assert result["severity"] == 6


def test_rfc3164_dual_timestamp_unifi():
    """UniFi devices send dual timestamps: BSD + ISO before hostname."""
    raw = "<4>Mar  7 07:26:58 2026-03-07T07:26:58.42112 UCG-Fiber CEF:0|Ubiquiti|USG|1.0|..."
    result = parse_syslog(raw, "10.10.30.1")
    assert result["hostname"] == "UCG-Fiber"
    assert result["app_name"] == "CEF"
    assert result["severity"] == 4   # warning


def test_rfc3164_single_digit_day():
    raw = "<34>Mar  5 01:02:03 server1 crond[99]: job started"
    result = parse_syslog(raw, "10.0.0.2")
    assert result["hostname"] == "server1"
    assert result["app_name"] == "crond"


# ── RFC 5424 ──────────────────────────────────────────────────────────────────

def test_rfc5424_basic():
    raw = '<165>1 2026-03-07T10:30:15.123Z myhost myapp 1234 ID47 - Hello world'
    result = parse_syslog(raw, "10.0.0.1")
    assert result is not None
    assert result["hostname"] == "myhost"
    assert result["app_name"] == "myapp"
    # Nil SD "-" is included in message (parser doesn't strip it)
    assert "Hello world" in result["message"]
    assert result["facility"] == 20  # local4
    assert result["severity"] == 5   # notice


def test_rfc5424_nil_values():
    raw = '<13>1 2026-03-07T10:30:15Z - - - - - Just a message'
    result = parse_syslog(raw, "10.0.0.1")
    assert result["hostname"] is None
    assert result["app_name"] is None
    assert "Just a message" in result["message"]


def test_rfc5424_with_structured_data():
    raw = '<134>1 2026-03-07T08:00:00.000Z fw1 filterlog 123 - [meta key="val"] blocked packet'
    result = parse_syslog(raw, "10.0.0.1")
    assert result["hostname"] == "fw1"
    assert result["app_name"] == "filterlog"
    assert "blocked packet" in result["message"]


def test_rfc5424_timezone_offset():
    raw = '<14>1 2026-03-07T10:30:15+01:00 host1 app - - - msg with tz offset'
    result = parse_syslog(raw, "10.0.0.1")
    assert result["hostname"] == "host1"


# ── Fallback parsing ─────────────────────────────────────────────────────────

def test_pri_only_fallback():
    raw = "<42>some random message without structure"
    result = parse_syslog(raw, "10.0.0.1")
    assert result["message"] == "some random message without structure"
    assert result["facility"] == 5   # syslog
    assert result["severity"] == 2   # critical


def test_no_pri_fallback():
    raw = "Just a plain text message"
    result = parse_syslog(raw, "10.0.0.1")
    assert result["message"] == "Just a plain text message"
    assert result["severity"] == 6   # informational default
    assert result["hostname"] is None


def test_empty_message():
    result = parse_syslog("", "10.0.0.1")
    assert result is None


def test_whitespace_only():
    result = parse_syslog("   \n  ", "10.0.0.1")
    assert result is None


# ── PRI computation ───────────────────────────────────────────────────────────

def test_emergency_severity():
    raw = "<0>Mar  7 10:00:00 host1 kernel: panic"
    result = parse_syslog(raw, "10.0.0.1")
    assert result["severity"] == 0   # emergency
    assert result["facility"] == 0   # kern


def test_high_facility():
    raw = "<191>Mar  7 10:00:00 host1 app: msg"
    result = parse_syslog(raw, "10.0.0.1")
    assert result["severity"] == 7   # debug
    assert result["facility"] == 23  # local7


# ── Source IP preservation ────────────────────────────────────────────────────

def test_source_ip_preserved():
    raw = "<14>Mar  7 10:00:00 host1 app: msg"
    result = parse_syslog(raw, "192.168.1.100")
    assert result["source_ip"] == "192.168.1.100"


def test_ipv6_source():
    raw = "<14>Mar  7 10:00:00 host1 app: msg"
    result = parse_syslog(raw, "::1")
    assert result["source_ip"] == "::1"
