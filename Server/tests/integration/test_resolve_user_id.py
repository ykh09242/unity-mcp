"""Identity comes from authentication middleware, never raw headers or session state."""

import sys
import types

import pytest

from core.config import config
from transport.remote_auth_middleware import AUTHENTICATED_USER_STATE
from transport.unity_transport import _resolve_user_id_from_request


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "remote,identity,expected",
    [
        (False, "alice", None),
        (True, "alice", "alice"),
        (True, None, None),
        (True, "", None),
        (True, 123, None),
    ],
)
async def test_resolves_only_authenticated_request_state(monkeypatch, remote, identity, expected):
    monkeypatch.setattr(config, "http_remote_hosted", remote)
    deps = types.ModuleType("fastmcp.server.dependencies")
    deps.get_http_request = lambda: types.SimpleNamespace(
        state=types.SimpleNamespace(**{AUTHENTICATED_USER_STATE: identity}),
        headers={"x-api-key": "unvalidated-key"},
    )
    monkeypatch.setitem(sys.modules, "fastmcp.server.dependencies", deps)
    assert await _resolve_user_id_from_request() == expected


@pytest.mark.asyncio
async def test_missing_http_request_fails_closed(monkeypatch):
    monkeypatch.setattr(config, "http_remote_hosted", True)
    deps = types.ModuleType("fastmcp.server.dependencies")

    def absent():
        raise RuntimeError("No request")

    deps.get_http_request = absent
    monkeypatch.setitem(sys.modules, "fastmcp.server.dependencies", deps)
    assert await _resolve_user_id_from_request() is None
