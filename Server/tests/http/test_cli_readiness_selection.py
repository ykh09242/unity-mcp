"""Compare CLI readiness with the real local inventory and command selector."""

import json
from unittest.mock import AsyncMock

import httpx
import pytest
from click.testing import CliRunner
from starlette.testclient import TestClient

from cli.main import cli
from cli.utils import connection
from core.config import config
from main import _select_local_session, create_mcp_server
from transport.models import SessionDetails, SessionList
from transport.plugin_hub import PluginHub


@pytest.fixture
def selection_client(monkeypatch, prepared_tool_factory):
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(config, "local_auth_token", "inert-selection-token")
    from fastmcp.tools.base import Tool

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(Tool, "from_function", prepared_tool_factory)
        server = create_mcp_server(False)
    return TestClient(server.http_app(), headers={"X-Unity-MCP-Token": "inert-selection-token"})


@pytest.mark.parametrize(
    "catalog,selector,expected",
    [
        ([], None, None),
        ([("Sample", "abcdef1234567890")], None, "session-1"),
        ([("Sample", "abcdef1234567890")], "", "session-1"),
        ([("Sample", "abcdef1234567890")], "Sample", "session-1"),
        ([("Sample", "abcdef1234567890")], "abcdef1234567890", "session-1"),
        ([("Sample", "abcdef1234567890")], "Sample@abcdef1234567890", "session-1"),
        ([("Sample", "abcdef1234567890")], "Other@abcdef1234567890", "session-1"),
        ([("Sample", "abcdef1234567890")], "@abcdef1234567890", "session-1"),
        ([("Sample", "abcdef1234567890")], "abcd", None),
        ([("Sample", "abcdef1234567890")], "Sample@abcd", None),
        ([("Sample", "abcdef1234567890")], "session-1", None),
        ([("Sample", "abcdef1234567890")], "Sample@", None),
        ([("Sample", "abcdef1234567890")], " Sample ", None),
        ([("Sample", "abcdef1234567890")], " abcdef1234567890 ", None),
        ([("Sample", "abcdef1234567890")], "Sample@missing", None),
        ([("Game@2026", "abcdef1234567890")], "Game@2026", None),
        ([("Game@2026", "abcdef1234567890")], "Game@2026@abcdef1234567890", "session-1"),
        ([("Sample", "aaaa"), ("Other", "bbbb")], None, None),
        ([("Sample", "aaaa"), ("Other", "bbbb")], "Other", "session-2"),
        ([("Sample", "aaaa"), ("Sample", "bbbb")], "Sample", None),
        ([("Sample", "aaaa"), ("Sample", "bbbb")], "Sample@bbbb", "session-2"),
        ([("bbbb", "aaaa"), ("Other", "bbbb")], "bbbb", "session-2"),
        ([("bbbb", "aaaa"), ("Other", "bbbb"), ("Third", "bbbb")], "bbbb", None),
        ([("Sample", "aaaa"), ("Other", "aaaa")], "aaaa", None),
        ([("Sample", "aaaa"), ("Other", "aaaa")], "Other@aaaa", None),
        ([("Sample", "aaaa"), ("Other", "aaaa")], "Other", "session-2"),
        ([("Other", "bbbb"), ("Other", "aaaa")], "Different@bbbb", "session-1"),
    ],
)
def test_readiness_agrees_with_forwarded_local_command(
    selection_client, monkeypatch, catalog, selector, expected
):
    # Given real inventory serialization and a recorder at the Editor boundary.
    sessions = SessionList(
        sessions={
            f"session-{index}": SessionDetails(
                project=project, hash=identity, unity_version="6000", connected_at="now"
            )
            for index, (project, identity) in enumerate(catalog, start=1)
        }
    )
    monkeypatch.setattr(PluginHub, "get_sessions", AsyncMock(return_value=sessions))
    dispatch = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(PluginHub, "send_command", dispatch)
    requests = []

    def respond(request):
        requests.append(request)
        response = selection_client.request(
            request.method,
            request.url.path,
            content=request.content,
            headers={"Content-Type": request.headers.get("Content-Type", "application/json")},
        )
        return httpx.Response(response.status_code, json=response.json())

    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        connection.httpx,
        "AsyncClient",
        lambda **_: client_type(transport=httpx.MockTransport(respond)),
    )
    monkeypatch.setattr(connection, "_auth_headers", lambda _: {})
    arguments = ["--no-project-context", "--format", "json"]
    if selector is not None:
        arguments += ["--instance", selector]

    # When Click runs readiness, then the same selection is forwarded by a raw command.
    ready = CliRunner().invoke(cli, [*arguments, "status", "--ready"])
    dispatch.assert_not_awaited()
    assert [request.url.path for request in requests] == ["/health", "/api/instances"]
    command = CliRunner().invoke(cli, [*arguments, "raw", "manage_scene", "{}"])

    # Then readiness agrees with the real selector and route, including CLI omission.
    forwarded = json.loads(requests[-1].content)
    assert requests[-1].url.path == "/api/command"
    assert forwarded.get("unity_instance") == (selector or None)
    assert _select_local_session(sessions, forwarded.get("unity_instance"))[0] == expected
    assert command.exit_code == (0 if expected else 1), (command.exception, command.output)
    if expected:
        assert dispatch.await_args.args == (expected, "manage_scene", {})
    else:
        dispatch.assert_not_awaited()
    assert ready.exit_code == (0 if expected else 1), (ready.exception, ready.output)
    assert json.loads(ready.stdout)["target_ready"] is (expected is not None)
