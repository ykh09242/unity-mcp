"""Real registered SDK calls; controlled transport is not native filesystem evidence."""

import os
from pathlib import Path
import subprocess
import sys


PROGRAM = r"""
import copy
import json
import anyio
from fastmcp import FastMCP, Client
from core.config import config
from services import tools
import transport.unity_transport as transport

config.transport_mode = "stdio"
config.http_remote_hosted = False
calls = []
raw = {"success": True, "data": {"change_set_id": "a" * 32, "phase": "prepared", "files": []}}

async def selected(ctx):
    return "Owned@fixture"

async def send(sender, instance, command, params, **kwargs):
    calls.append((instance, command, copy.deepcopy(params)))
    return copy.deepcopy(raw)

tools.get_unity_instance_from_context = selected
transport.send_with_unity_instance = send
server = FastMCP("bounded-script-change-set-contract")
tools.register_all_tools(server)

def unpack(result):
    document = result.structured_content
    if document is None and result.content:
        try:
            document = json.loads(result.content[0].text)
        except (ValueError, AttributeError):
            pass
    return document

async def main():
    global raw
    async with Client(server) as client:
        files = [
            {"path": "Assets/First.cs", "expected_bytes_sha256": "b" * 64,
             "contents": "class First { string value = \"새😀\"; }\r\n"},
            {"path": "Assets/Second.cs", "expected_bytes_sha256": "c" * 64,
             "contents": "class Second {}\n"},
        ]
        result = await client.call_tool("script_change_set", {"action": "prepare", "files": files}, raise_on_error=False)
        document = unpack(result)
        assert document and document.get("success"), repr(result)
        assert calls[-1] == ("Owned@fixture", "manage_script", {"action": "prepare_change_set", "files": files, "validation": "standard"})
        for action, native in (("apply", "apply_change_set"), ("status", "get_change_set"), ("recover", "recover_change_set")):
            response = await client.call_tool("script_change_set", {"action": action, "change_set_id": "a" * 32}, raise_on_error=False)
            assert unpack(response)["success"], repr(response)
            assert calls[-1][2] == {"action": native, "change_set_id": "a" * 32}
        invalid = [
            {"action": "prepare", "files": []},
            {"action": "prepare", "files": files * 9},
            {"action": "prepare", "files": [files[0], files[0]]},
            {"action": "prepare", "files": [{**files[0], "path": "Assets/../Outside.cs"}]},
            {"action": "prepare", "files": [{**files[0], "expected_bytes_sha256": "bad"}]},
            {"action": "prepare", "files": [{**files[0], "contents": "x" * (256 * 1024 + 1)}]},
            {"action": "prepare", "files": [{**files[0], "extra": True}]},
            {"action": "prepare", "files": files, "change_set_id": "a" * 32},
            {"action": "apply", "change_set_id": "../manifest"},
            {"action": "recover", "change_set_id": "a" * 32, "files": files},
        ]
        for payload in invalid:
            before = len(calls)
            response = await client.call_tool("script_change_set", payload, raise_on_error=False)
            document = unpack(response)
            assert response.is_error or document and document.get("success") is False, repr(payload)
            assert len(calls) == before, repr(payload)
        raw = {"success": False, "code": "change_set_conflict", "data": {
            "phase": "conflict", "recovery_manifest": "Library/MCPForUnity/ScriptChangeSets/owned/manifest.json",
            "files": [{"path": "Assets/First.cs", "state": "uncertain"}, {"path": "Assets/Second.cs", "state": "restored"}],
        }}
        response = await client.call_tool("script_change_set", {"action": "recover", "change_set_id": "a" * 32}, raise_on_error=False)
        assert unpack(response) == raw

anyio.run(main)
print("SDK prepare/apply/status/recover, bounded preflight and retained conflict outcomes passed")
"""


def test_registered_change_set_sdk_contract(tmp_path):
    # Given: a fresh real SDK registration with only the external Unity boundary controlled.
    environment = {
        **os.environ,
        "APPDATA": str(tmp_path),
        "XDG_DATA_HOME": str(tmp_path),
        "UNITY_MCP_DISABLE_TELEMETRY": "true",
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
    }
    environment.pop("PYTEST_CURRENT_TEST", None)
    # When: clients prepare, apply, inspect and recover a bounded source change set.
    result = subprocess.run(
        [sys.executable, "-B", "-c", PROGRAM],
        env=environment,
        capture_output=True,
        text=True,
        timeout=90,
    )
    # Then: registered SDK behavior preserves ordered payloads and refuses unsafe inputs before dispatch.
    assert result.returncode == 0, result.stdout + result.stderr
