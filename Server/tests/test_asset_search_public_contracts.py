"""Registered asset-search SDK and real central CLI contracts; Unity responses stay opaque."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


def _run(program: str, mode: str, tmp_path: Path) -> str:
    source = (Path(__file__).resolve().parents[1] / "src").resolve()
    env = {**os.environ, "UNITY_MCP_DISABLE_TELEMETRY": "true"}
    for key in ("APPDATA", "XDG_DATA_HOME", "UNITY_MCP_LOG_DIR"):
        env[key] = str(tmp_path / key)
    # Preserve an inherited candidate-only sitecustomize guard in every SDK/CLI child.
    env["PYTHONPATH"] = os.pathsep.join(
        dict.fromkeys([str(source), *env.get("PYTHONPATH", "").split(os.pathsep)])
    )
    env.pop("PYTEST_CURRENT_TEST", None)
    result = subprocess.run(
        [sys.executable, "-B", "-c", program, mode, str(source)],
        env=env,
        text=True,
        capture_output=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


SDK_PROGRAM = r"""
import copy
import json
from pathlib import Path
import socket
import sys
import anyio
from fastmcp import Client, FastMCP
from fastmcp.server.middleware import Middleware
from core.config import config
from services.tools import register_all_tools
from services.tools import manage_asset as module
from transport.plugin_hub import PluginHub

assert Path(module.__file__).resolve() == Path(sys.argv[2]) / "services/tools/manage_asset.py"
def deny(*args, **kwargs):
    raise AssertionError("Application network is forbidden in this owned fixture")
requests = []
context_calls = []
original_context = module.get_unity_instance_from_context
async def counted_context(ctx):
    context_calls.append(True)
    return await original_context(ctx)
reply = {"success": True, "data": {"assets": [], "totalAssets": 0, "instanceID": 0, "optional": None}}
async def send(instance, command, params, **kwargs):
    requests.append((instance, command, copy.deepcopy(params)))
    if command == "get_editor_state":
        return {"success": True, "data": {"compilation": {"is_compiling": False}}}
    if command == "get_project_info":
        return {"success": True, "data": {}}
    return copy.deepcopy(reply)
class State(Middleware):
    async def on_call_tool(self, context, call_next):
        await context.fastmcp_context.set_state("unity_instance", "Owned@asset-search")
        return await call_next(context)
token = "mcp-as-v1_" + "a" * 32 + "_" + "b" * 32

async def main():
    socket.socket.connect = deny
    socket.create_connection = deny
    config.transport_mode = "http"
    config.http_remote_hosted = False
    module.get_unity_instance_from_context = counted_context
    PluginHub.send_command_for_instance = staticmethod(send)
    server = FastMCP("asset-search-contract")
    server.add_middleware(State())
    register_all_tools(server)
    async with Client(server, mode=sys.argv[1]) as client:
        # Given: the actual registered SDK advertises the opt-in without changing the public alias.
        schema = {tool.name: tool.input_schema for tool in await client.list_tools()}["manage_asset"]
        assert "metadata_only" in schema["properties"]
        assert "search_snapshot" in schema["properties"]
        legacy = {"action": "search", "path": "Assets", "generatePreview": False, "pageSize": 50, "pageNumber": 1}
        for extra, wire in (
            ({}, legacy),
            ({"metadata_only": False}, legacy),
            ({"metadata_only": True}, {**legacy, "action": "search_metadata_v1"}),
            ({"metadata_only": True, "search_snapshot": token, "page_number": 2},
             {**legacy, "action": "search_metadata_v1", "searchSnapshot": token, "pageNumber": 2}),
        ):
            before = len(requests)
            # When: the real SDK invokes each old/new search mode.
            result = await client.call_tool("manage_asset", {"action": "search", **extra})
            actual = requests[before:]
            domain = [row for row in actual if row[1] == "manage_asset"]
            # Then: exact native wires and opaque results survive both SDK protocols.
            assert domain == [("Owned@asset-search", "manage_asset", wire)], (extra, domain)
            assert result.structured_content == reply
            assert any(row[1] == "get_editor_state" for row in actual), "real preflight was bypassed"
        for extra in (
            {"metadata_only": True, "generate_preview": True},
            {"metadata_only": True, "page_size": 1001},
            {"metadata_only": True, "page_number": 0},
            {"metadata_only": True, "search_snapshot": "invalid"},
            {"metadata_only": True, "search_snapshot": ""},
            {"search_snapshot": token},
            {"metadata_only": "true"},
            {"metadata_only": 1},
        ):
            before = len(requests)
            contexts_before = len(context_calls)
            # When: invalid metadata options reach the public boundary.
            result = await client.call_tool("manage_asset", {"action": "search", **extra}, raise_on_error=False)
            # Then: no context/preflight/native command is attempted.
            assert result.is_error or result.structured_content.get("success") is False, extra
            assert requests[before:] == [], (extra, requests[before:])
            assert len(context_calls) == contexts_before, extra
        before = len(requests)
        result = await client.call_tool("manage_asset", {"action": "get_info", "path": "Assets/a.txt", "metadata_only": True}, raise_on_error=False)
        assert result.is_error or result.structured_content.get("success") is False
        assert requests[before:] == []
        # Given: an old native peer refuses the versioned action with a structured diagnostic.
        reply.clear()
        reply.update({"success": False, "code": "unknown_action", "error": "unsupported", "data": {"zero": 0, "disabled": False, "optional": None}})
        before = len(requests)
        # When: opting into metadata on that peer.
        result = await client.call_tool("manage_asset", {"action": "search", "metadata_only": True})
        # Then: one refused versioned action is returned, with no loading legacy fallback.
        domain = [row for row in requests[before:] if row[1] == "manage_asset"]
        assert len(domain) == 1 and domain[0][2]["action"] == "search_metadata_v1", domain
        assert result.structured_content == reply
        for code in ("stale_search_snapshot", "search_snapshot_query_mismatch"):
            reply.clear()
            reply.update({"success": False, "code": code, "error": code})
            before = len(requests)
            result = await client.call_tool("manage_asset", {"action": "search", "metadata_only": True, "search_snapshot": token, "page_number": 2})
            domain = [row for row in requests[before:] if row[1] == "manage_asset"]
            assert len(domain) == 1 and domain[0][2]["searchSnapshot"] == token, domain
            assert domain[0][2]["action"] == "search_metadata_v1"
            assert result.structured_content == reply
        print("ASSET_SEARCH_SDK", sys.argv[1], json.dumps(domain), "PASS")
anyio.run(main)
"""


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
def test_registered_asset_search_preserves_legacy_and_refuses_unsafe_fallback(
    mode: str, tmp_path: Path
) -> None:
    # Given/When: a fresh process exercises the actual registration and SDK in the selected protocol.
    output = _run(SDK_PROGRAM, mode, tmp_path)
    # Then: child assertions prove exact wires, early refusal, and structured old-peer behavior.
    assert "ASSET_SEARCH_SDK" in output


CLI_PROGRAM = r"""
import copy
import json
from pathlib import Path
import sys
import httpx
from click.testing import CliRunner
from cli.main import cli
from cli.utils import connection
from cli.commands import asset as module
assert Path(module.__file__).resolve() == Path(sys.argv[2]) / "cli/commands/asset.py"
requests = []
reply = {"success": True, "data": {"assets": [], "totalAssets": 0}}
def respond(request):
    requests.append(json.loads(request.content))
    return httpx.Response(200, json=copy.deepcopy(reply))
client_type = httpx.AsyncClient
connection.httpx.AsyncClient = lambda: client_type(transport=httpx.MockTransport(respond))
connection._auth_headers = lambda config: {}
runner = CliRunner()
token = "mcp-as-v1_" + "a" * 32 + "_" + "b" * 32
for args, expected in (
    (["search"], {"action": "search", "path": "Assets", "searchPattern": "*", "pageSize": 25, "pageNumber": 1}),
    (["search", "t:TextAsset", "--metadata-only"], {"action": "search_metadata_v1", "path": "Assets", "searchPattern": "t:TextAsset", "pageSize": 25, "pageNumber": 1}),
    (["search", "t:TextAsset", "--metadata-only", "--search-snapshot", token, "--page", "2"],
     {"action": "search_metadata_v1", "path": "Assets", "searchPattern": "t:TextAsset", "pageSize": 25, "pageNumber": 2, "searchSnapshot": token}),
):
    before = len(requests)
    result = runner.invoke(cli, ["--format", "json", "--instance", "Owned@asset-search", "asset", *args])
    assert result.exit_code == 0, (args, result.output, result.exception)
    assert json.loads(result.stdout) == reply
    assert len(requests[before:]) == 1
    assert requests[-1]["params"] == expected, requests[-1]
    assert requests[-1]["type"] == "manage_asset"
for args in (
    ["search", "--search-snapshot", token],
    ["search", "--metadata-only", "--search-snapshot", "invalid"],
):
    before = len(requests)
    result = runner.invoke(cli, ["--format", "json", "asset", *args])
    assert result.exit_code != 0 and requests[before:] == [], (args, result.output)
reply.clear()
reply.update({"success": False, "code": "unknown_action", "error": "unsupported", "data": {"instanceID": 0}})
before = len(requests)
result = runner.invoke(cli, ["--format", "json", "--instance", "Owned@asset-search", "asset", "search", "--metadata-only"])
assert result.exit_code == 1 and json.loads(result.stdout) == reply
assert len(requests[before:]) == 1 and requests[-1]["params"]["action"] == "search_metadata_v1"
print("ASSET_SEARCH_CLI_PASS")
"""


def test_central_asset_search_cli_preserves_wires_and_refuses_old_peer_without_fallback(
    tmp_path: Path,
) -> None:
    # Given/When: the central Click CLI runs against an HTTP transport fake, without a server.
    output = _run(CLI_PROGRAM, "cli", tmp_path)
    # Then: exact legacy/opt-in wires and old-peer diagnostics survive the real CLI output flow.
    assert "ASSET_SEARCH_CLI_PASS" in output
