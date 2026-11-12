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
after_send = None
raw = {"success": True, "data": {"change_set_id": "a" * 32, "phase": "prepared", "files": []}}

async def selected(ctx):
    return "Owned@fixture"

async def send(sender, instance, command, params, **kwargs):
    calls.append((instance, command, copy.deepcopy(params)))
    if after_send is not None:
        after_send()
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
    global raw, after_send
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
        large = [{"path": f"Assets/Source{i}.cs", "expected_bytes_sha256": "b" * 64,
                  "contents": f"class Source{i} {{}}" + ("/*" + "x" * 700000 + "*/" if i == 0 else "")} for i in range(40)]
        result = await client.call_tool("script_change_set", {"action": "prepare", "files": large}, raise_on_error=False)
        assert unpack(result).get("success"), repr(result)
        assert calls[-1][2]["files"] == large
        invalid = [
            {"action": "prepare", "files": []},
            {"action": "prepare", "files": files * 9},
            {"action": "prepare", "files": [files[0], files[0]]},
            {"action": "prepare", "files": [{**files[0], "path": "Assets/../Outside.cs"}]},
            {"action": "prepare", "files": [{**files[0], "expected_bytes_sha256": "bad"}]},
            {"action": "prepare", "files": [{**files[0], "contents": "x" * (2 * 1024 * 1024 + 1)}]},
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

        from services.registry import operation_invocation as invocation
        from services.registry.operation_context import active_operation
        from services.registry.operation_scheduler import OperationScope
        from services.registry.operation_target import OperationTarget
        from services.verification import service as verification
        starts, child_authorizations = [], []
        denied = False
        async def authorize(name, params):
            child_authorizations.append(name)
            if denied and name == "refresh_unity":
                raise PermissionError("revoked after source save")
            return lambda: None
        selected_target = OperationTarget(
            OperationScope("local", "fixture", "connection-1"), lambda: True,
            authorize_public=authorize, resume_generation="process-1",
        )
        async def capture(*args, **kwargs):
            return selected_target
        async def start_runner(self, io, profile, selection, lease, **kwargs):
            assert active_operation() is None, "mutation lease retained during verification"
            await io.authorize_profile(profile)
            starts.append((io.owner, selection, kwargs["expected_sources"]))
            return "f" * 32
        invocation.capture_target = capture
        verification.capture_target = capture
        verification.VerificationService.start_runner = start_runner
        verification.VerificationService.read = lambda *args: {
            "success": True, "data": {"run_id": "f" * 32, "state": "running", "stages": []},
        }
        raw = {"success": False, "code": "partial", "data": {"files": [
            {"path": "Assets/First.cs", "state": "confirmed", "replace_returned": True,
             "candidate_bytes_sha256": "b" * 64, "observed_bytes_sha256": "b" * 64},
            {"path": "Assets/Second.cs", "state": "confirmed", "replace_returned": True,
             "candidate_bytes_sha256": "c" * 64, "observed_bytes_sha256": "c" * 64},
            {"path": "Assets/Third.cs", "state": "uncertain", "replace_returned": True,
             "candidate_bytes_sha256": "d" * 64, "observed_bytes_sha256": "e" * 64},
        ]}}
        before = len(calls)
        response = await client.call_tool("script_change_set", {"action": "apply", "change_set_id": "a" * 32}, raise_on_error=False)
        assert unpack(response) == raw and not starts
        assert len(calls) == before + 1

        before = len(calls)
        response = await client.call_tool("script_change_set", {"action": "apply", "change_set_id": "a" * 32, "verification": "auto"}, raise_on_error=False)
        document = unpack(response)
        assert document["success"] is False and document["code"] == "partial"
        assert document["data"]["files"] == raw["data"]["files"]
        assert document["data"]["verification"]["status"] == "pending"
        assert document["data"]["verification_excluded_files"] == 1
        assert len(calls) == before + 1 and len(starts) == 1
        assert starts[0] == (("local", "fixture"), {"paths": ["Assets/First.cs", "Assets/Second.cs"]}, {
            "Assets/First.cs": {"bytes_sha256": "b" * 64}, "Assets/Second.cs": {"bytes_sha256": "c" * 64},
        })
        assert {"refresh_unity", "manage_script_quality"}.issubset(child_authorizations)

        denied = True
        before = len(calls)
        response = await client.call_tool("script_change_set", {"action": "apply", "change_set_id": "a" * 32, "verification": "auto"}, raise_on_error=False)
        document = unpack(response)
        assert document["data"]["verification"]["status"] == "unavailable"
        assert len(calls) == before + 1 and len(starts) == 1

        denied = False
        for process, expected_status in (("process-1", "pending"), ("replacement", "stale")):
            selected_target = OperationTarget(
                OperationScope("local", "fixture", "before-save"), lambda: True,
                authorize_public=authorize, resume_generation="process-1",
            )
            def replace_target():
                nonlocal selected_target
                selected_target = OperationTarget(
                    OperationScope("local", "fixture", "after-save"), lambda: True,
                    authorize_public=authorize, resume_generation=process,
                )
            after_send = replace_target
            before, starts_before = len(calls), len(starts)
            response = await client.call_tool("script_change_set", {"action": "apply", "change_set_id": "a" * 32, "verification": "auto"}, raise_on_error=False)
            document = unpack(response)
            assert document["code"] == "partial" and document["data"]["files"] == raw["data"]["files"]
            assert document["data"]["verification"]["status"] == expected_status, document
            assert len(calls) == before + 1
            assert len(starts) == starts_before + (1 if process == "process-1" else 0)
            after_send = None

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
