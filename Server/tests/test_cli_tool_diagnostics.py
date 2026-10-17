"""CLI consumes one advertised named template; it never invents hidden-tool causes."""

import json
from urllib.parse import unquote

from fastmcp import FastMCP
import pytest

from cli.utils.config import CLIConfig
from models.capabilities import CapabilityConnection
from models.tool_diagnostics import (
    NativeToolDiagnostics,
    ToolDiagnosticRow,
    ToolDiagnostics,
    ToolDiagnosticsResponse,
)
from tests.test_tool_description_sdk import TARGET, install_connection, install_resources


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_summary_marker_enables_one_named_template_read_with_unicode(monkeypatch, mode):
    # Given actual SDK resources advertise a summary and a template, never a concrete named URI.
    server = FastMCP("named-diagnostics-cli")
    install_resources(server)
    calls = []
    name = "owned_\u03bb"

    @server.resource("mcpforunity://tool-diagnostics")
    async def summary():
        raise AssertionError("summary is only a support marker")

    @server.resource("mcpforunity://tool-diagnostics/{tool_name}")
    async def named(tool_name: str):
        calls.append(unquote(tool_name))
        return ToolDiagnosticsResponse(
            data=ToolDiagnostics(
                connection=CapabilityConnection(
                    transport="http",
                    status="connected",
                    instance_id=TARGET,
                    generation="opaque-fixture-generation",
                ),
                native=NativeToolDiagnostics(
                    complete=True,
                    rows=[
                        ToolDiagnosticRow(
                            tool_name=name,
                            stage="native_enablement",
                            state="disabled",
                            reason="native_disabled",
                            next_action="review_tool_toggle",
                            native_enabled=False,
                        )
                    ],
                ),
            )
        ).model_dump_json()

    inspection, trace = install_connection(monkeypatch, server)
    # When hidden-tool inspection reads existing optional evidence.
    result = await inspection.inspect_tool(CLIConfig(unity_instance=TARGET), name, protocol=mode)
    # Then the native observation is attributed and only one named request was made.
    assert result.advertised is False and result.exit_code == 3
    assert result.availability.native_enabled is False
    assert result.evidence["tool_diagnostics"]["reason"] == "native_disabled"
    assert calls == [name]
    assert sum(uri.startswith("mcpforunity://tool-diagnostics/") for uri, _ in trace) == 1
    assert all(uri != "mcpforunity://tool-diagnostics" for uri, _ in trace)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_unadvertised_summary_does_not_probe_a_known_template(monkeypatch, mode):
    server = FastMCP("old-peer-no-diagnostic-marker")
    install_resources(server)
    calls = []

    @server.resource("mcpforunity://tool-diagnostics/{tool_name}")
    async def named(tool_name: str):
        calls.append(tool_name)
        return json.dumps({"should_not_be_read": True})

    inspection, _ = install_connection(monkeypatch, server)
    result = await inspection.inspect_tool(
        CLIConfig(unity_instance=TARGET), "missing", protocol=mode
    )
    assert result.availability.native_enabled is None
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
@pytest.mark.parametrize("matching_generation", [False, True])
async def test_new_failure_never_replaces_callable_schema_or_accepts_wrong_owner(
    monkeypatch, mode, matching_generation
):
    from fastmcp import Client
    from models.tool_diagnostics import ToolDiagnosticAttempt

    server = FastMCP("callable-retained-with-diagnostics")
    install_resources(server)
    executed = []

    @server.tool(name="owned")
    async def owned(count: int):
        executed.append(count)
        return {"count": count}

    @server.resource("mcpforunity://tool-diagnostics")
    async def summary():
        return "{}"

    @server.resource("mcpforunity://tool-diagnostics/{tool_name}")
    async def named(tool_name: str):
        return ToolDiagnosticsResponse(
            data=ToolDiagnostics(
                connection=CapabilityConnection(
                    transport="http",
                    status="connected",
                    instance_id=TARGET,
                    generation="opaque-fixture-generation"
                    if matching_generation
                    else "stale-owner",
                ),
                server=[
                    ToolDiagnosticAttempt(
                        source_kind="http",
                        project_id="opaque-project",
                        attempt=2,
                        catalog_revision=1,
                        prior_catalog_retained=True,
                        rows=[
                            ToolDiagnosticRow(
                                tool_name=tool_name,
                                stage="server_publication",
                                state="registration_failed",
                                reason="schema_or_default_invalid",
                                next_action="review_schema_and_defaults",
                            )
                        ],
                    )
                ],
            )
        ).model_dump_json()

    inspection, _ = install_connection(monkeypatch, server)
    result = await inspection.inspect_tool(CLIConfig(unity_instance=TARGET), "owned", protocol=mode)
    async with Client(server, mode=mode) as client:
        listed = (await client.list_tools())[0]
    assert result.exit_code == 0 and result.advertised is True
    assert result.tool["inputSchema"] == listed.input_schema
    assert result.reason == "advertised" and executed == []
    assert result.evidence["tool_diagnostics"]["reason"] == (
        "schema_or_default_invalid" if matching_generation else "wrong_target"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
@pytest.mark.parametrize("advertised", [False, True])
@pytest.mark.parametrize(
    "reason,action",
    [
        ("unsupported_handler", "fix_handler_signature"),
        ("metadata_invalid", "review_tool_declaration"),
        ("schema_or_default_invalid", "review_schema_and_defaults"),
    ],
)
async def test_explicit_native_rejection_is_useful_without_server_attempt_or_enabled_flag(
    monkeypatch,
    mode,
    advertised,
    reason,
    action,
):
    server = FastMCP("native-rejection-introspection")
    install_resources(server)
    executed = []
    if advertised:

        @server.tool(name="owned")
        async def owned(count: int):
            executed.append(count)
            return count

    @server.resource("mcpforunity://tool-diagnostics")
    async def summary():
        return "{}"

    @server.resource("mcpforunity://tool-diagnostics/{tool_name}")
    async def named(tool_name: str):
        return ToolDiagnosticsResponse(
            data=ToolDiagnostics(
                connection=CapabilityConnection(
                    transport="http",
                    status="connected",
                    instance_id=TARGET,
                    generation="opaque-fixture-generation",
                ),
                native=NativeToolDiagnostics(
                    complete=True,
                    rows=[
                        ToolDiagnosticRow(
                            tool_name=tool_name,
                            stage="native_discovery",
                            state="rejected",
                            reason=reason,
                            next_action=action,
                        )
                    ],
                ),
            )
        ).model_dump_json()

    inspection, _ = install_connection(monkeypatch, server)
    result = await inspection.inspect_tool(CLIConfig(unity_instance=TARGET), "owned", protocol=mode)
    assert result.evidence["tool_diagnostics"]["reason"] == reason
    assert result.evidence["tool_diagnostics"]["next_action"] == action
    assert result.availability.native_enabled is None and executed == []
    assert result.advertised is advertised and result.exit_code == (0 if advertised else 3)
    if advertised:
        assert result.reason == "advertised"
        assert "count" in result.tool["inputSchema"]["required"]
