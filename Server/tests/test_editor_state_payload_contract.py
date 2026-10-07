"""Exercise readiness payload validation through the actual SDK in a fresh process."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap


def _run_sdk_regression(source):
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(source)],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "UNITY_MCP_DISABLE_TELEMETRY": "1"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_editor_state_rejects_missing_payload_without_fabricating_readiness():
    source = """
        import asyncio, copy, json, sys
        from unittest.mock import AsyncMock
        sys.path.insert(0, "src")
        from fastmcp import FastMCP, Client
        import services.resources as resources
        import services.resources.editor_state as state
        from services.registry import get_registered_resources

        async def scenario():
            state.get_unity_instance_from_context = AsyncMock(return_value=None)
            state.infer_single_instance_id = AsyncMock(return_value=None)
            state._now_unix_ms = lambda: 5000
            metadata = [r for r in get_registered_resources() if r["name"] == "editor_state"]
            resources.discover_modules = lambda *args: []
            resources.get_registered_resources = lambda: metadata
            mcp = FastMCP("editor-state-payload-contract")
            resources.register_all_resources(mcp)
            for mode in ("2026-07-28", "legacy"):
                async with Client(mcp, mode=mode) as client:
                    for raw in (None, "invalid", {"success": True}, {"success": True, "data": None},
                                {"success": True, "data": []}, {"success": True, "data": "invalid"}):
                        state.unity_transport.send_with_unity_instance = AsyncMock(return_value=copy.deepcopy(raw))
                        response = json.loads((await client.read_resource("mcpforunity://editor/state"))[0].text)
                        assert response["success"] is False, (mode, raw, response)
                        assert response["error"] == "invalid_editor_state", response
                        assert response.get("data") is None or not response["data"].get("advice"), response

                    for data in ({}, {"observed_at_unix_ms": 5000, "sequence": 0},
                                 {"observed_at_unix_ms": 5000, "compilation": {"is_compiling": False},
                                  "tests": {"is_running": False}}):
                        state.unity_transport.send_with_unity_instance = AsyncMock(return_value={"success": True, "data": copy.deepcopy(data)})
                        response = json.loads((await client.read_resource("mcpforunity://editor/state"))[0].text)
                        assert response["success"] is True, response
                        assert response["data"]["advice"]["ready_for_tools"] is True, response
                        assert response["data"]["sequence"] == 0, response

                    state.unity_transport.send_with_unity_instance = AsyncMock(return_value={"success": True, "data": {"observed_at_unix_ms": 0}})
                    response = json.loads((await client.read_resource("mcpforunity://editor/state"))[0].text)
                    assert response["success"] is True and response["data"]["staleness"]["is_stale"] is True, response
                    assert response["data"]["advice"]["ready_for_tools"] is False, response
                    state.unity_transport.send_with_unity_instance = AsyncMock(return_value={"success": False, "error": "busy", "data": {"retry_after_ms": 25}})
                    response = json.loads((await client.read_resource("mcpforunity://editor/state"))[0].text)
                    assert response["success"] is False and response["error"] == "busy", response
                    assert response["data"]["retry_after_ms"] == 25, response

        asyncio.run(scenario())
    """
    _run_sdk_regression(source)


def test_editor_state_preserves_typed_transport_success_and_retry_metadata():
    _run_sdk_regression("""
        import json, sys
        import anyio
        from unittest.mock import AsyncMock
        sys.path.insert(0, "src")
        from fastmcp import FastMCP, Client
        from models import MCPResponse
        import services.resources as resources
        import services.resources.editor_state as state
        from services.registry import get_registered_resources

        async def scenario():
            # Given the actual resource registration and a legacy typed response.
            state.get_unity_instance_from_context = AsyncMock(return_value=None)
            state.infer_single_instance_id = AsyncMock(return_value=None)
            state._now_unix_ms = lambda: 5000
            metadata = [r for r in get_registered_resources() if r["name"] == "editor_state"]
            resources.discover_modules = lambda *args: []
            resources.get_registered_resources = lambda: metadata
            server = FastMCP("editor-state-typed-contract")
            resources.register_all_resources(server)
            replies = (
                MCPResponse(success=False, error="busy", hint="retry", data={"reason": "reloading", "retry_after_ms": 25}),
                MCPResponse(success=True, data={"observed_at_unix_ms": 5000, "sequence": 3}),
            )
            for mode in ("2026-07-28", "legacy"):
                async with Client(server, mode=mode) as client:
                    for reply in replies:
                        state.unity_transport.send_with_unity_instance = AsyncMock(return_value=reply.model_copy(deep=True))
                        # When a client reads editor readiness.
                        result = json.loads((await client.read_resource("mcpforunity://editor/state"))[0].text)
                        # Then typed failures keep retry metadata and success gets readiness.
                        assert result["success"] is reply.success, result
                        if reply.success:
                            assert result["data"]["sequence"] == 3, result
                            assert result["data"]["advice"]["ready_for_tools"] is True, result
                        else:
                            assert result["error"] == reply.error, result
                            assert result["hint"] == reply.hint, result
                            assert result["data"] == reply.data, result
        anyio.run(scenario)
    """)


def test_editor_state_reuses_inferred_instance_for_local_scanning():
    _run_sdk_regression("""
        import json, sys
        import anyio
        from unittest.mock import AsyncMock
        sys.path.insert(0, "src")
        from fastmcp import FastMCP, Client
        from core.config import config
        import services.resources as resources
        import services.resources.editor_state as state
        from services.registry import get_registered_resources

        async def scenario():
            # Given a local read with no explicitly selected instance.
            config.http_remote_hosted = False
            state.get_unity_instance_from_context = AsyncMock(return_value=None)
            state.infer_single_instance_id = AsyncMock(return_value="Selected@fixture")
            state._local_project_root = AsyncMock(return_value=None)
            state.external_changes_scanner.update_and_get_async = AsyncMock(return_value={})
            state.unity_transport.send_with_unity_instance = AsyncMock(side_effect=lambda *args: {"success": True, "data": {}})
            metadata = [r for r in get_registered_resources() if r["name"] == "editor_state"]
            resources.discover_modules = lambda *args: []
            resources.get_registered_resources = lambda: metadata
            server = FastMCP("editor-state-inference-contract")
            resources.register_all_resources(server)
            for mode in ("2026-07-28", "legacy"):
                async with Client(server, mode=mode) as client:
                    before = state.infer_single_instance_id.await_count
                    # When a client reads editor readiness.
                    result = json.loads((await client.read_resource("mcpforunity://editor/state"))[0].text)
                    # Then discovery runs once and the same key describes and scans it.
                    assert state.infer_single_instance_id.await_count - before == 1
                    assert result["data"]["unity"]["instance_id"] == "Selected@fixture", result
                    state._local_project_root.assert_awaited_with("Selected@fixture")
                    state.external_changes_scanner.update_and_get_async.assert_awaited_with("Selected@fixture")
            assert state.unity_transport.send_with_unity_instance.await_count == 2
        anyio.run(scenario)
    """)


def test_editor_state_keeps_native_snapshot_when_optional_discovery_fails():
    _run_sdk_regression("""
        import json, sys
        import anyio
        from unittest.mock import AsyncMock
        sys.path.insert(0, "src")
        from fastmcp import FastMCP, Client, Context
        from core.config import config
        import services.resources as resources
        import services.resources.editor_state as state
        from services.registry import get_registered_resources

        async def scenario():
            # Given a valid native identity and unavailable optional discovery.
            config.http_remote_hosted = False
            state.get_unity_instance_from_context = AsyncMock(return_value=None)
            state.unity_transport.send_with_unity_instance = AsyncMock(side_effect=lambda *args: {
                "success": True, "data": {"unity": {"instance_id": "Native@fixture"}}})
            Context.info = AsyncMock(side_effect=RuntimeError("discovery logging unavailable"))
            state.infer_single_instance_id = AsyncMock(side_effect=RuntimeError("discovery unavailable"))
            state.external_changes_scanner.update_and_get_async = AsyncMock(return_value={})
            metadata = [r for r in get_registered_resources() if r["name"] == "editor_state"]
            resources.discover_modules = lambda *args: []
            resources.get_registered_resources = lambda: metadata
            server = FastMCP("editor-state-discovery-failure-contract")
            resources.register_all_resources(server)
            for mode in ("2026-07-28", "legacy"):
                async with Client(server, mode=mode) as client:
                    # When a client reads readiness and optional discovery raises.
                    result = json.loads((await client.read_resource("mcpforunity://editor/state"))[0].text)
                    # Then the native snapshot remains valid and no untrusted scan runs.
                    assert result["success"] is True, result
                    assert result["data"]["unity"]["instance_id"] == "Native@fixture", result
                    assert state.infer_single_instance_id.await_count == (1 if mode == "2026-07-28" else 2)
                    Context.info.assert_not_awaited()
                    state.external_changes_scanner.update_and_get_async.assert_not_awaited()

            # Cancellation remains control flow even during optional discovery.
            cancelled = anyio.get_cancelled_exc_class()
            state.infer_single_instance_id = AsyncMock(side_effect=cancelled)
            try:
                await state.get_editor_state(AsyncMock())
            except cancelled:
                pass
            else:
                raise AssertionError("Optional discovery must propagate cancellation")
        anyio.run(scenario)
    """)
