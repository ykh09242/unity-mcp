"""Real HTTP and stdio clients verify all static profiles under full catalog pressure."""

from contextlib import ExitStack
import json
import os
from pathlib import Path
import sys

from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client
import pytest

from tests.http.client_assurance_wire import wire_client
from tests.http.test_local_control_wire import local_server_process

CATALOG_SIZE = 512


@pytest.fixture(scope="module")
def profile_servers(tmp_path_factory):
    """Own one child per static HTTP profile; each matrix case opens a fresh client."""
    servers = {}
    with ExitStack() as children:

        def get(profile):
            if profile not in servers:
                with pytest.MonkeyPatch.context() as environment:
                    environment.setenv("UNITY_MCP_TOOL_PROFILE", profile)
                    environment.setenv("UNITY_MCP_ASSURANCE_CATALOG_SIZE", str(CATALOG_SIZE))
                    servers[profile] = children.enter_context(
                        local_server_process(
                            tmp_path_factory.mktemp(f"client-profile-{profile}"),
                            module="tests.http.client_assurance_server",
                        )
                    )
            return servers[profile]

        yield get


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["minimal", "core", "full"])
@pytest.mark.parametrize("transport", ["modern", "legacy", "stdio"])
async def test_profiles_large_catalog_and_legacy_reset_over_real_transports(
    tmp_path, monkeypatch, profile, transport, request
):
    # Given: actual production registration under each documented static profile.
    monkeypatch.setenv("UNITY_MCP_TOOL_PROFILE", profile)
    monkeypatch.setenv("UNITY_MCP_ASSURANCE_CATALOG_SIZE", str(CATALOG_SIZE))

    async def check(client: Client) -> None:
        initial = await client.list_tools(cache_mode="refresh")
        names = {tool.name for tool in initial.tools}
        generated = {name for name in names if name.startswith("assurance_catalog_")}
        assert len(generated) == (CATALOG_SIZE if profile == "full" else 0)
        assert "manage_tools" in names
        # When: the actual server describes a hidden tool, or legacy selection enables it.
        description = await client.call_tool(
            "manage_tools", {"action": "describe", "tool": "assurance_catalog_0000"}
        )
        assert not description.is_error
        assert description.structured_content["tool"]["input_schema"]["properties"]["value"]
        if transport != "modern":
            enabled = await client.call_tool("manage_tools", {"action": "activate", "group": "vfx"})
            assert enabled.structured_content["activated"] == "vfx"
            active = await client.list_tools(cache_mode="refresh")
            exposed = next(tool for tool in active.tools if tool.name == "assurance_catalog_0000")
            assert exposed.input_schema == description.structured_content["tool"]["input_schema"]
            assert (
                sum(tool.name.startswith("assurance_catalog_") for tool in active.tools)
                == CATALOG_SIZE
            )
            result = await client.call_tool("assurance_catalog_0000", {"value": 23})
            assert not result.is_error
            assert json.loads(result.content[0].text) == 23
            invalid = await client.call_tool("assurance_catalog_0000", {"value": []})
            assert invalid.is_error
            await client.call_tool("manage_tools", {"action": "reset"})
        # Then: reset/refresh returns the profile's complete original catalog.
        restored = await client.list_tools(cache_mode="refresh")
        assert {tool.name for tool in restored.tools} == names
        assert len({tool.name for tool in restored.tools}) == len(restored.tools)

    if transport == "stdio":
        params = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "tests.http.client_assurance_server",
                "--transport",
                "stdio",
                "--project-scoped-tools",
            ],
            cwd=Path(__file__).resolve().parents[2],
            env={
                **os.environ,
                "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"),
                "DISABLE_TELEMETRY": "1",
                "UNITY_MCP_SKIP_STARTUP_CONNECT": "1",
                "UNITY_MCP_STATUS_DIR": str(tmp_path / "status"),
                "UNITY_MCP_LOG_DIR": str(tmp_path / "logs"),
            },
        )
        with (tmp_path / "stdio.log").open("w", encoding="utf-8") as log:
            async with Client(
                stdio_client(params, errlog=log), mode="legacy", cache=None
            ) as client:
                await check(client)
    else:
        server = request.getfixturevalue("profile_servers")(profile)
        async with wire_client(server, transport) as client:
            await check(client)
