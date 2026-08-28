"""The address handed to an agent must be one the agent can actually reach.

Every browser request arrives through the frontend's proxy, which sets
``changeOrigin`` — it rewrites ``Host`` to its upstream target and moves the
real one into ``X-Forwarded-Host``. The installers derived their server URL
from ``request.url.netloc`` and so wrote the internal Docker service name into
the one-liners and into the agent's ``config.json``:

    curl -sSL 'http://nodeglow:8000/install/linux?token=…' | sudo bash

Nothing resolves ``nodeglow`` on a customer's machine. The agent product did
not work at all outside the container network, and the documented override
(``agent_server_url``) was read in four places and written in none — settable
only by hand-editing the database.
"""
from unittest.mock import AsyncMock, patch

import pytest

from routers.agents import resolve_server_url


class FakeURL:
    def __init__(self, netloc, scheme="http"):
        self.netloc = netloc
        self.scheme = scheme


class FakeRequest:
    def __init__(self, headers=None, netloc="nodeglow:8000", scheme="http"):
        self.headers = headers or {}
        self.url = FakeURL(netloc, scheme)


def _no_setting():
    return patch("routers.agents.get_setting", new=AsyncMock(return_value=""))


def _setting(value):
    return patch("routers.agents.get_setting", new=AsyncMock(return_value=value))


async def test_the_proxys_host_wins_over_the_internal_one():
    """The exact production shape."""
    req = FakeRequest(
        headers={"x-forwarded-host": "nodeglow.example.ch", "x-forwarded-proto": "https"},
        netloc="nodeglow:8000",
    )
    with _no_setting():
        assert await resolve_server_url(req) == "https://nodeglow.example.ch"


async def test_a_direct_request_still_uses_its_own_host():
    req = FakeRequest(headers={}, netloc="10.0.0.5:8000")
    with _no_setting():
        assert await resolve_server_url(req) == "http://10.0.0.5:8000"


async def test_the_operator_setting_beats_both():
    req = FakeRequest(headers={"x-forwarded-host": "internal.local"})
    with _setting("https://monitoring.kunde.ch"):
        assert await resolve_server_url(req) == "https://monitoring.kunde.ch"


async def test_a_trailing_slash_does_not_produce_a_double_slash():
    """The value is concatenated with '/install/linux' — '//' breaks the URL."""
    req = FakeRequest()
    with _setting("https://monitoring.kunde.ch/"):
        assert await resolve_server_url(req) == "https://monitoring.kunde.ch"


async def test_a_blank_setting_is_not_treated_as_configured():
    req = FakeRequest(headers={"x-forwarded-host": "real.example.ch"})
    with _setting("   "):
        assert await resolve_server_url(req) == "http://real.example.ch"


async def test_a_proxy_chain_uses_the_client_facing_host():
    """Chained proxies append; the first entry is the one the client asked for."""
    req = FakeRequest(headers={"x-forwarded-host": "edge.example.ch, inner.local"})
    with _no_setting():
        assert await resolve_server_url(req) == "http://edge.example.ch"


async def test_agent_server_url_is_actually_writable():
    """It was read in four places and written in none.

    Without this the only way to correct the address was an INSERT into the
    settings table, which is not a thing a customer can be asked to do.
    """
    import inspect

    from routers.settings.general import save_settings, settings_json

    assert "agent_server_url" in inspect.signature(save_settings).parameters
    assert "agent_server_url" in inspect.getsource(save_settings)
    assert "agent_server_url" in inspect.getsource(settings_json)
