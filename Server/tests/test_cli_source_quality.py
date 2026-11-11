"""Composition must reach server-owned handles instead of raw native commands."""

import json
from types import SimpleNamespace

from click.testing import CliRunner
import pytest


@pytest.mark.asyncio
async def test_composition_uses_the_actual_sdk_wire_once(monkeypatch):
    from fastmcp import FastMCP
    from cli.utils.config import CLIConfig
    from cli.utils.composed_tools import send_composed_tool
    from tests.test_cli_doctor import live_mcp

    monkeypatch.setenv("UNITY_MCP_LOCAL_AUTH_TOKEN", "disposable-composition-token")
    monkeypatch.delenv("UNITY_MCP_LOCAL_AUTH_TOKEN_FILE", raising=False)
    server, calls, trace = FastMCP("composition-wire"), [], []

    @server.tool()
    def manage_verification(action: str, options: dict) -> dict:
        calls.append((action, options))
        return {"success": True, "data": {"run_id": "a" * 32}}

    async with live_mcp(server, trace) as port:
        result = await send_composed_tool(
            "manage_verification",
            "run",
            {"preparation_id": "b" * 32},
            CLIConfig(port=port, unity_instance="Fixture@aaaa1111", timeout=5),
        )
    assert result["data"]["run_id"] == "a" * 32
    assert calls == [("run", {"preparation_id": "b" * 32})]
    assert trace.count("tools/call") == 1


@pytest.mark.parametrize(
    "command,action,options",
    [
        (["script", "quality"], "analyze", {"paths": ["Assets/Example.cs"]}),
        (["script", "quality"], "status", {"job_id": "a" * 32}),
        (["script", "quality"], "impact", {"analysis_id": "b" * 32}),
        (
            ["verification"],
            "prepare",
            {"paths": ["Assets/Example.cs"], "profile_id": "editmode-check"},
        ),
        (["verification"], "run", {"preparation_id": "c" * 32}),
        (["verification"], "report", {"run_id": "d" * 32}),
    ],
)
def test_cli_uses_public_composition_schema(monkeypatch, command, action, options):
    from cli.main import cli
    import cli.utils.composed_tools as composed

    sent = []
    monkeypatch.setattr(
        composed,
        "run_composed_tool",
        lambda *args: sent.append(args) or {"success": True, "data": {"id": "owned"}},
    )
    result = CliRunner().invoke(
        cli,
        [
            "--no-project-context",
            "--format",
            "json",
            *command,
            action,
            "--options",
            json.dumps(options),
        ],
    )
    assert result.exit_code == 0, result.output
    assert len(sent) == 1
    assert sent[0][0] == (
        "manage_script_quality" if command[0] == "script" else "manage_verification"
    )
    assert sent[0][1] == action
    assert all(sent[0][2][key] == value for key, value in options.items())
    assert json.loads(result.output)["data"]["id"] == "owned"


@pytest.mark.parametrize(
    "command,action,options",
    [
        (["script", "quality"], "analyze", {}),
        (["script", "quality"], "analyze", {"paths": ["../private.cs"]}),
        (
            ["verification"],
            "set_profile",
            {"profile_id": "x", "expected_hash": "missing", "profile": {"command": "anything"}},
        ),
    ],
)
def test_invalid_composition_options_refused_before_connection(
    monkeypatch, command, action, options
):
    from cli.main import cli
    import cli.utils.composed_tools as composed

    monkeypatch.setattr(
        composed, "run_composed_tool", lambda *_: pytest.fail("invalid input dispatched")
    )
    result = CliRunner().invoke(
        cli,
        [
            "--no-project-context",
            "--format",
            "json",
            *command,
            action,
            "--options",
            json.dumps(options),
        ],
    )
    assert result.exit_code == 1
    assert json.loads(result.output)["code"] == "invalid_composition_options"


@pytest.mark.asyncio
async def test_public_dispatch_has_exact_target_and_never_replays_timeout(monkeypatch):
    from cli.utils.config import CLIConfig
    from cli.utils.connection import UnityConnectionError
    import cli.utils.composed_tools as composed

    sent = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def call_tool_mcp(self, name, params, *, meta):
            sent.append((name, params, meta))
            raise TimeoutError("secret")

    monkeypatch.setattr(composed, "DiagnosticClient", lambda *_: Client())
    monkeypatch.setattr(composed, "_auth_headers", lambda *_: {})
    with pytest.raises(UnityConnectionError, match="outcome is unknown") as error:
        await composed.send_composed_tool(
            "manage_verification",
            "run",
            {"preparation_id": "a" * 32},
            CLIConfig(unity_instance="Fixture@aaaa1111"),
        )
    assert "secret" not in str(error.value)
    assert len(sent) == 1
    assert sent[0][2] == {"unity_instance": "Fixture@aaaa1111"}


@pytest.mark.asyncio
async def test_public_dispatch_preserves_structured_child_handle(monkeypatch):
    from cli.utils.config import CLIConfig
    import cli.utils.composed_tools as composed

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def call_tool_mcp(self, *_args, **_kwargs):
            return SimpleNamespace(
                is_error=False,
                structured_content={"success": True, "data": {"run_id": "a" * 32}},
                content=[],
            )

    monkeypatch.setattr(composed, "DiagnosticClient", lambda *_: Client())
    monkeypatch.setattr(composed, "_auth_headers", lambda *_: {})
    result = await composed.send_composed_tool(
        "manage_verification",
        "run",
        {"preparation_id": "b" * 32},
        CLIConfig(unity_instance="Fixture@aaaa1111"),
    )
    assert result["data"]["run_id"] == "a" * 32


@pytest.mark.asyncio
async def test_public_dispatch_never_downgrades_project_guard(monkeypatch):
    from cli.utils.config import CLIConfig
    from cli.utils.input_errors import CLIInputError
    import cli.utils.composed_tools as composed

    monkeypatch.setattr(composed, "_auth_headers", lambda *_: pytest.fail("guard was bypassed"))
    with pytest.raises(CLIInputError, match="Project context"):
        await composed.send_composed_tool(
            "manage_verification",
            "profiles",
            {},
            CLIConfig(unity_instance="Fixture@aaaa1111", expected_project_root="/Owned"),
        )
