"""Cache reuse and authoritative fallback through actual FastMCP resource reads."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap


def test_sdk_resource_reuses_push_and_strict_reads_keep_rpc_authority():
    source = '''
        import asyncio, copy, json, sys, time
        from unittest.mock import AsyncMock
        sys.path.insert(0, "src")
        from fastmcp import FastMCP, Client
        from core.config import config
        from transport.editor_state_store import EditorStateStore
        import services.resources as resources
        import services.resources.editor_state as state
        from services.registry import get_registered_resources

        async def scenario():
            # Given actual SDK resource registration and an authenticated cache slot.
            now = time.time()
            clock = {"wall": now, "mono": 10.0}
            cache = EditorStateStore(wall_time=lambda: clock["wall"], monotonic=lambda: clock["mono"])
            cache.register_session("fixture-socket", None)
            payload = {
                "type": "editor_state", "epoch": "fixture-domain", "sequence": 7,
                "observed_at_unix_ms": int(now * 1000),
                "state": {"schema_version": "unity-mcp/editor_state@2", "sequence": 7,
                          "observed_at_unix_ms": int(now * 1000),
                          "compilation": {"is_compiling": False, "is_domain_reload_pending": False}},
            }
            async def cached(instance, user_id=None):
                assert instance == "Fixture@fixture"
                return cache.get("fixture-socket", user_id)
            config.transport_mode = "http"
            config.http_remote_hosted = False
            state.PluginHub.is_configured = staticmethod(lambda: True)
            state.PluginHub.get_cached_editor_state = staticmethod(cached)
            state.get_unity_instance_from_context = AsyncMock(return_value="Fixture@fixture")
            state._local_project_root = AsyncMock(return_value=None)
            state.external_changes_scanner.update_and_get_async = AsyncMock(return_value={})
            authoritative = copy.deepcopy(payload["state"])
            authoritative["sequence"] = 99
            rpc = AsyncMock(side_effect=lambda *args: {"success": True, "data": copy.deepcopy(authoritative)})
            state.unity_transport.send_with_unity_instance = rpc
            metadata = [r for r in get_registered_resources() if r["name"] == "editor_state"]
            resources.discover_modules = lambda *args: []
            resources.get_registered_resources = lambda: metadata
            server = FastMCP("editor-state-push-resource")
            resources.register_all_resources(server)

            for mode in ("2026-07-28", "legacy"):
                cache.remove_session("fixture-socket")
                cache.register_session("fixture-socket", None)
                clock.update(wall=now, mono=10.0)
                async with Client(server, mode=mode) as client:
                    # When an older peer has not pushed any state.
                    before = rpc.await_count
                    fallback = json.loads((await client.read_resource("mcpforunity://editor/state"))[0].text)
                    # Then the existing RPC supplies the state.
                    assert fallback["data"]["sequence"] == 99
                    assert rpc.await_count == before + 1

                    # When twenty actual SDK reads follow one fresh push.
                    assert cache.accept("fixture-socket", payload)
                    before = rpc.await_count
                    for _ in range(20):
                        pushed = json.loads((await client.read_resource("mcpforunity://editor/state"))[0].text)
                        assert pushed["data"]["sequence"] == 7
                    # Then none requires an editor RPC.
                    assert rpc.await_count == before

                    # When a strict internal workflow reads concurrently valid pushed state.
                    strict = await state.get_editor_state_authoritative(AsyncMock())
                    # Then it still obtains the authoritative editor reply.
                    assert strict.data["sequence"] == 99
                    assert rpc.await_count == before + 1

                    # When observation/receive freshness expires.
                    clock.update(wall=now + 3, mono=13.0)
                    expired = json.loads((await client.read_resource("mcpforunity://editor/state"))[0].text)
                    # Then the resource automatically uses the authoritative fallback.
                    assert expired["data"]["sequence"] == 99
                    assert rpc.await_count == before + 2

                    # When the authenticated connection closes.
                    clock.update(wall=now, mono=10.0)
                    cache.remove_session("fixture-socket")
                    disconnected = json.loads((await client.read_resource("mcpforunity://editor/state"))[0].text)
                    # Then the closed peer's cached state cannot be served.
                    assert disconnected["data"]["sequence"] == 99
                    assert rpc.await_count == before + 3
        asyncio.run(scenario())
    '''
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(source)],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "UNITY_MCP_DISABLE_TELEMETRY": "1"},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
