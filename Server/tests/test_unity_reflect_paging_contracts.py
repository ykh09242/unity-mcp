"""Actual SDK and CLI paging adapters, also reusable with QA-native page JSON."""

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap


def run_paging_adapter_contract(pages):
    """Replay ordered native envelopes verbatim; providers are controlled, not live Unity."""
    program = textwrap.dedent("""
        import asyncio, copy, importlib, json, sys
        import httpx
        from click.testing import CliRunner
        from fastmcp import FastMCP, Client
        from fastmcp.server.middleware import Middleware
        from services.tools import register_all_tools
        from cli.main import cli
        from cli.utils import connection
        reflection = importlib.import_module("services.tools.unity_reflect")
        pages = json.loads(sys.stdin.read())
        current, requests = pages[0], []
        async def send(fn, selected, command, params):
            assert selected == "Selected@fixture" and command == "unity_reflect"
            requests.append(copy.deepcopy(params))
            return copy.deepcopy(current)
        reflection.send_with_unity_instance = send
        class FixtureState(Middleware):
            async def on_call_tool(self, context, call_next):
                await context.fastmcp_context.set_state("unity_instance", "Selected@fixture")
                return await call_next(context)
        server = FastMCP("reflect-paging")
        server.add_middleware(FixtureState())
        register_all_tools(server)
        server.enable(names={'unity_reflect'}, components={"tool"})
        async def scenario():
            global current
            for mode in ("2026-07-28", "legacy"):
                async with Client(server, mode=mode) as client:
                    schema = next(t for t in await client.list_tools() if t.name == "unity_reflect").input_schema
                    assert all(name in schema["properties"] for name in ("page_size", "cursor", "assembly", "type_kind"))
                    assert "spelling" in schema["properties"]["assembly"]["description"]
                    cursor = None
                    for current in pages:
                        request = dict(action="search", query="Dev07Paging", scope="all", page_size=7)
                        if cursor is not None: request["cursor"] = cursor
                        result = await client.call_tool("unity_reflect", request)
                        assert result.structured_content == current
                        assert requests[-1] == request
                        cursor = current["data"]["next_cursor"]
                    result = await client.call_tool("unity_reflect", dict(action="search", query="Fixture", page_size=7, assembly="TestAsmdef", type_kind="struct", cursor="opaque"))
                    assert requests[-1]["assembly"] == "TestAsmdef" and requests[-1]["type_kind"] == "struct" and requests[-1]["cursor"] == "opaque"
                    for value in (0, 101, True, 1.5, "7"):
                        count = len(requests)
                        try:
                            await client.call_tool("unity_reflect", dict(action="search", query="Fixture", page_size=value))
                        except Exception:
                            pass
                        else:
                            raise AssertionError("invalid page_size accepted")
                        assert len(requests) == count
        asyncio.run(scenario())
        http_requests = []
        base_client = httpx.AsyncClient
        def respond(request):
            http_requests.append(json.loads(request.content))
            return httpx.Response(200, json={"status": "success", "result": copy.deepcopy(current)})
        connection.httpx.AsyncClient = lambda: base_client(transport=httpx.MockTransport(respond))
        connection._auth_headers = lambda config: {}
        runner = CliRunner()
        help_result = runner.invoke(cli, ["reflect", "search", "--help"])
        assert help_result.exit_code == 0 and "spelling" in help_result.output
        cursor = None
        for current in pages:
            args = ["--format", "json", "reflect", "search", "Dev07Paging", "--scope", "all", "--page-size", "7"]
            if cursor is not None: args += ["--cursor", cursor]
            result = runner.invoke(cli, args)
            assert result.exit_code == 0, result.output
            assert json.loads(result.output) == current
            assert http_requests[-1]["params"] == dict(action="search", query="Dev07Paging", scope="all", page_size=7, **({"cursor": cursor} if cursor else {}))
            cursor = current["data"]["next_cursor"]
        result = runner.invoke(cli, ["--format", "json", "reflect", "search", "Fixture", "--assembly", "TestAsmdef", "--type-kind", "enum"])
        assert result.exit_code == 0, result.output
        assert http_requests[-1]["params"]["assembly"] == "TestAsmdef"
        assert http_requests[-1]["params"]["type_kind"] == "enum"
        for args in (["--page-size", "0"], ["--page-size", "101"], ["--type-kind", "bad"], ["--cursor", "x" * 1025], ["--assembly", ""]):
            count = len(http_requests)
            result = runner.invoke(cli, ["reflect", "search", "Fixture", *args])
            assert result.exit_code != 0 and len(http_requests) == count
        print("paging adapter pages passed", len(pages))
    """)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    env["UNITY_MCP_DISABLE_TELEMETRY"] = "true"
    result = subprocess.run(
        [sys.executable, "-B", "-c", program],
        input=json.dumps(pages),
        text=True,
        capture_output=True,
        env=env,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_actual_sdk_cli_preserve_paging_order_and_controls():
    pages = [
        {
            "success": True,
            "data": {
                "results": [
                    {"full_name": "Fixture.Alpha", "assembly_full_name": "Assembly.A", "rank": 0},
                    {"full_name": "Fixture.Alpha", "assembly_full_name": "Assembly.B", "rank": 0},
                ],
                "count": 2,
                "next_cursor": "opaque",
                "complete": False,
            },
        },
        {
            "success": True,
            "data": {
                "results": [{"full_name": "Fixture.Zeta", "rank": 2}],
                "count": 1,
                "next_cursor": None,
                "complete": False,
            },
        },
    ]
    assert "paging adapter pages passed 2" in run_paging_adapter_contract(pages)
