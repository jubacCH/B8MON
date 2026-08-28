"""A read-only API key must not be able to decommission the agent fleet.

Three agent routes hung on ``require_api_key``, which only proves a key is
valid — it does not look at the role. The role check in the HTTP middleware
does not cover them either: it lives inside the branch for session-authenticated
users, and an API-key request has no session user, so it never runs.

``readonly`` is the default role for a new key. A key issued to give someone a
dashboard could therefore delete every agent and queue a remote uninstall, which
the Rust agent carries out on the customer's host.

These tests pin the role each route now requires. They fail against
``require_api_key`` on all three.
"""
import pytest

from routers.api_v1 import require_admin, require_api_key, require_editor
from models.api_key import ApiKey


def _key(role):
    return ApiKey(id=1, name="t", key_hash="", prefix="t", role=role, enabled=True)


@pytest.mark.parametrize("role", ["readonly", "editor"])
async def test_only_admin_passes_the_admin_gate(role):
    with pytest.raises(Exception) as exc:
        await require_admin(_key(role))
    assert "403" in str(exc.value) or "Admin" in str(exc.value)


async def test_readonly_does_not_pass_the_editor_gate():
    with pytest.raises(Exception) as exc:
        await require_editor(_key("readonly"))
    assert "403" in str(exc.value) or "Editor" in str(exc.value)


@pytest.mark.parametrize("route_name,expected", [
    ("delete_agent", require_admin),
    ("uninstall_agent", require_admin),
    ("patch_agent", require_editor),
])
def test_agent_routes_are_role_gated(route_name, expected):
    """Read the guard off the live route, not off a copy of the source.

    ``require_api_key`` authenticates without authorising. A route that carries
    it as its only dependency is open to every valid key regardless of role.
    """
    from routers import api_v1

    route = next(
        r for r in api_v1.router.routes
        if getattr(r, "name", None) == route_name
    )
    guards = {
        d.call for d in route.dependant.dependencies
        if getattr(d, "call", None) is not None
    }
    assert expected in guards, (
        f"{route_name} is not gated by {expected.__name__}; "
        f"found {[g.__name__ for g in guards]}"
    )
    assert require_api_key not in guards, (
        f"{route_name} still depends on require_api_key directly, which "
        f"authenticates without checking the role"
    )
