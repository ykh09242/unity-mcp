"""Run real tools/resources/SDK in fresh children, with only Unity boundaries controlled."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("case", [
    "tests_start_during_compile", "known_tests_dirty", "compile_finishes_ready",
    "clear_stuck", "unknown_state", "malformed_state", "stale_idle",
    "refresh_error_blocks", "refresh_exception_blocks", "requires_no_tests_false", "invalid_init_timeout",
    "dirty_assets_ready", "dirty_script_compiles",
])
def test_preflight_public_contract(case, tmp_path):
    owned = tmp_path / case
    owned.mkdir()
    env = os.environ.copy()
    for key in ("APPDATA", "XDG_DATA_HOME", "UNITY_MCP_LOG_DIR", "HOME", "USERPROFILE", "TEMP", "TMP"):
        env[key] = str(owned)
    env["UNITY_MCP_DISABLE_TELEMETRY"] = "true"
    env["UNITY_MCP_TRANSPORT"] = "http"
    env.pop("PYTEST_CURRENT_TEST", None)
    child = subprocess.run(
        [sys.executable, "-B", __file__, case, str(owned)], env=env,
        capture_output=True, text=True, timeout=30,
    )
    assert child.returncode == 0, child.stdout + child.stderr


async def _scenario(case):
    import asyncio
    import copy
    import socket
    from types import SimpleNamespace

    from fastmcp import Client, FastMCP
    from fastmcp.server.middleware import Middleware
    from core.config import config
    from services.resources import editor_state
    from services.tools import run_tests as tests, preflight
    from transport.plugin_hub import PluginHub

    def denied(*args, **kwargs):
        raise AssertionError("Application network prohibited")

    # The async runtime's standard-library internal socketpair exists before these guards.
    socket.socket.connect = denied
    socket.socket.connect_ex = denied
    socket.create_connection = denied
    config.transport_mode = "http"
    config.http_remote_hosted = False

    class Selected(Middleware):
        async def on_call_tool(self, context, call_next):
            await context.fastmcp_context.set_state("unity_instance", "Selected@fixture")
            return await call_next(context)

    app = FastMCP("preflight-public-contract")
    app.add_middleware(Selected())
    app.tool(name="run_tests")(tests.run_tests)
    receipts = []
    for protocol in ("2026-07-28", "legacy"):
        requests = []
        index = 0
        refresh_started = False
        clock = [0.0]

        # Each protocol's callbacks finish before the next iteration replaces them.
        # pylint: disable=cell-var-from-loop
        async def sleep(delay):
            clock[0] += delay
            await asyncio.sleep(0)

        # Narrow clock/sleep boundary leaves SDK scheduling on the actual asyncio runtime.
        preflight.time = SimpleNamespace(monotonic=lambda: clock[0])
        preflight.asyncio = SimpleNamespace(sleep=sleep)
        dirty = case in ("known_tests_dirty", "refresh_error_blocks", "refresh_exception_blocks", "dirty_assets_ready", "dirty_script_compiles")
        async def external_changes(instance):
            return {"external_changes_dirty": dirty}
        editor_state.external_changes_scanner.update_and_get_async = external_changes

        async def send(instance, command, params, **kwargs):
            nonlocal index, refresh_started
            assert instance == "Selected@fixture"
            requests.append({"instance": instance, "command": command, "params": copy.deepcopy(params)})
            if command == "get_project_info":
                return {"success": False, "error": "fixture no project path"}
            if command == "get_editor_state":
                if case == "unknown_state":
                    return {"success": False, "error": "fixture status unavailable", "hint": "retry"}
                if case == "malformed_state":
                    return {"success": True, "data": {"compilation": ["fixture malformed"]}}
                compiling = index == 0 and case in ("tests_start_during_compile", "compile_finishes_ready", "requires_no_tests_false")
                if case == "dirty_script_compiles" and refresh_started and index < 3:
                    compiling = True
                running = case == "known_tests_dirty" or (case in ("tests_start_during_compile", "requires_no_tests_false") and index > 0)
                index += 1
                data = {"compilation": {"is_compiling": compiling, "is_domain_reload_pending": False}, "tests": {"is_running": running}}
                if case == "stale_idle":
                    data["observed_at_unix_ms"] = 1
                return {"success": True, "data": data}
            if command == "refresh_unity":
                if case == "refresh_exception_blocks":
                    raise RuntimeError("fixture refresh transport exception")
                if case in ("dirty_assets_ready", "dirty_script_compiles"):
                    refresh_started = True
                    return {"success": True, "data": {"refresh_triggered": True}}
                return {"success": False, "error": "tests_running" if case == "known_tests_dirty" else "fixture refresh failure"}
            assert command == "run_tests"
            return {"success": True, "data": {"job_id": "fixture-job", "status": "queued"}}

        PluginHub.send_command_for_instance = send
        # pylint: enable=cell-var-from-loop
        if case == "requires_no_tests_false":
            async def get_state(key):
                assert key == "unity_instance"
                return "Selected@fixture"
            response = await preflight.preflight(SimpleNamespace(get_state=get_state), wait_for_no_compile=True)
            assert response is None
            commands = [r["command"] for r in requests]
            assert commands.count("get_editor_state") == 2 and "run_tests" not in commands
            receipts.append({"protocol": protocol, "requests": requests, "response": None})
            continue

        async with Client(app, mode=protocol) as client:
            arguments = {"clear_stuck": True} if case == "clear_stuck" else (
                {"init_timeout": 2**31} if case == "invalid_init_timeout" else {}
            )
            result = await client.call_tool("run_tests", arguments)
        response = json.loads(result.content[0].text)
        commands = [r["command"] for r in requests]
        if case == "invalid_init_timeout":
            assert response["success"] is False and "init_timeout" in response["error"]
            assert commands == []
        elif case in ("tests_start_during_compile", "known_tests_dirty"):
            assert response["success"] is False and response["error"] == "busy"
            assert response["hint"] == "retry"
            assert response["data"] == {"reason": "tests_running", "retry_after_ms": 5000}
            assert "run_tests" not in commands and "refresh_unity" not in commands
        elif case == "refresh_error_blocks":
            assert response["success"] is False and response["error"] == "fixture refresh failure"
            assert commands.count("refresh_unity") == 1 and "run_tests" not in commands
        elif case == "refresh_exception_blocks":
            assert response["success"] is False and response["error"] == "fixture refresh transport exception"
            assert response["hint"] == "retry"
            assert commands.count("refresh_unity") == 1 and "run_tests" not in commands
        else:
            assert response["success"] is True and commands.count("run_tests") == 1
            if case == "clear_stuck":
                assert commands == ["run_tests"] and requests[0]["params"] == {"clear_stuck": True}
            if case in ("dirty_assets_ready", "dirty_script_compiles"):
                refresh_requests = [r for r in requests if r["command"] == "refresh_unity"]
                assert len(refresh_requests) == 1
                assert refresh_requests[0]["params"] == {
                    "mode": "if_dirty", "scope": "all", "compile": "none", "wait_for_ready": False,
                }
                assert commands.count("get_editor_state") == (4 if case == "dirty_script_compiles" else 2)
                assert commands[-1] == "run_tests"
        receipts.append({"protocol": protocol, "response": response, "requests": requests})
    print(json.dumps({"case": case, "receipts": receipts}))


if __name__ == "__main__":
    import anyio

    Path.home = classmethod(lambda cls: Path(sys.argv[2]))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    anyio.run(_scenario, sys.argv[1])
