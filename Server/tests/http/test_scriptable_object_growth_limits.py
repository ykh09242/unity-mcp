"""Actual SDK dispatch with inert Unity routing; no editor or application sockets."""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys


def _own_environment(owned: Path) -> None:
    owned.mkdir(parents=True, exist_ok=True)
    for name in tuple(os.environ):
        if name.startswith(("UNITY_MCP_", "MCP_")):
            del os.environ[name]
    for name in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_DATA_HOME", "XDG_CONFIG_HOME",
                 "XDG_CACHE_HOME", "TEMP", "TMP", "UNITY_MCP_LOG_DIR", "UNITY_MCP_STATUS_DIR"):
        os.environ[name] = str(owned)
    os.environ["DISABLE_TELEMETRY"] = "1"
    os.environ["UNITY_MCP_SKIP_STARTUP_CONNECT"] = "1"
    Path.home = classmethod(lambda cls: owned)
    original_pair, original_connect, original_bind = socket.socketpair, socket.socket.connect, socket.socket.bind

    def denied(*args, **kwargs):
        raise AssertionError("Application network prohibited in ScriptableObject fixture")

    def internal_pair(*args, **kwargs):
        socket.socket.connect, socket.socket.bind = original_connect, original_bind
        try:
            return original_pair(*args, **kwargs)
        finally:
            socket.socket.connect = socket.socket.bind = denied

    socket.socket.connect = socket.socket.connect_ex = socket.socket.bind = denied
    socket.create_connection = socket.getaddrinfo = socket.gethostbyname = socket.gethostbyname_ex = denied
    socket.socketpair = internal_pair


def _sdk_proof(owned: Path, source_override: Path | None = None) -> dict:
    _own_environment(owned)
    root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(root / "Server/src"))
    import asyncio
    import hashlib
    import importlib
    import importlib.abc
    import importlib.util

    if source_override is not None:
        class ExactSource(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == "services.tools.manage_scriptable_object":
                    return importlib.util.spec_from_file_location(fullname, source_override)
                return None
        sys.meta_path.insert(0, ExactSource())

    from fastmcp import FastMCP, Client
    from core.logging_decorator import log_execution
    from core.telemetry_decorator import telemetry_tool
    module = importlib.import_module("services.tools.manage_scriptable_object")
    calls: list[object] = []

    async def instance(ctx):
        calls.append("instance")
        return None

    async def send(fn, selected, command, params):
        calls.append(params)
        return {"success": False, "message": "controlled Unity response"}

    module.get_unity_instance_from_context = instance
    module.send_with_unity_instance = send
    server = FastMCP("scriptable-object-growth-contract")
    wrapped = log_execution("manage_scriptable_object", "Tool")(module.manage_scriptable_object)
    server.tool(name="manage_scriptable_object")(telemetry_tool("manage_scriptable_object")(wrapped))
    invalid = [
        {"path": "items", "op": "array_resize", "value": 2_147_483_648},
        {"path": "items", "op": "array_resize", "value": "9223372036854775808"},
        {"path": "items.Array.size", "value": -1},
        {"path": "items", "op": "array_resize", "value": True},
        {"path": "items", "op": "array_resize", "value": 1e100},
        {"path": "items[2147483647]", "value": 0},
        {"path": "items[-1]", "value": 0},
        {"path": "outer[1].inner[9999999999999999999999]", "value": 0},
    ]
    valid = [
        {"path": "items", "op": "array_resize", "value": 1_500_000},
        {"path": "items[1499999]", "value": 0},
        {"path": "items", "op": "array_resize", "value": "4"},
        {"path": "items", "op": "array_resize", "value": 4.75},
        {"path": "items", "op": "array_resize", "value": "4.75"},
        {"path": "items", "op": "array_resize", "value": "4e0"},
        {"path": "nested", "value": {"numbers": [3, 4]}},
    ]
    records = []

    async def main():
        for protocol in ("2026-07-28", "legacy"):
            async with Client(server, mode=protocol) as client:
                for allowed, patches in ((False, invalid), (True, valid)):
                    for dry_run in (False, True):
                        for patch in patches:
                            before = len(calls)
                            response = await client.call_tool("manage_scriptable_object", {
                                "action": "modify", "target": '{"path":"Assets/OwnedFixture.asset"}',
                                "patches": json.dumps([patch]), "dry_run": dry_run,
                            })
                            data = response.structured_content
                            forwarded = len(calls) == before + 2 and calls[-1].get("patches") == [patch]
                            rejected = len(calls) == before and data.get("success") is False and "Int32" in data.get("message", "")
                            records.append({"protocol": protocol, "dry_run": dry_run, "patch": patch,
                                            "allowed": allowed, "passed": forwarded if allowed else rejected,
                                            "calls": len(calls) - before, "response": data})
    asyncio.run(main())
    source = source_override or root / "Server/src/services/tools/manage_scriptable_object.py"
    return {"count": len(records), "passed": sum(r["passed"] for r in records), "records": records,
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "fixture_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


def test_registered_scriptable_object_array_numeric_domains(tmp_path):
    result = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()), "--sdk-child", str(tmp_path / "sdk")],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


if __name__ == "__main__":
    assert sys.argv[1] == "--sdk-child"
    receipt = _sdk_proof(Path(sys.argv[2]), Path(sys.argv[3]) if len(sys.argv) > 3 else None)
    print(json.dumps(receipt))
    if len(sys.argv) <= 3:
        assert receipt["passed"] == receipt["count"]
