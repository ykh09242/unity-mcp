"""Registered tool actions are fixed telemetry dimensions, never caller text."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


PROGRAM = r'''
import copy
import hashlib
import importlib
import importlib.util
from importlib.metadata import version
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import urllib.request
from unittest.mock import patch
from types import SimpleNamespace
Path.home = classmethod(lambda cls: Path(os.environ["HOME"]))
def denied(*args, **kwargs):
    raise AssertionError("Application network prohibited")
original_connect, original_bind, original_pair = socket.socket.connect, socket.socket.bind, socket.socketpair
def internal_pair(*args, **kwargs):
    socket.socket.connect, socket.socket.bind = original_connect, original_bind
    try:
        return original_pair(*args, **kwargs)
    finally:
        socket.socket.connect = socket.socket.bind = denied
socket.socketpair = internal_pair
socket.socket.connect = socket.socket.connect_ex = socket.socket.bind = denied
socket.create_connection = socket.getaddrinfo = denied
loaded = []
if os.environ.get("G_ACTION_BASELINE") == "1":
    root = Path(os.environ["PYTHONPATH"]).parents[1]
    for name in ("core.telemetry", "core.telemetry_decorator"):
        relative = "Server/src/" + name.replace(".", "/") + ".py"
        source = subprocess.check_output(["git", "show", "4678db509fbbe748415624572db9d96b9718c54d:" + relative], cwd=root)
        spec = importlib.util.spec_from_loader(name, loader=None, origin=str(root / relative))
        module = importlib.util.module_from_spec(spec)
        module.__file__ = str(root / relative)
        sys.modules[name] = module
        exec(compile(source, module.__file__, "exec"), module.__dict__)
        loaded.append({"path": relative, "sha256": hashlib.sha256(source).hexdigest()})
import anyio
import httpx
from fastmcp import Client, FastMCP
from core.config import config
import core.telemetry as telemetry
import services.tools as tools
camera = importlib.import_module("services.tools.manage_camera")
editor = importlib.import_module("services.tools.manage_editor")
physics = importlib.import_module("services.tools.manage_physics")
custom = importlib.import_module("services.custom_tool_service")
from models.models import ToolDefinitionModel, ToolParameterModel
queued, records, sent, cases, dispatches = [], [], [], [], []
SECRETS = (r"C:\synthetic\private\project.asset", "https://fixture.example/private?token=fixture-secret", "Bearer fixture-bearer", "api_key=fixture-key", "caller-private-input", "caller-private-input" * 4096)
client_type = httpx.Client
def sink(request):
    sent.append(json.loads(request.content))
    return httpx.Response(200)
def check(name, condition):
    cases.append({"case": name, "passed": bool(condition)})
def clean(data):
    action = data.get("sub_action")
    return action is None or action in ({"ping"} if data.get("tool_name") in {"manage_camera", "manage_physics"} else {"telemetry_status"})

async def main():
    config.telemetry_enabled = True
    config.telemetry_endpoint = "https://owned.example/telemetry"
    config.transport_mode = "stdio"
    config.http_remote_hosted = False
    async def selected(*args, **kwargs):
        return "Owned@fixturehash"
    async def unity(sender, instance, command, params, **kwargs):
        dispatches.append({"instance": instance, "command": command, "params": params})
        return {"success": True, "data": {"fixture": True}}
    app = FastMCP("telemetry-action-privacy")
    with patch.object(tools, "discover_modules", lambda *args: iter(())):
        tools.register_all_tools(app)
    service = custom.CustomToolService(app)
    definition = ToolDefinitionModel(name="caller_custom", parameters=[ToolParameterModel(name="action", type="string", required=False, default_value=SECRETS[4])])
    service._register_project_tools("fixturehash", [definition], project_hash="fixturehash")
    service.register_global_tools([definition])
    # Caller definitions cannot replace an existing built-in action contract.
    service.register_global_tools([ToolDefinitionModel(name="manage_camera", parameters=definition.parameters)])
    with patch.object(telemetry.httpx, "Client", lambda **kwargs: client_type(transport=httpx.MockTransport(sink), **kwargs)), \
         patch.object(camera, "get_unity_instance_from_context", selected), patch.object(camera, "send_with_unity_instance", unity), \
         patch.object(editor, "get_unity_instance_from_context", selected), \
         patch.object(physics, "get_unity_instance_from_context", selected), patch.object(physics, "send_with_unity_instance", unity), \
         patch.object(custom, "get_unity_instance_from_context", selected), patch.object(custom, "send_with_unity_instance", unity), \
         patch.object(custom, "get_unity_connection_pool", lambda: SimpleNamespace(discover_all_instances=lambda: [])):
        for key in ("DISABLE_TELEMETRY", "UNITY_MCP_DISABLE_TELEMETRY", "MCP_DISABLE_TELEMETRY"):
            os.environ.pop(key, None)
        collector = telemetry.get_telemetry()
        put = collector._queue.put_nowait
        def capture(record):
            queued.append(copy.deepcopy(record.data))
            records.append(record)
            put(record)
        collector._queue.put_nowait = capture
        try:
            async with Client(app, mode=sys.argv[1]) as client:
                for index, secret in enumerate(SECRETS):
                    start, calls = len(queued), len(dispatches)
                    result = await client.call_tool("manage_camera", {"action": secret})
                    assert json.loads(result.content[0].text)["success"] is False
                    collector._queue.join()
                    data = queued[start:]
                    check("invalid_public_" + str(index), bool(data) and all("sub_action" not in item for item in data)
                        and len(dispatches) == calls and all(secret not in json.dumps(item["data"]) for item in sent))
                for tool, action, expected in (("manage_camera", "PING", "ping"), ("manage_editor", "telemetry_status", "telemetry_status"), ("manage_physics", "ping", "ping")):
                    start = len(queued)
                    result = await client.call_tool(tool, {"action": action})
                    assert json.loads(result.content[0].text)["success"] is True
                    collector._queue.join()
                    check("valid_" + tool, any(item.get("tool_name") == tool and item.get("sub_action") == expected for item in queued[start:]))
                for action in (None, "ping"):
                    start = len(queued)
                    result = await client.call_tool("caller_custom", {} if action is None else {"action": action})
                    assert json.loads(result.content[0].text)["success"] is True
                    collector._queue.join()
                    check("custom_catalog_" + str(action is None), bool(queued[start:]) and all("sub_action" not in item for item in queued[start:]))
                check("builtin_catalog_not_replaced", "manage_camera" not in service._global_tools)
            for tool, action, expected in (("manage_camera", "PING", "ping"), ("manage_editor", "ping", None), ("unknown_tool", "ping", None), ("manage_camera", SECRETS[0], None)):
                telemetry.record_tool_usage(tool, False, 1, sub_action=action)
                check("helper_" + str(len(cases)), queued[-1].get("sub_action") == expected)
            class OpaqueAction:
                def __str__(self): raise AssertionError("No action formatting")
                def __repr__(self): raise AssertionError("No action formatting")
            telemetry.record_tool_usage("manage_camera", False, 1, sub_action=OpaqueAction())
            check("opaque_action", "sub_action" not in queued[-1])
            class HostileString(str):
                def __str__(self): raise AssertionError("No subclass formatting")
                def lower(self): raise AssertionError("No subclass coercion")
            telemetry.record_tool_usage("manage_camera", False, 1, sub_action=HostileString("ping"))
            check("string_subclass", "sub_action" not in queued[-1])
            telemetry.record_telemetry(telemetry.RecordType.TOOL_EXECUTION, {"tool_name": "manage_editor", "sub_action": "ping"})
            check("cross_tool_label_rejected", "sub_action" not in queued[-1])
            telemetry.record_failure("fixture", ValueError(SECRETS[0]), {"tool_name": "manage_camera", "sub_action": SECRETS[1]})
            check("metadata_override_rejected", "sub_action" not in queued[-1] and queued[-1].get("error") == "ValueError")
            original_actions = camera.ALL_ACTIONS[:]
            camera.ALL_ACTIONS.append(SECRETS[4])
            try:
                telemetry.record_tool_usage("manage_camera", False, 1, sub_action=SECRETS[4])
                check("validator_snapshot_immutable", "sub_action" not in queued[-1])
            finally:
                camera.ALL_ACTIONS[:] = original_actions
            data = {"tool_name": "manage_camera", "sub_action": SECRETS[1], "success": False, "duration_ms": 2}
            collector.record(telemetry.RecordType.TOOL_EXECUTION, data)
            data["sub_action"] = SECRETS[2]
            check("queue_detached", records[-1].data is not data and "sub_action" not in records[-1].data)
            collector._queue.join()
            manual = telemetry.TelemetryRecord(telemetry.RecordType.TOOL_EXECUTION, 1, "synthetic-uuid", "synthetic-session", {"tool_name": "manage_camera", "sub_action": "ping", "error": ValueError(SECRETS[3])})
            manual.data["sub_action"] = SECRETS[4]
            collector._send_telemetry(manual)
            check("httpx_mutated_manual", "sub_action" not in sent[-1]["data"] and sent[-1]["data"].get("error") == "ValueError")
            class Response:
                def __enter__(self): return self
                def __exit__(self, *args): pass
                def getcode(self): return 200
            fallback = []
            def urlopen(request, **kwargs):
                fallback.append(json.loads(request.data))
                return Response()
            with patch.object(telemetry, "httpx", None), patch.object(urllib.request, "urlopen", urlopen):
                collector._send_telemetry(manual)
            check("urllib_mutated_manual", len(fallback) == 1 and "sub_action" not in fallback[0]["data"] and fallback[0]["data"].get("error") == "ValueError")
            check("serialized_all", bool(sent) and all(clean(item["data"]) for item in sent))
        finally:
            telemetry.reset_telemetry()
        for optout in ("config", "DISABLE_TELEMETRY", "UNITY_MCP_DISABLE_TELEMETRY", "MCP_DISABLE_TELEMETRY"):
            if optout == "config": config.telemetry_enabled = False
            else: os.environ[optout] = "1"
            count = len(sent)
            async with Client(app, mode=sys.argv[1]) as client:
                await client.call_tool("manage_camera", {"action": SECRETS[0]})
            disabled = telemetry.get_telemetry()
            check("optout_" + optout, not disabled.config.enabled and disabled._queue.empty() and disabled._worker is None and len(sent) == count)
            telemetry.reset_telemetry()
            config.telemetry_enabled = True
            os.environ.pop(optout, None)
    print(json.dumps({"protocol": sys.argv[1], "cases": cases, "queued": len(queued), "serialized": len(sent), "loadedInputs": loaded,
        "versions": {name: version(name) for name in ("fastmcp", "mcp", "httpx")}}))
    assert all(item["passed"] for item in cases), [item["case"] for item in cases if not item["passed"]]
anyio.run(main)
'''


@pytest.mark.parametrize("protocol", ["2026-07-28", "legacy"])
def test_public_action_telemetry_uses_registered_labels(tmp_path, protocol):
    env = {key: value for key, value in os.environ.items() if not key.startswith("UNITY_MCP_")}
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_DATA_HOME", "TEMP", "TMP", "UNITY_MCP_LOG_DIR"):
        env[key] = str(tmp_path)
    env["DISABLE_TELEMETRY"] = "1"
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    env.pop("PYTEST_CURRENT_TEST", None)
    result = subprocess.run([sys.executable, "-B", "-c", PROGRAM, protocol], env=env, capture_output=True, text=True, timeout=90)
    (tmp_path / "public-telemetry.json").write_text(result.stdout, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
