"""Real registered SDK capture requests with isolated, inert Editor boundaries."""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from typing import TypeAlias, TypedDict

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


class CaptureCase(TypedDict):
    tool: str
    arguments: dict[str, JsonValue]
    allowed: bool


def _capture_cases() -> list[CaptureCase]:
    """Use compact scalar attacks and small controls; no capture is performed."""
    ui_rejections = [
        {"width": 0}, {"height": -1}, {"width": 8193, "height": 1},
        {"width": 1, "height": 8193}, {"width": 8192, "height": 4097},
        {"width": 2147483647, "height": 2147483647},
        {"max_resolution": 8193}, {"max_resolution": -1},
    ]
    camera_rejections = [
        {"screenshot_super_size": 0}, {"screenshot_super_size": -1},
        {"screenshot_super_size": 5}, {"screenshot_super_size": 2147483647},
        {"screenshot_super_size": "5"}, {"max_resolution": 8193},
        {"max_resolution": "8193"},
    ]
    orbit_rejections = [
        {"orbit_angles": 0}, {"orbit_angles": 37},
        {"orbit_angles": 2147483647}, {"orbit_elevations": []},
        {"orbit_elevations": [0.0] * 17},
        {"orbit_elevations": json.dumps([0.0] * 17)},
        {"orbit_angles": 36, "orbit_elevations": [0.0] * 4},
        {"orbit_angles": 9, "orbit_elevations": [0.0] * 15},
        {"orbit_elevations": "[NaN]"}, {"orbit_elevations": "[Infinity]"},
        {"orbit_elevations": "[1e500]"}, {"orbit_elevations": "[true]"},
        {"orbit_elevations": "[" + "9" * 400 + "]"},
    ]
    cases: list[CaptureCase] = [
        {"tool": "manage_ui", "arguments": {"action": "render_ui", **values}, "allowed": False}
        for values in ui_rejections
    ]
    cases.extend(
        {"tool": "manage_camera", "arguments": {"action": action, **values}, "allowed": False}
        for action in ("screenshot", "screenshot_multiview")
        for values in camera_rejections
    )
    cases.extend(
        {"tool": "manage_camera", "arguments": {"action": "screenshot", "batch": batch, **values}, "allowed": False}
        for batch in ("orbit", "ORBIT")
        for values in orbit_rejections
    )
    ui_controls = [
        {}, {"width": 1, "height": 1}, {"width": 8192, "height": 4096},
        {"width": 4096, "height": 8192}, {"width": 1, "height": 8192, "max_resolution": 8192},
        {"width": None, "height": None, "max_resolution": None},
        {"include_image": False, "width": 2, "height": 2},
        {"max_resolution": 0},
    ]
    cases.extend(
        {"tool": "manage_ui", "arguments": {"action": "render_ui", **values}, "allowed": True}
        for values in ui_controls
    )
    camera_controls = [
        {}, {"screenshot_super_size": 1}, {"screenshot_super_size": "4"},
        {"max_resolution": 8192}, {"include_image": False},
        {"capture_source": "scene_view", "screenshot_super_size": 1},
        {"properties": {"superSize": 2147483647, "orbitElevations": [0.0] * 17}},
        {"properties": '{"superSize":2147483647,"maxResolution":2147483647}'},
    ]
    cases.extend(
        {"tool": "manage_camera", "arguments": {"action": action, **values}, "allowed": True}
        for action in ("screenshot", "screenshot_multiview")
        for values in camera_controls
    )
    orbit_controls = [
        {}, {"orbit_angles": 36, "orbit_elevations": [0.0] * 3},
        {"orbit_angles": 8, "orbit_elevations": [0.0] * 16},
        {"orbit_angles": "1", "orbit_elevations": "[0, 30]", "max_resolution": "8192"},
    ]
    cases.extend(
        {"tool": "manage_camera", "arguments": {"action": "screenshot", "batch": "orbit", **values}, "allowed": True}
        for values in orbit_controls
    )
    cases.extend([
        {"tool": "manage_camera", "arguments": {"action": "screenshot_multiview", "batch": "orbit", "orbit_angles": 37, "orbit_elevations": [0.0] * 17}, "allowed": True},
        {"tool": "manage_camera", "arguments": {"action": "set_lens", "properties": {"fieldOfView": 60}, "screenshot_super_size": 2147483647}, "allowed": True},
        {"tool": "manage_ui", "arguments": {"action": "ping", "width": 2147483647}, "allowed": True},
    ])
    return cases


def _own_child_environment(owned: Path) -> None:
    """Contain product reads/writes and forbid sockets before product imports."""
    owned.mkdir(parents=True, exist_ok=True)
    for name in tuple(os.environ):
        if name.startswith("UNITY_MCP_"):
            del os.environ[name]
    for name in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "TEMP", "TMP", "UNITY_MCP_LOG_DIR", "UNITY_MCP_STATUS_DIR"):
        os.environ[name] = str(owned)
    os.environ["DISABLE_TELEMETRY"] = "1"
    os.environ["UNITY_MCP_SKIP_STARTUP_CONNECT"] = "1"
    Path.home = classmethod(lambda cls: owned)

    def denied(*args, **kwargs):
        raise AssertionError("Application network is prohibited in capture fixtures")

    original_connect = socket.socket.connect
    original_bind = socket.socket.bind
    original_pair = socket.socketpair

    def internal_pair(*args, **kwargs):
        # Windows asyncio uses this verified stdlib helper for its wakeup pipe.
        socket.socket.connect, socket.socket.bind = original_connect, original_bind
        try:
            return original_pair(*args, **kwargs)
        finally:
            socket.socket.connect = socket.socket.bind = denied

    socket.socketpair = internal_pair
    socket.socket.connect = socket.socket.connect_ex = socket.socket.bind = denied
    socket.create_connection = socket.getaddrinfo = socket.gethostbyname = socket.gethostbyname_ex = denied


async def _exercise_registered_sdk(owned: Path) -> None:
    """Call the production registration and wrappers using installed FastMCP."""
    from fastmcp import Client, FastMCP
    from services.tools import register_all_tools

    camera = importlib.import_module("services.tools.manage_camera")
    ui = importlib.import_module("services.tools.manage_ui")
    refresh = importlib.import_module("services.tools.refresh_unity")
    transport = importlib.import_module("transport.unity_transport")
    original_resolve = camera.get_unity_instance_from_context
    calls: list[str] = []
    sent: list[dict[str, JsonValue]] = []

    async def resolve(ctx):
        calls.append("resolve")
        # Keep the actual context reader; its selected-instance default is None.
        return await original_resolve(ctx)

    async def send(fn, instance, command, params, **kwargs):
        assert instance is None and command in ("manage_ui", "manage_camera")
        calls.append("send")
        sent.append(params.copy())
        return {"success": True, "data": {"count": 0, "enabled": False, "reference": None}}

    async def readiness(ctx, timeout_s=30.0):
        calls.append("ready")
        return True, 0.0

    camera.get_unity_instance_from_context = ui.get_unity_instance_from_context = resolve
    camera.send_with_unity_instance = ui.send_with_unity_instance = send
    transport.send_with_unity_instance = send
    refresh.wait_for_editor_ready = readiness
    server = FastMCP("render-capture-resource-limits")
    register_all_tools(server)
    failures: list[str] = []
    rows = []
    for mode in ("auto", "legacy"):
        async with Client(server, mode=mode) as client:
            names = {tool.name for tool in await client.list_tools()}
            assert {"manage_ui", "manage_camera"}.issubset(names)
            for index, case in enumerate(_capture_cases()):
                calls.clear()
                sent.clear()
                result = await client.call_tool(case["tool"], case["arguments"])
                success = result.structured_content.get("success")
                expected_calls = []
                if case["allowed"]:
                    expected_calls = ["resolve", "send"]
                    if case["tool"] == "manage_ui" and case["arguments"]["action"] == "render_ui":
                        expected_calls.append("ready")
                passed = success is case["allowed"] and calls == expected_calls
                if case["allowed"] and result.structured_content.get("data") != {"count": 0, "enabled": False, "reference": None}:
                    passed = False
                properties = case["arguments"].get("properties")
                if properties is not None and (not sent or sent[0].get("properties") != properties):
                    passed = False
                if not passed:
                    failures.append(f"{mode} case {index}: allowed={case['allowed']} success={success} calls={calls}")
                rows.append({"mode": mode, "index": index, "tool": case["tool"], "allowed": case["allowed"], "passed": passed, "calls": calls.copy()})
    report = {"runtime": sys.version.split()[0], "checks": len(rows), "failures": failures, "results": rows}
    (owned / "sdk-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"checks": len(rows), "passed": len(rows) - len(failures), "failed": len(failures)}))
    assert not failures, "\n".join(failures)


def test_render_capture_limits_at_registered_sdk(tmp_path: Path) -> None:
    """The permanent test always configures ownership before importing product code."""
    owned = tmp_path / "capture-sdk"
    result = subprocess.run(
        [sys.executable, "-B", str(Path(__file__).resolve()), "--sdk-child", str(owned)],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((owned / "sdk-report.json").read_text(encoding="utf-8"))
    assert report["checks"] > 100 and report["failures"] == []


if __name__ == "__main__":
    assert len(sys.argv) == 3 and sys.argv[1] == "--sdk-child"
    child_root = Path(sys.argv[2]).resolve()
    _own_child_environment(child_root)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    import anyio

    anyio.run(_exercise_registered_sdk, child_root)
