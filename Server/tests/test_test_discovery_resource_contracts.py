"""Test discovery responses survive registered production resource serialization."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap


def _run_sdk_scenario(source: str, evidence_dir: Path) -> None:
    # Legacy collection replaces SDK modules, so keep the actual SDK isolated.
    env = dict(os.environ)
    env["UNITY_MCP_DISABLE_TELEMETRY"] = "true"
    for name in ("APPDATA", "XDG_DATA_HOME", "UNITY_MCP_LOG_DIR"):
        env[name] = str(evidence_dir / name.lower())
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(source)],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_test_discovery_resource_preserves_native_path_and_pagination(tmp_path):
    _run_sdk_scenario(
        """
        import asyncio, json, socket, sys
        from unittest.mock import AsyncMock
        sys.path.insert(0, "src")
        from fastmcp import Client, FastMCP
        from core.config import config
        import services.resources as resources
        import services.resources.tests as tests
        from services.registry import get_registered_resources
        import transport.unity_transport as transport

        def deny_outbound(*args, **kwargs):
            raise AssertionError("Unexpected outbound socket connection")

        async def scenario():
            socket.socket.connect = deny_outbound
            socket.socket.connect_ex = deny_outbound
            config.transport_mode = "http"
            config.http_remote_hosted = False
            metadata = [r for r in get_registered_resources() if r["name"] in ("get_tests", "get_tests_for_mode")]
            resources.discover_modules = lambda *args: []
            resources.get_registered_resources = lambda: metadata
            mcp = FastMCP("test-discovery-path")
            resources.register_all_resources(mcp)
            tests.get_unity_instance_from_context = AsyncMock(return_value="Selected@hash")
            row = {"name": "Example", "full_name": "Fixture.Example", "mode": "EditMode",
                "path": "Assembly/Fixture/Example"}
            page = {"items": [row], "cursor": 0, "nextCursor": 1, "totalCount": 51,
                "pageSize": 50, "hasMore": True}
            boundary = AsyncMock(return_value={"success": True, "data": page})
            transport.PluginHub.send_command_for_instance = boundary
            for protocol in ("2026-07-28", "legacy"):
                async with Client(mcp, mode=protocol) as client:
                    for uri, command, params in (
                        ("mcpforunity://tests", "get_tests", {}),
                        ("mcpforunity://tests/EditMode", "get_tests_for_mode", {"mode": "EditMode"}),
                        ("mcpforunity://tests/PlayMode", "get_tests_for_mode", {"mode": "PlayMode"}),
                    ):
                        row["mode"] = params.get("mode", "EditMode")
                        response = json.loads((await client.read_resource(uri))[0].text)
                        assert response["success"] is True
                        assert response["data"] == page
                        assert boundary.call_args.args == ("Selected@hash", command, params)
                        assert boundary.call_args.kwargs == {"user_id": None, "retry_on_reload": True}
        asyncio.run(scenario())
    """,
        tmp_path,
    )


def test_test_discovery_resource_keeps_older_rows_empty_pages_and_error_fallback(tmp_path):
    _run_sdk_scenario(
        """
        import asyncio, json, socket, sys
        from unittest.mock import AsyncMock
        sys.path.insert(0, "src")
        from fastmcp import Client, FastMCP
        from core.config import config
        import services.resources as resources
        import services.resources.tests as tests
        from services.registry import get_registered_resources
        import transport.unity_transport as transport

        def deny_outbound(*args, **kwargs):
            raise AssertionError("Unexpected outbound socket connection")

        async def scenario():
            socket.socket.connect = deny_outbound
            socket.socket.connect_ex = deny_outbound
            config.transport_mode = "http"
            config.http_remote_hosted = False
            metadata = [r for r in get_registered_resources() if r["name"] in ("get_tests", "get_tests_for_mode")]
            resources.discover_modules = lambda *args: []
            resources.get_registered_resources = lambda: metadata
            mcp = FastMCP("test-discovery-compatibility")
            resources.register_all_resources(mcp)
            tests.get_unity_instance_from_context = AsyncMock(return_value="Selected@hash")
            boundary = AsyncMock()
            transport.PluginHub.send_command_for_instance = boundary
            old_row = {"name": "Old", "full_name": "Fixture.Old", "mode": "EditMode"}
            old_page = {"items": [old_row], "cursor": 0, "nextCursor": None,
                "totalCount": 1, "pageSize": 50, "hasMore": False}
            empty_page = dict(old_page, items=[], totalCount=0)
            for protocol in ("2026-07-28", "legacy"):
                async with Client(mcp, mode=protocol) as client:
                    for uri in ("mcpforunity://tests", "mcpforunity://tests/EditMode"):
                        boundary.return_value = {"success": True, "data": old_page}
                        response = json.loads((await client.read_resource(uri))[0].text)
                        assert response["data"]["items"][0] == dict(old_row, path=None)
                        boundary.return_value = {"success": True, "data": empty_page}
                        response = json.loads((await client.read_resource(uri))[0].text)
                        assert response["data"] == empty_page
                        boundary.return_value = {"success": False, "error": "discovery_failed", "message": "Failed to retrieve tests"}
                        response = json.loads((await client.read_resource(uri))[0].text)
                        assert response["success"] is False
                        assert response["error"] == "discovery_failed"
                        assert response["message"] == "Failed to retrieve tests"
                        assert response["data"] is None
        asyncio.run(scenario())
    """,
        tmp_path,
    )


def test_test_discovery_resource_rejects_invalid_mode_before_outbound(tmp_path):
    _run_sdk_scenario(
        """
        import asyncio, socket, sys
        from unittest.mock import AsyncMock
        sys.path.insert(0, "src")
        from fastmcp import Client, FastMCP
        from mcp.shared.exceptions import MCPError
        from core.config import config
        import services.resources as resources
        import services.resources.tests as tests
        from services.registry import get_registered_resources
        import transport.unity_transport as transport

        def deny_outbound(*args, **kwargs):
            raise AssertionError("Unexpected outbound socket connection")

        async def scenario():
            socket.socket.connect = deny_outbound
            socket.socket.connect_ex = deny_outbound
            config.transport_mode = "http"
            config.http_remote_hosted = False
            metadata = [r for r in get_registered_resources() if r["name"] in ("get_tests", "get_tests_for_mode")]
            resources.discover_modules = lambda *args: []
            resources.get_registered_resources = lambda: metadata
            mcp = FastMCP("test-discovery-mode-validation")
            resources.register_all_resources(mcp)
            tests.get_unity_instance_from_context = AsyncMock(return_value="Selected@hash")
            boundary = AsyncMock()
            transport.PluginHub.send_command_for_instance = boundary
            for protocol in ("2026-07-28", "legacy"):
                async with Client(mcp, mode=protocol) as client:
                    for mode in ("invalid", "editmode", "playmode"):
                        try:
                            await client.read_resource("mcpforunity://tests/" + mode)
                        except MCPError:
                            pass
                        else:
                            raise AssertionError("Mode must respect the existing Literal contract")
            boundary.assert_not_awaited()
        asyncio.run(scenario())
    """,
        tmp_path,
    )


def test_resource_error_metadata_survives_strict_data_models(tmp_path):
    _run_sdk_scenario(
        """
        import asyncio, json, socket, sys
        from unittest.mock import AsyncMock
        sys.path.insert(0, "src")
        from fastmcp import Client, FastMCP
        from mcp.shared.exceptions import MCPError
        from core.config import config
        import services.resources as resources
        import services.resources.tests as tests
        import services.resources.tags as tags
        from services.registry import get_registered_resources
        from transport.plugin_hub import InstanceSelectionRequiredError
        import transport.unity_transport as transport

        def deny_outbound(*args, **kwargs):
            raise AssertionError("Unexpected outbound socket connection")

        async def scenario():
            socket.socket.connect = deny_outbound
            socket.socket.connect_ex = deny_outbound
            config.transport_mode = "http"
            config.http_remote_hosted = False
            metadata = [r for r in get_registered_resources() if r["name"] in ("get_tests", "get_tests_for_mode", "project_tags")]
            resources.discover_modules = lambda *args: []
            resources.get_registered_resources = lambda: metadata
            mcp = FastMCP("strict-resource-error-metadata")
            resources.register_all_resources(mcp)
            tests.get_unity_instance_from_context = AsyncMock(return_value="Selected@hash")
            tags.get_unity_instance_from_context = AsyncMock(return_value="Selected@hash")
            boundary = AsyncMock()
            transport.PluginHub.send_command_for_instance = boundary
            uris = ("mcpforunity://tests", "mcpforunity://tests/EditMode", "mcpforunity://project/tags")
            for protocol in ("2026-07-28", "legacy"):
                async with Client(mcp, mode=protocol) as client:
                    for uri in uris:
                        boundary.side_effect = InstanceSelectionRequiredError(available_instances=["A@a", "B@b"])
                        response = json.loads((await client.read_resource(uri))[0].text)
                        assert response["success"] is False
                        assert response["hint"] == "select_instance"
                        assert response["data"] == {"reason": "instance_selection_required", "available_instances": ["A@a", "B@b"]}
                        boundary.side_effect = None
                        for data in ({"reason": "discovery_failed", "count": 0}, [], False, 0, None):
                            boundary.return_value = {"success": False, "error": "discovery_failed",
                                "message": "Failed to retrieve metadata", "hint": "retry", "data": data}
                            response = json.loads((await client.read_resource(uri))[0].text)
                            assert response == {"success": False, "error": "discovery_failed",
                                "message": "Failed to retrieve metadata", "hint": "retry", "data": data}
                        boundary.return_value = {"success": True, "data": {"unexpected": "shape"}}
                        try:
                            await client.read_resource(uri)
                        except MCPError:
                            pass
                        else:
                            raise AssertionError("Successful payloads must still use strict resource data validation")
        asyncio.run(scenario())
    """,
        tmp_path,
    )
