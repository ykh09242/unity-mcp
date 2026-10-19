"""Independent profiler window admission, transport, and actual SDK contracts."""

import asyncio
import importlib
import os
import subprocess
import sys
import textwrap
from types import SimpleNamespace

import pytest


@pytest.fixture
def profiler(monkeypatch):
    module = importlib.import_module("services.tools.manage_profiler")
    sent = []

    async def instance(_ctx):
        return "owned-editor"

    async def send(_function, selected, command, params):
        sent.append((selected, command, params))
        return {"success": True, "data": {"window_id": "owned-window", "sample_count": 0}}

    monkeypatch.setattr(module, "get_unity_instance_from_context", instance)
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    return module, sent


def test_window_start_forwards_explicit_zero_false_and_native_result(profiler):
    module, sent = profiler
    args = {
        "action": "WINDOW_START",
        "category": "Scripts",
        "markers": ["Owned.Marker"],
        "frame_count": 20,
        "warmup_frames": 0,
        "timeout_seconds": 5,
        "include_samples": False,
    }
    result = asyncio.run(module.manage_profiler(SimpleNamespace(), **args))
    assert result == {"success": True, "data": {"window_id": "owned-window", "sample_count": 0}}
    assert sent == [("owned-editor", "manage_profiler", {**args, "action": "window_start"})]


@pytest.mark.parametrize("action", ["window_status", "window_result", "window_cancel"])
def test_window_identity_is_forwarded_without_injected_start_defaults(profiler, action):
    module, sent = profiler
    result = asyncio.run(
        module.manage_profiler(SimpleNamespace(), action=action, window_id="owned")
    )
    assert result["success"] is True
    assert sent[-1][2] == {"action": action, "window_id": "owned"}


@pytest.mark.parametrize(
    "field,value",
    [
        ("frame_count", 0),
        ("frame_count", 601),
        ("frame_count", True),
        ("frame_count", 1.2),
        ("warmup_frames", -1),
        ("warmup_frames", 121),
        ("timeout_seconds", 0),
        ("timeout_seconds", 121),
        ("include_samples", 0),
        ("include_samples", "false"),
        ("markers", []),
        ("markers", ["same", "same"]),
        ("markers", [True]),
        ("markers", ["m"] * 9),
        ("markers", [" "]),
        ("category", " "),
    ],
)
def test_window_invalid_start_is_local_and_does_not_allocate_transport(profiler, field, value):
    module, sent = profiler
    args = {"action": "window_start", "category": "Scripts", "markers": ["owned"], field: value}
    result = asyncio.run(module.manage_profiler(SimpleNamespace(), **args))
    assert result["success"] is False
    assert not sent


@pytest.mark.parametrize("action", ["window_status", "window_result", "window_cancel"])
def test_window_requires_identity(profiler, action):
    module, sent = profiler
    result = asyncio.run(module.manage_profiler(SimpleNamespace(), action=action))
    assert result["success"] is False
    assert not sent


def test_legacy_actions_keep_omission_behavior(profiler):
    module, sent = profiler
    asyncio.run(module.manage_profiler(SimpleNamespace(), action="profiler_status"))
    assert sent[-1][2] == {"action": "profiler_status"}


def test_window_real_sdk_schema_and_strict_admission():
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
            return {"success":False,"code":"window_not_found","data":{"mean":None,"sample_count":0}}
        module.get_unity_instance_from_context=instance
        module.send_with_unity_instance=send
        server=FastMCP("profiler-window-contract")
        wrapped=log_execution("manage_profiler","Tool")(module.manage_profiler)
        server.tool(name="manage_profiler")(telemetry_tool("manage_profiler")(wrapped))
        async def main():
            for mode in ("2026-07-28","legacy"):
                async with Client(server, mode=mode) as client:
                    tool=next(t for t in await client.list_tools() if t.name=="manage_profiler")
                    assert {"markers","frame_count","warmup_frames","timeout_seconds","window_id","include_samples"} <= set(tool.inputSchema["properties"])
                    args={"action":"window_start","category":"Scripts","markers":["owned"],"frame_count":4,"warmup_frames":0,"include_samples":False}
                    result=await client.call_tool("manage_profiler",args)
                    assert sent[-1]==args
                    assert result.structured_content=={"success":False,"code":"window_not_found","data":{"mean":None,"sample_count":0}}
                    before=len(sent)
                    for field,value in (("frame_count",True),("frame_count",0),("include_samples",0)):
                        try:
                            rejected=await client.call_tool("manage_profiler",{**args,field:value})
                            assert rejected.structured_content["success"] is False
                        except Exception as error:
                            assert "validation" in str(error).lower() or "invalid" in str(error).lower(), str(error)
                    assert len(sent)==before
            print("real SDK independent profiler window contract passed")
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
    assert "real SDK independent profiler window contract passed" in result.stdout
