"""SPEC10 public admission and transport; native byte evidence remains Editor-owned."""

import asyncio
import importlib
import os
import subprocess
import sys
import textwrap
from types import SimpleNamespace

import pytest


@pytest.fixture
def memory_tool(monkeypatch):
    module = importlib.import_module("services.tools.manage_profiler")
    sent = []

    async def instance(_ctx):
        return "owned-editor"

    async def send(_function, selected, command, params):
        sent.append((selected, command, params))
        return {
            "success": False,
            "code": "snapshot_format_unsupported",
            "data": {"managed_heap_bytes_delta": None, "count_delta": 0},
        }

    monkeypatch.setattr(module, "get_unity_instance_from_context", instance)
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    return module, sent


def test_live_aggregation_forwards_scope_and_explicit_false(memory_tool):
    module, sent = memory_tool
    args = {
        "action": "memory_aggregate",
        "targets": ["-120", "-121"],
        "include_references": False,
        "include_objects": True,
        "max_objects": 64,
        "object_limit": 20,
    }
    result = asyncio.run(module.manage_profiler(SimpleNamespace(), **args))
    assert sent == [("owned-editor", "manage_profiler", args)]
    assert result["code"] == "snapshot_format_unsupported"
    assert result["data"] == {"managed_heap_bytes_delta": None, "count_delta": 0}


def test_snapshot_analysis_uses_actual_paths_and_no_file_size_fallback(memory_tool):
    module, sent = memory_tool
    args = {
        "action": "memory_analyze_snapshots",
        "snapshot_a": "Memory/a.snap",
        "snapshot_b": "Memory/b.snap",
        "name_prefix": "Owned_",
        "object_limit": 16,
        "max_objects": 200000,
    }
    result = asyncio.run(module.manage_profiler(SimpleNamespace(), **args))
    assert sent[-1][2] == args
    assert result == {
        "success": False,
        "code": "snapshot_format_unsupported",
        "data": {"managed_heap_bytes_delta": None, "count_delta": 0},
    }


def test_reader_capability_does_not_inject_capture_parameters(memory_tool):
    module, sent = memory_tool
    asyncio.run(module.manage_profiler(SimpleNamespace(), action="memory_snapshot_reader_status"))
    assert sent[-1][2] == {"action": "memory_snapshot_reader_status"}


@pytest.mark.parametrize(
    "field,value",
    [
        ("targets", []),
        ("targets", [True]),
        ("targets", ["not-an-id"]),
        ("targets", ["1"] * 33),
        ("max_objects", 0),
        ("max_objects", 4097),
        ("max_objects", True),
        ("include_references", 1),
        ("include_objects", "false"),
        ("object_limit", 257),
    ],
)
def test_aggregate_invalid_scope_does_not_reach_unity(memory_tool, field, value):
    module, sent = memory_tool
    args = {"action": "memory_aggregate", "targets": ["1"], field: value}
    result = asyncio.run(module.manage_profiler(SimpleNamespace(), **args))
    assert result["success"] is False
    assert not sent


@pytest.mark.parametrize(
    "args",
    [
        {},
        {"snapshot_a": "a.snap"},
        {"snapshot_a": False, "snapshot_b": "b.snap"},
        {"snapshot_a": "a.snap", "snapshot_b": "b.snap", "max_objects": 200001},
        {"snapshot_a": "a.snap", "snapshot_b": "b.snap", "name_prefix": "x" * 129},
    ],
)
def test_snapshot_invalid_admission_is_local(memory_tool, args):
    module, sent = memory_tool
    result = asyncio.run(
        module.manage_profiler(SimpleNamespace(), action="memory_analyze_snapshots", **args)
    )
    assert result["success"] is False
    assert not sent


def test_existing_snapshot_metadata_action_keeps_legacy_payload(memory_tool):
    module, sent = memory_tool
    args = {"action": "memory_compare_snapshots", "snapshot_a": "a.snap", "snapshot_b": "b.snap"}
    asyncio.run(module.manage_profiler(SimpleNamespace(), **args))
    assert sent[-1][2] == args


def test_memory_analysis_actual_sdk_schema_and_native_failure_shape():
    code = textwrap.dedent("""
        import asyncio, importlib
        from fastmcp import FastMCP, Client
        from core.logging_decorator import log_execution
        from core.telemetry_decorator import telemetry_tool
        module=importlib.import_module("services.tools.manage_profiler")
        sent=[]
        async def instance(ctx): return None
        async def send(fn, instance, command, params):
            sent.append(params)
            return {"success":False,"code":"snapshot_reader_unavailable","data":{"required_package_version":"1.1.12"}}
        module.get_unity_instance_from_context=instance
        module.send_with_unity_instance=send
        server=FastMCP("profiler-memory-analysis")
        wrapped=log_execution("manage_profiler","Tool")(module.manage_profiler)
        server.tool(name="manage_profiler")(telemetry_tool("manage_profiler")(wrapped))
        async def main():
            for mode in ("2026-07-28","legacy"):
                async with Client(server,mode=mode) as client:
                    tool=next(t for t in await client.list_tools() if t.name=="manage_profiler")
                    assert {"targets","max_objects","include_references","include_objects","object_limit","name_prefix"} <= set(tool.inputSchema["properties"])
                    for args in ({"action":"memory_aggregate","targets":["-71"],"include_references":False},
                                 {"action":"memory_analyze_snapshots","snapshot_a":"a.snap","snapshot_b":"b.snap","object_limit":1},
                                 {"action":"memory_snapshot_reader_status"}):
                        result=await client.call_tool("manage_profiler",args)
                        assert sent[-1]==args
                        assert result.structured_content=={"success":False,"code":"snapshot_reader_unavailable","data":{"required_package_version":"1.1.12"}}
                    before=len(sent)
                    for args in ({"action":"memory_aggregate","targets":["-71"],"include_references":0},
                                 {"action":"memory_aggregate","targets":["-71"],"max_objects":4097},
                                 {"action":"memory_analyze_snapshots","snapshot_a":"a.snap","snapshot_b":"b.snap","name_prefix":"x"*129}):
                        try:
                            rejected=await client.call_tool("manage_profiler",args)
                            assert rejected.structured_content["success"] is False
                        except Exception as error:
                            assert "validation" in str(error).lower() or "invalid" in str(error).lower(), str(error)
                    assert len(sent)==before
            print("real SDK memory analysis payload/failure contract passed")
        asyncio.run(main())
    """)
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=40,
        env={**os.environ, "UNITY_MCP_DISABLE_TELEMETRY": "true"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "real SDK memory analysis payload/failure contract passed" in result.stdout
