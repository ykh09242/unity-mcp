"""Actual SDK registration and selected-instance routing with controlled providers."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap


def test_docs_versions_and_policy_at_actual_sdk_boundary():
    code = textwrap.dedent("""
        import asyncio, importlib
        from types import SimpleNamespace
        from fastmcp import FastMCP, Client
        from services.tools import register_all_tools
        from transport.unity_instance_middleware import UnityInstanceMiddleware
        from core.config import config
        docs = importlib.import_module("services.tools.unity_docs")
        config.transport_mode = "stdio"
        config.http_remote_hosted = False
        requests = []
        versions = {"Old@12345678": "2022.3.45f1", "New@87654321": "6000.0.69f1"}
        class Pool:
            def resolve_instance(self, selected, force_refresh=False):
                return SimpleNamespace(unity_version=versions[selected])
        docs.get_unity_connection_pool = lambda: Pool()
        async def fetch(url):
            requests.append(url)
            if "Missing" in url and "/Documentation/" in url:
                return 404, "", url
            return 200, "<h1>Fixture</h1>", url
        docs._fetch_url_full = fetch
        middleware = UnityInstanceMiddleware()
        async def resolve(value, ctx):
            assert value in versions
            return value
        async def no_auto(ctx): return None
        middleware._resolve_instance_value = resolve
        middleware._maybe_autoselect_instance = no_auto
        server = FastMCP("docs-contracts")
        server.add_middleware(middleware)
        register_all_tools(server)
        async def main():
            async with Client(server) as client:
                schema = next(t for t in await client.list_tools() if t.name == "unity_docs").input_schema
                assert schema["properties"]["strict_version"]["default"] is False
                assert "selected" in schema["properties"]["version"]["description"].lower()
                for selected, target in (("Old@12345678", "2022.3"), ("New@87654321", "6000.0")):
                    result = (await client.call_tool("unity_docs", {"action": "get_doc", "class_name": "Physics", "unity_instance": selected})).structured_content
                    assert result["data"]["version"]["target_version"] == target
                    assert f"/{target}/Documentation/" in result["data"]["url"]
                explicit = (await client.call_tool("unity_docs", {"action": "get_doc", "class_name": "Physics", "unity_instance": "Old@12345678", "version": "6000.0"})).structured_content
                assert explicit["data"]["version"]["version_source"] == "explicit"
                assert explicit["data"]["version"]["target_version"] == "6000.0"
                for strict in (False, True):
                    result = (await client.call_tool("unity_docs", {"action": "get_doc", "class_name": "Missing", "version": "2022.3", "strict_version": strict})).structured_content
                    assert result["data"]["found"] is not strict
                    assert result["data"]["version"]["fallback_blocked"] is strict
                result = (await client.call_tool("unity_docs", {"action": "get_doc", "class_name": "Physics"})).structured_content
                assert result["data"]["version"]["resolution_reason"] == "no_selected_instance"
            print("actual SDK version contracts passed")
        asyncio.run(main())
    """)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    env["UNITY_MCP_DISABLE_TELEMETRY"] = "true"
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=90
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "actual SDK version contracts passed" in result.stdout
