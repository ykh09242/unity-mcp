"""Actual registered SDK component admission, independently of collection-time stubs."""

import json
import os
from pathlib import Path
import subprocess
import sys

from .test_ui_snapshot_components import toolkit_snapshot


SDK_PROGRAM = r"""
import copy
import importlib
import json
import os
from pathlib import Path
import anyio
from fastmcp import Client, FastMCP
from core.logging_decorator import log_execution
from core.telemetry_decorator import telemetry_tool
from services.registry import get_registered_tools

module = importlib.import_module("services.tools.capture_ui_snapshot")
assert Path(module.__file__).resolve() == Path(os.environ["UI13_EXPECTED_MODULE"]).resolve()
source = json.loads(os.environ["UI13_NATIVE"])
native = {"success": True, "data": source}
sent, ready_calls = [], []

async def ready(*args, **kwargs):
    ready_calls.append(True)
    return None

async def instance(*args, **kwargs):
    return "Editor@ui13-sdk"

async def send(*args, **kwargs):
    sent.append(args)
    return copy.deepcopy(native)

module.preflight = ready
module.get_unity_instance_from_context = instance
module.send_with_unity_instance = send
server = FastMCP("ui13-component-selection")
metadata = next(item for item in get_registered_tools() if item["name"] == "capture_ui_snapshot")
server.tool(name="capture_ui_snapshot", description=metadata["description"], **metadata["kwargs"])(
    telemetry_tool("capture_ui_snapshot")(
        log_execution("capture_ui_snapshot", "Tool")(module.capture_ui_snapshot)))

async def main():
    global native
    for mode in ("2026-07-28", "legacy"):
        async with Client(server, mode=mode) as client:
            tool = next(t for t in await client.list_tools() if t.name == "capture_ui_snapshot")
            field = tool.inputSchema["properties"]["component_type"]
            assert field["type"] == "string"
            assert field["enum"] == ["auto", "ui_document", "panel_renderer"]
            assert "component_type" not in tool.inputSchema.get("required", [])
            base = {"framework": "uitoolkit", "target": "Canvas"}
            for framework, selector in (("uitoolkit", None), ("uitoolkit", True),
                    ("uitoolkit", "UIDocument"), ("uitoolkit", "unknown"),
                    ("ugui", "auto"), ("ugui", "ui_document"), ("ugui", "panel_renderer")):
                before = (len(sent), len(ready_calls))
                try:
                    rejected = await client.call_tool("capture_ui_snapshot",
                        {"framework": framework, "target": "Canvas", "component_type": selector})
                except Exception:
                    pass  # The SDK rejects invalid public input before calling the tool.
                else:
                    assert rejected.structured_content["success"] is False
                assert (len(sent), len(ready_calls)) == before
            for version in (2, 3):
                for selector, actual in (("auto", "ui_document"), ("auto", "panel_renderer"),
                        ("ui_document", "ui_document"), ("panel_renderer", "panel_renderer")):
                    artifact = copy.deepcopy(source)
                    artifact["evidence"]["component_type"] = actual
                    request = {**base, "component_type": selector}
                    if version == 3:
                        artifact["schema_version"] = 3
                        artifact["evidence"]["identity_method"] = "explicit_path_map_v1"
                        artifact["nodes"][1]["id"] = artifact["target"] + "/@id:item-A"
                        request["identity_map"] = [{"element_path": "/0", "id": "item-A"}]
                    native = {"success": True, "data": artifact}
                    result = await client.call_tool("capture_ui_snapshot", request)
                    assert result.structured_content == native
                    assert sent[-1][1:] == ("Editor@ui13-sdk", "capture_ui_snapshot", {**request, "max_nodes": 200})
            for wrong in (None, "panel_renderer"):
                artifact = copy.deepcopy(source)
                if wrong is None:
                    artifact["evidence"].pop("component_type")
                else:
                    artifact["evidence"]["component_type"] = wrong
                native = {"success": True, "data": artifact}
                rejected = await client.call_tool("capture_ui_snapshot", {**base, "component_type": "ui_document"})
                assert rejected.structured_content["success"] is False
            artifact = copy.deepcopy(source)
            artifact["evidence"].pop("component_type")
            native = {"success": True, "data": artifact}
            legacy = await client.call_tool("capture_ui_snapshot", base)
            assert legacy.structured_content == native
            assert "component_type" not in sent[-1][3]
            print("UI13_SDK", mode, "PASS")
    print("UI13_IMPORT", module.__file__)

anyio.run(main)
"""


def test_registered_sdk_rejects_null_and_preserves_component_provenance_when_dispatched(tmp_path):
    # Given
    server = Path(__file__).resolve().parents[1]
    environment = {
        **os.environ,
        "PYTHONPATH": str(server / "src"),
        "APPDATA": str(tmp_path),
        "XDG_DATA_HOME": str(tmp_path),
        "UNITY_MCP_DISABLE_TELEMETRY": "true",
        "UI13_EXPECTED_MODULE": str(server / "src/services/tools/capture_ui_snapshot.py"),
        "UI13_NATIVE": json.dumps(toolkit_snapshot()),
    }
    # When
    result = subprocess.run(
        [sys.executable, "-B", "-c", SDK_PROGRAM],
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )
    # Then
    assert result.returncode == 0, result.stdout + result.stderr
    assert "UI13_SDK 2026-07-28 PASS" in result.stdout
    assert "UI13_SDK legacy PASS" in result.stdout
