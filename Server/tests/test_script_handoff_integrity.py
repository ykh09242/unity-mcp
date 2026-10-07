"""Mixed script recovery must distinguish each successfully applied phase."""

import os
from pathlib import Path
import subprocess
import sys


PROGRAM = r"""
import asyncio
import hashlib
import importlib
import importlib.util
import json
import sys
from fastmcp import Client, FastMCP
from core.logging_decorator import log_execution
from core.telemetry_decorator import telemetry_tool

spec = importlib.util.spec_from_file_location("services.tools.script_apply_edits", SOURCE)
tools = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = tools
spec.loader.exec_module(tools)
recovery = importlib.import_module("services.tools.refresh_unity")
state = {}

async def instance(ctx):
    return "Fixture@owned"

async def ready(ctx, timeout_s=30):
    return True, 0

async def wire(sender, target, command, params, **kwargs):
    assert target == "Fixture@owned" and command == "manage_script"
    assert params["name"] == "Foo" and params["path"] == "Assets"
    action = params["action"]
    state["calls"].append(action)
    case = state["case"]
    if action == "validate_edit":
        assert len(state["calls"]) == 1
        assert state["text"] == "class Foo {}\n"
        return {"success": True}
    if action == "read":
        return {"success": True, "data": {"contents": state["text"]}}
    if action == "apply_text_edits":
        assert params["options"]["refresh"] == "debounced"
        if case == "text_failure":
            return {"success": False, "error": "controlled text failure"}
        # Controlled text boundary; actual public normalization/recovery remain in use.
        state["text"] = tools._preview_text_spans(state["text"], params["edits"])
        sha = hashlib.sha256(state["text"].encode()).hexdigest()
        data = {"path": "Assets/Foo.cs", "editsApplied": 0 if case == "noop" else 1, "no_op": case == "noop", "sha256": sha, "scheduledRefresh": True}
        if case in ("missing", "missing_applied", "unavailable", "fallback_invalid", "fallback_empty"):
            data.pop("sha256")
        if case == "null_hash":
            data["sha256"] = None
        if case == "malformed":
            data["sha256"] = "not-a-sha"
        if case == "uppercase":
            data["sha256"] = sha.upper()
        return {"success": True, "data": data}
    if action == "get_sha":
        if case == "unavailable":
            return {"success": False, "error": "controlled SHA unavailable"}
        if case in ("fallback_invalid", "fallback_empty"):
            return {"success": True, "data": {"sha256": "not-a-sha" if case == "fallback_invalid" else ""}}
        return {"success": True, "data": {"sha256": hashlib.sha256(state["text"].encode()).hexdigest()}}
    if action == "edit":
        if case in ("applied", "missing_applied", "success"):
            state["text"] = state["text"].replace("class Foo {}", "class Foo { void M() {} }")
        if case == "success":
            return {"success": True, "data": {"editsApplied": 1}}
        return {"success": False, "error": "connection closed"}
    raise AssertionError(action)

tools.get_unity_instance_from_context = instance
tools.send_with_unity_instance = wire
recovery.unity_transport.send_with_unity_instance = wire
recovery.wait_for_editor_ready = ready
server = FastMCP("mixed-handoff-integrity")
wrapped = log_execution("script_apply_edits", "Tool")(tools.script_apply_edits)
server.tool(name="script_apply_edits")(telemetry_tool("script_apply_edits")(wrapped))

async def main():
    passed = failed = 0
    for mode in ("2026-07-28", "legacy"):
        async with Client(server, mode=mode) as client:
            for case in ("not_applied", "applied", "missing", "missing_applied", "malformed", "unavailable", "noop", "text_failure", "success", "uppercase", "null_hash", "fallback_invalid", "fallback_empty"):
                state.clear()
                state.update(case=case, text="class Foo {}\n", calls=[])
                result = await client.call_tool("script_apply_edits", {
                    "name": "Foo", "path": "Assets", "edits": [
                        {"op": "append", "text": "" if case == "noop" else "// first phase\n"},
                        {"op": "insert_method", "replacement": "void M() {}"},
                    ], "options": {"refresh": "debounced"},
                })
                response = result.structured_content
                try:
                    assert response["success"] is (case in ("applied", "missing_applied", "success")), response
                    if case == "text_failure":
                        assert state["calls"] == ["validate_edit", "read", "apply_text_edits"]
                        assert state["text"] == "class Foo {}\n"
                    elif case in ("missing", "missing_applied", "malformed", "null_hash"):
                        assert state["calls"] == ["validate_edit", "read", "apply_text_edits", "get_sha", "edit", "get_sha"]
                    elif case in ("unavailable", "fallback_invalid", "fallback_empty"):
                        assert state["calls"] == ["validate_edit", "read", "apply_text_edits", "get_sha", "edit"]
                    elif case == "success":
                        assert state["calls"] == ["validate_edit", "read", "apply_text_edits", "edit"]
                    else:
                        assert state["calls"] == ["validate_edit", "read", "apply_text_edits", "edit", "get_sha"]
                    if case not in ("applied", "missing_applied", "success", "text_failure"):
                        assert response["error"] == "connection closed"
                    passed += 1
                    verdict = "PASS"
                except AssertionError as error:
                    failed += 1
                    verdict = "FAIL " + str(error)
                print(json.dumps({"mode": mode, "case": case, "calls": state["calls"], "contents": state["text"], "response": response, "verdict": verdict}))
    print(f"RESULT {passed} passed {failed} failed")
    return failed == 0

raise SystemExit(0 if asyncio.run(main()) else 1)
"""


def test_mixed_script_recovery_does_not_verify_prior_text_phase(tmp_path):
    env = {
        **os.environ,
        "APPDATA": str(tmp_path / "profile"),
        "XDG_DATA_HOME": str(tmp_path / "profile"),
        "UNITY_MCP_DISABLE_TELEMETRY": "true",
    }
    source = Path(__file__).parents[1] / "src/services/tools/script_apply_edits.py"
    result = subprocess.run(
        [sys.executable, "-c", "SOURCE = " + repr(str(source)) + "\n" + PROGRAM],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "RESULT 26 passed 0 failed" in result.stdout


NEWLINE_PROGRAM = r"""
import asyncio
import hashlib
import importlib.util
import json
import sys
from fastmcp import Client, FastMCP

spec = importlib.util.spec_from_file_location("services.tools.script_apply_edits", SOURCE)
tools = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = tools
spec.loader.exec_module(tools)
state = {}
def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()
async def instance(ctx):
    return "Fixture@owned"
async def wire(sender, target, command, params, **kwargs):
    state["calls"].append(params)
    if params["action"] == "read":
        return {"success": True, "data": {"contents": state["source"]}}
    assert params["action"] == "preview_text_edits"
    assert params["precondition_sha256"] == sha(state["source"])
    original, candidate = state["source"], state["candidate"]
    if candidate is None:
        return {"success": False, "error": "controlled native out-of-range boundary"}
    return {"success": True, "data": {
        "preview": True, "complete": True, "truncated": False, "editsApplied": 0, "editsPrepared": 1,
        "scheduledRefresh": False, "encoding": "utf-8", "bom": False, "no_op": False,
        "path": "Assets/Foo.cs", "project_root": "C:/Owned", "absolute_path": "C:/Owned/Assets/Foo.cs",
        "original_contents": original, "new_contents": candidate, "sha256": sha(original),
        "original_sha256": sha(original), "candidate_sha256": sha(candidate), "candidate_bytes_sha256": sha(candidate),
    }}
async def mutate(ctx, target, command, params, **kwargs):
    state["calls"].append(params)
    assert params["action"] == "apply_text_edits"
    assert params["precondition_sha256"] == sha(state["source"])
    if state["candidate"] is None:
        return {"success": False, "error": "controlled native out-of-range boundary"}
    return {"success": True}
tools.get_unity_instance_from_context = instance
tools.send_with_unity_instance = wire
tools.send_mutation = mutate
server = FastMCP("native-newline-contract")
server.tool(name="script_apply_edits")(tools.script_apply_edits)

async def main():
    passed = failed = 0
    for mode in ("2026-07-28", "legacy"):
        async with Client(server, mode=mode) as client:
            cases = []
            for separator in ("\r", "\r\n", "\n", "\u2028", "\u0085", "\u2029", "\v", "\f"):
                source = "class Foo {}" + separator + "// 😀marker"
                native_line = 2 if separator in ("\r", "\r\n", "\n") else 1
                for op in ("append", "regex_replace"):
                    edit = {"op": "append", "text": separator + "// next"} if op == "append" else {"op": "regex_replace", "pattern": "marker", "replacement": "next"}
                    col = (11 if native_line == 2 else len(source) + 1) if op == "append" else (5 if native_line == 2 else len("class Foo {}" + separator + "// 😀") + 1)
                    candidate = source + edit["text"] if op == "append" else source.replace("marker", "next")
                    cases.append((source, edit, [native_line, col], candidate, False))
            for separator in ("\r", "\r\n", "\n"):
                cases.append(("class Foo {}" + separator, {"op": "append", "text": "// next"}, [2, 1], "class Foo {}" + separator + "// next", False))
                source = "class Foo {}" + separator + "// 😀marker"
                edit = {"range": {"start": {"line": 1, "character": 5}, "end": {"line": 1, "character": 11}}, "newText": "next"}
                cases.append((source, edit, [2, 5], source.replace("marker", "next"), False))
            cases.append(("class Foo {}\r\n// marker", {"op": "regex_replace", "pattern": "\r", "replacement": "X"}, None, None, True))
            for source, edit, expected, candidate, rejected in cases:
                for preview in (False, True):
                    state.clear()
                    state.update(source=source, candidate=candidate, calls=[])
                    arguments = {"name": "Foo", "path": "Assets", "edits": [edit], "options": {"preview": preview}}
                    result = await client.call_tool("script_apply_edits", arguments)
                    response = result.structured_content
                    try:
                        assert response["success"] is not rejected, response
                        if rejected:
                            assert len(state["calls"]) == 1 and state["calls"][0]["action"] == "read"
                        else:
                            span = state["calls"][-1]["edits"][0]
                            assert [span["startLine"], span["startCol"]] == expected, span
                            assert tools._preview_text_spans(source, [span]) == candidate
                            if preview:
                                assert response["data"]["original_contents"] == source
                                assert response["data"]["new_contents"] == candidate
                                assert response["data"]["candidate_bytes_sha256"] == sha(candidate)
                        passed += 1
                        verdict = "PASS"
                    except (AssertionError, ValueError) as error:
                        failed += 1
                        verdict = "FAIL " + str(error)
                    print(json.dumps({"mode": mode, "input": arguments, "source": source, "calls": state["calls"], "verdict": verdict}))
    print(f"RESULT {passed} passed {failed} failed")
    return failed == 0
raise SystemExit(0 if asyncio.run(main()) else 1)
"""


def test_script_text_spans_preserve_native_newline_coordinates(tmp_path):
    env = {
        **os.environ,
        "APPDATA": str(tmp_path / "profile"),
        "XDG_DATA_HOME": str(tmp_path / "profile"),
        "UNITY_MCP_DISABLE_TELEMETRY": "true",
    }
    source = Path(__file__).parents[1] / "src/services/tools/script_apply_edits.py"
    result = subprocess.run(
        [sys.executable, "-c", "SOURCE = " + repr(str(source)) + "\n" + NEWLINE_PROGRAM],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "RESULT 92 passed 0 failed" in result.stdout
