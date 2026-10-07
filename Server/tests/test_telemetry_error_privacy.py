"""Public SDK telemetry errors contain metadata only, in an isolated process."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


PROGRAM = r"""
import asyncio
import contextlib
import copy
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

home = Path(os.environ["HOME"])
Path.home = classmethod(lambda cls: home)
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
loaded_inputs = []
if os.environ.get("G_TELEMETRY_BASELINE") == "1":
    root = Path(os.environ["PYTHONPATH"]).parents[1]
    for name in ("core.telemetry", "core.telemetry_decorator"):
        relative = "Server/src/" + name.replace(".", "/") + ".py"
        source = subprocess.check_output(["git", "show", "8947353997a2534e916c148081386b21bfe8f78d:" + relative], cwd=root)
        spec = importlib.util.spec_from_loader(name, loader=None, origin=str(root / relative))
        module = importlib.util.module_from_spec(spec)
        module.__file__ = str(root / relative)
        sys.modules[name] = module
        exec(compile(source, module.__file__, "exec"), module.__dict__)
        import hashlib
        loaded_inputs.append({"path": relative, "sha256": hashlib.sha256(source).hexdigest(), "source": "exact pinned baseline"})
import anyio
import httpx
from fastmcp import Client, FastMCP
from core.config import config
import core.telemetry as telemetry
from core.telemetry_decorator import telemetry_tool, telemetry_resource
import services.tools as tools
import services.resources as resources
import importlib
editor = importlib.import_module("services.tools.manage_editor")
prefab = importlib.import_module("services.resources.prefab_stage")

SENSITIVE = (r"C:\synthetic\private\project.asset", "https://fixture.example/private?token=fixture-secret", "Bearer fixture-bearer", "api_key=fixture-key", "caller-private-input")
LABELS = {"Exception", "ValueError", "TypeError", "RuntimeError", "OSError", "FileNotFoundError", "PermissionError", "ConnectionError", "TimeoutError"}
queued, queued_records, sent, cases = [], [], [], []
client_type = httpx.Client
def sink(request):
    sent.append(json.loads(request.content))
    return httpx.Response(200)
def safe_records(records):
    text = json.dumps(records)
    return all(value not in text for value in SENSITIVE) and all(
        "error" not in item or item["error"] in LABELS for item in records)
def check(name, condition):
    cases.append({"case": name, "passed": bool(condition)})

async def main():
    config.telemetry_enabled = True
    config.telemetry_endpoint = "https://owned.example/telemetry"
    config.transport_mode = "stdio"
    config.http_remote_hosted = False
    telemetry.reset_telemetry()
    # Install the actual HTTPX MockTransport before enabling any collector.
    with patch.object(telemetry.httpx, "Client", lambda **kwargs: client_type(transport=httpx.MockTransport(sink), **kwargs)):
        for key in ("DISABLE_TELEMETRY", "UNITY_MCP_DISABLE_TELEMETRY", "MCP_DISABLE_TELEMETRY"):
            os.environ.pop(key, None)
        collector = telemetry.get_telemetry()
        put = collector._queue.put_nowait
        def capture(record):
            queued.append(copy.deepcopy(record.data))
            queued_records.append(record)
            return put(record)
        collector._queue.put_nowait = capture
        app = FastMCP("telemetry-privacy")
        # Load only the two actual registered producers; registration itself is production.
        with patch.object(tools, "discover_modules", lambda *args: iter(())), patch.object(resources, "discover_modules", lambda *args: iter(())):
            tools.register_all_tools(app)
            resources.register_all_resources(app)
        try:
            async with Client(app, mode=sys.argv[1]) as client:
                for index, secret in enumerate(SENSITIVE):
                    async def failing_tool_context(*args, **kwargs):
                        raise ValueError(secret)
                    async def failing_resource_context(*args, **kwargs):
                        raise FileNotFoundError(secret)
                    start = len(queued)
                    with patch.object(editor, "get_unity_instance_from_context", failing_tool_context), patch.object(prefab, "get_unity_instance_from_context", failing_resource_context):
                        for call in (lambda: client.call_tool("manage_editor", {"action": "play", "tool_name": secret}), lambda: client.read_resource("mcpforunity://editor/prefab-stage")):
                            try:
                                await call()
                            except Exception:
                                pass
                            else:
                                raise AssertionError("The inert producer must raise")
                    collector._queue.join()
                    subset = queued[start:]
                    check("public_errors_" + str(index), len(subset) == 2 and safe_records(subset)
                        and {item.get("error") for item in subset} == {"ValueError", "FileNotFoundError"})
                async def selected(*args, **kwargs):
                    return "Owned@fixturehash"
                async def resource_success(*args, **kwargs):
                    return {"success": True, "data": {"isOpen": False}}
                with patch.object(editor, "get_unity_instance_from_context", selected), patch.object(prefab, "get_unity_instance_from_context", selected), patch.object(prefab, "send_with_unity_instance", resource_success):
                    start = len(queued)
                    result = await client.call_tool("manage_editor", {"action": "telemetry_status"})
                    assert json.loads(result.content[0].text)["success"] is True
                    await client.read_resource("mcpforunity://editor/prefab-stage")
                    collector._queue.join()
                    check("public_success_metadata", any(item.get("sub_action") == "telemetry_status" and item["success"] for item in queued[start:])
                        and any(item.get("resource_name") == "editor_prefab_stage" and item["success"] for item in queued[start:]))
            for decorator, label in ((telemetry_tool, "sync_tool"), (telemetry_resource, "sync_resource")):
                error = PermissionError(SENSITIVE[0])
                def fail():
                    raise error
                start = len(queued)
                try:
                    decorator(label)(fail)()
                except PermissionError as caught:
                    check(label, caught is error and queued[start]["error"] == "PermissionError" and safe_records(queued[start:]))
            class NoStringError(ValueError):
                def __str__(self):
                    raise RuntimeError("Exception formatting must not be called")
            original = NoStringError()
            def no_format():
                raise original
            try:
                telemetry_tool("no_format")(no_format)()
            except Exception as caught:
                check("exception_identity_no_format", caught is original and queued[-1].get("error") == "ValueError")
            secret_class = type(SENSITIVE[4], (Exception,), {})
            telemetry.record_resource_usage("direct_resource", False, 2, secret_class(SENSITIVE[2]))
            check("custom_class_generic", queued[-1].get("error") == "Exception")
            for error_type in (FileNotFoundError, PermissionError, ConnectionError, TimeoutError, ValueError, TypeError, OSError, RuntimeError, Exception):
                telemetry.record_tool_usage("direct_class", False, 2, error_type(SENSITIVE[0]))
                check("class_" + error_type.__name__, queued[-1].get("error") == error_type.__name__ and safe_records(queued[-1:]))
            for producer in (lambda: telemetry.record_tool_usage("direct_tool", False, 3, SENSITIVE[1]),
                             lambda: telemetry.record_resource_usage("direct_resource", False, 4, SENSITIVE[2]),
                             lambda: telemetry.record_failure("connection", SENSITIVE[3], {"error": SENSITIVE[4], "attempt": 2}),
                             lambda: telemetry.record_telemetry(telemetry.RecordType.UNITY_CONNECTION, {"error": SENSITIVE[0], "connected": False})):
                producer()
                check("direct_error_" + str(len(cases)), safe_records(queued[-1:]) and queued[-1]["error"] == "Exception")
            data = {"error": SENSITIVE[0], "attempt": 1}
            collector.record(telemetry.RecordType.FAILURE, data)
            data["error"] = SENSITIVE[1]
            check("queue_detached", queued_records[-1].data is not data and queued_records[-1].data["error"] == "Exception")
            collector._queue.join()
            check("httpx_serialized", bool(sent) and safe_records([item["data"] for item in sent]))
            # Fallback Request/JSON serialization remains actual; only urlopen is inert.
            class Response:
                def __enter__(self): return self
                def __exit__(self, *args): pass
                def getcode(self): return 200
            fallback = []
            def urlopen(request, **kwargs):
                fallback.append(json.loads(request.data))
                return Response()
            manual = telemetry.TelemetryRecord(telemetry.RecordType.FAILURE, 1, "synthetic-uuid", "synthetic-session", {"error": SENSITIVE[2], "attempt": 1})
            collector._send_telemetry(manual)
            check("httpx_manual_record", sent[-1]["data"]["attempt"] == 1 and safe_records([sent[-1]["data"]]))
            with patch.object(telemetry, "httpx", None), patch.object(urllib.request, "urlopen", urlopen):
                collector._send_telemetry(manual)
            check("urllib_manual_record", len(fallback) == 1 and safe_records([fallback[0]["data"]]))
        finally:
            telemetry.reset_telemetry()
        for optout in ("config", "DISABLE_TELEMETRY", "UNITY_MCP_DISABLE_TELEMETRY", "MCP_DISABLE_TELEMETRY"):
            if optout == "config":
                config.telemetry_enabled = False
            else:
                os.environ[optout] = "1"
            count = len(sent)
            async with Client(app, mode=sys.argv[1]) as client:
                with patch.object(editor, "get_unity_instance_from_context", selected), patch.object(prefab, "get_unity_instance_from_context", selected), patch.object(prefab, "send_with_unity_instance", resource_success):
                    await client.call_tool("manage_editor", {"action": "telemetry_status"})
                    await client.read_resource("mcpforunity://editor/prefab-stage")
            telemetry.record_failure("disabled", SENSITIVE[3])
            disabled = telemetry.get_telemetry()
            check(optout, not disabled.config.enabled and disabled._queue.empty() and disabled._worker is None and len(sent) == count)
            telemetry.reset_telemetry()
            config.telemetry_enabled = True
            os.environ.pop(optout, None)
    print(json.dumps({"protocol": sys.argv[1], "cases": cases, "queued": len(queued), "serialized": len(sent),
        "loadedInputs": loaded_inputs, "versions": {name: version(name) for name in ("fastmcp", "mcp", "httpx")}}))
    assert all(case["passed"] for case in cases), [case["case"] for case in cases if not case["passed"]]
anyio.run(main)
"""


@pytest.mark.parametrize("protocol", ["2026-07-28", "legacy"])
def test_public_telemetry_error_channel_is_metadata_only(tmp_path, protocol):
    env = {key: value for key, value in os.environ.items() if not key.startswith("UNITY_MCP_")}
    for key in (
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "XDG_DATA_HOME",
        "TEMP",
        "TMP",
        "UNITY_MCP_LOG_DIR",
    ):
        env[key] = str(tmp_path)
    env["DISABLE_TELEMETRY"] = "1"
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    env.pop("PYTEST_CURRENT_TEST", None)
    result = subprocess.run(
        [sys.executable, "-B", "-c", PROGRAM, protocol],
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )
    (tmp_path / "public-telemetry.json").write_text(result.stdout, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
