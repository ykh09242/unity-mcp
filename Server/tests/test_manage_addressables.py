"""Dedicated Addressables admission and wire contracts."""

import asyncio
import importlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import textwrap
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

REV = "addr1:" + "a" * 64
SETTINGS = "Assets/Owned/Settings.asset"


def test_addressables_tool_exists():
    assert importlib.util.find_spec("services.tools.manage_addressables") is not None, (
        "SPEC15 dedicated Addressables tool is missing."
    )


@pytest.fixture
def wire(monkeypatch):
    if importlib.util.find_spec("services.tools.manage_addressables") is None:
        pytest.skip("Addressables implementation is not present yet.")
    module = importlib.import_module("services.tools.manage_addressables")
    send = AsyncMock(return_value={"success": True, "data": {"revision": REV}})
    context = AsyncMock(return_value="owned")
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    monkeypatch.setattr(module, "get_unity_instance_from_context", context)
    return module, send, context


def invoke(wire, **kwargs):
    return asyncio.run(wire[0].manage_addressables(SimpleNamespace(), **kwargs))


@pytest.mark.parametrize(
    "action,extra",
    [
        ("inspect", {"section": "groups"}),
        ("preflight", {}),
        (
            "set_address",
            {"entry_guid": "b" * 32, "address": "owned/address", "expected_revision": REV},
        ),
        ("move_entry", {"entry_guid": "b" * 32, "group_guid": "c" * 32, "expected_revision": REV}),
        (
            "set_profile_value",
            {
                "profile_id": "c" * 32,
                "variable_name": "Local.BuildPath",
                "value": "Library/Owned",
                "expected_revision": REV,
            },
        ),
    ],
)
def test_explicit_settings_actions_exact_wire(wire, action, extra):
    assert invoke(wire, action=action, settings_path=SETTINGS, **extra)["success"]
    assert wire[1].await_args.args[3] == {
        "action": action,
        "settings_path": SETTINGS,
        "page_number": 1,
        "page_size": 50,
        **extra,
    }


@pytest.mark.parametrize(
    "extra",
    [
        {"settings_path": "../Settings.asset"},
        {"settings_path": "/absolute"},
        {"page_size": True},
        {"page_size": 101},
        {"page_number": 0},
        {"page_number": 2},
        {"section": "all"},
        {"layout_path": "Library/secret.json"},
    ],
)
def test_invalid_read_refused_before_io(wire, extra):
    args = {"action": "inspect", "settings_path": SETTINGS}
    args.update(extra)
    assert invoke(wire, **args)["success"] is False
    wire[1].assert_not_awaited()
    wire[2].assert_not_awaited()


@pytest.mark.parametrize(
    "action,extra",
    [
        ("set_address", {"entry_guid": "b" * 32, "address": ""}),
        ("set_address", {"entry_guid": "bad", "address": "new"}),
        ("set_address", {"entry_guid": "b" * 32, "address": "[bad]", "expected_revision": REV}),
        ("move_entry", {"entry_guid": "b" * 32, "group_guid": "bad"}),
        (
            "set_profile_value",
            {"profile_id": "c" * 32, "variable_name": "x", "value": "new", "expected_revision": ""},
        ),
    ],
)
def test_invalid_authoring_refused_before_io(wire, action, extra):
    assert invoke(wire, action=action, settings_path=SETTINGS, **extra)["success"] is False
    wire[1].assert_not_awaited()


@pytest.mark.parametrize("directory", ["BuildReports", "buildReports"])
def test_layout_is_explicit_read_only_report_path(wire, directory):
    assert invoke(
        wire,
        action="analyze_layout",
        layout_path=f"Library/com.unity.addressables/{directory}/buildlayout_owned.json",
    )["success"]
    assert wire[1].await_args.args[3]["action"] == "analyze_layout"
    wire[1].reset_mock()
    assert invoke(wire, action="build", settings_path=SETTINGS)["success"] is False
    wire[1].assert_not_awaited()


def test_registered_addressables_sdk_wire_and_strict_pages():
    code = textwrap.dedent("""
        import asyncio, importlib
        from fastmcp import FastMCP, Client
        from fastmcp.exceptions import ToolError
        from core.logging_decorator import log_execution
        from core.telemetry_decorator import telemetry_tool
        module = importlib.import_module("services.tools.manage_addressables")
        sent = []
        contexts = []
        async def context(ctx):
            contexts.append(True)
            return "owned"
        async def send(fn, instance, command, args):
            assert command == "manage_addressables" and instance == "owned"
            sent.append(args)
            return {"success": True, "data": {"total": 2}}
        module.get_unity_instance_from_context = context
        module.send_with_unity_instance = send
        server = FastMCP("addressables-contract")
        wrapped = log_execution("manage_addressables", "Tool")(module.manage_addressables)
        server.tool(name="manage_addressables")(telemetry_tool("manage_addressables")(wrapped))
        async def main():
            for mode in ("2026-07-28", "legacy"):
                async with Client(server, mode=mode) as client:
                    args = {"action": "set_address", "settings_path": "Assets/Owned/Settings.asset",
                            "entry_guid": "b" * 32, "address": "owned/address", "expected_revision": "addr1:" + "a" * 64}
                    result = await client.call_tool("manage_addressables", args)
                    assert result.structured_content["data"]["total"] == 2
                    assert sent[-1] == {**args, "page_number": 1, "page_size": 50}
                    before = (len(sent), len(contexts))
                    for invalid in (True, "10", 1.5):
                        try:
                            await client.call_tool("manage_addressables", {"action": "inspect", "settings_path": "Assets/Owned/Settings.asset", "page_size": invalid})
                        except ToolError:
                            pass
                        else:
                            raise AssertionError("Page scalar was coerced")
                    result = await client.call_tool("manage_addressables", {"action": "analyze_layout", "layout_path": "Library/secret.json"})
                    assert result.structured_content["success"] is False
                    assert before == (len(sent), len(contexts))
            print("registered addressables contracts passed")
        asyncio.run(main())
    """)
    result = subprocess.run(
        [sys.executable, "-B", "-c", code],
        capture_output=True,
        text=True,
        timeout=30,
        env={
            **os.environ,
            "UNITY_MCP_DISABLE_TELEMETRY": "true",
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "registered addressables contracts passed" in result.stdout
