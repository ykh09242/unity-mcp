"""Reuse one original-buffer line map within each public LSP edit operation."""

import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import weakref

import pytest

module = importlib.import_module("services.tools.script_apply_edits")


class SourcePayload(str):
    """Observe the producer source without retaining it in request observers."""


@pytest.fixture
def routing(monkeypatch):
    state = SimpleNamespace(
        source="// 😀 ready\r\nnext\rfinal\n",
        maps=[],
        calls=[],
        selects=[],
        writes=[],
        payload_refs=[],
        encoded=False,
    )
    original = module._script_lines_and_starts

    def metadata(contents):
        state.maps.append((len(contents), hashlib.sha256(contents.encode()).hexdigest()))
        return original(contents)

    async def selected(_ctx):
        instance = "Selected@fixture"
        state.selects.append(instance)
        return instance

    async def read(_send, instance, command, params):
        assert command == "manage_script" and instance == "Selected@fixture"
        state.calls.append(params["action"])
        assert params["action"] == "read"
        source = SourcePayload(state.source)
        state.payload_refs.append(weakref.ref(source))
        if state.encoded:
            import base64

            return {
                "success": True,
                "data": {
                    "contentsEncoded": True,
                    "encodedContents": base64.b64encode(source.encode()).decode(),
                },
            }
        return {"success": True, "data": {"contents": source}}

    async def write(_ctx, instance, command, params, **_kwargs):
        assert command == "manage_script" and instance == "Selected@fixture"
        state.calls.append(params["action"])
        assert params["action"] == "apply_text_edits"
        state.writes.append(params)
        return {"success": True, "data": {"saved": True}}

    monkeypatch.setattr(module, "_script_lines_and_starts", metadata)
    monkeypatch.setattr(module, "get_unity_instance_from_context", selected)
    monkeypatch.setattr(module, "send_with_unity_instance", read)
    monkeypatch.setattr(module, "send_mutation", write)
    return state


def lsp_edit(start=3, end=5):
    return {
        "op": "replace_range",
        "range": {"start": {"line": 0, "character": start}, "end": {"line": 0, "character": end}},
        "newText": "updated",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["large", "unicode", "encoded", "regex"])
async def test_public_lsp_uses_one_original_line_map(routing, case):
    edit = lsp_edit()
    edits = [edit]
    if case == "large":
        routing.source = "// fixture line\r\n" * 65_536
        edits = [lsp_edit(3, 10)]
    if case == "encoded":
        routing.encoded = True
    if case == "regex":
        edits.append({"op": "regex_replace", "pattern": "ready", "replacement": "set"})
    original_edits = json.loads(json.dumps(edits))
    result = await module.script_apply_edits(None, "Fixture", "Assets", edits)
    assert result["success"] is True
    digest = hashlib.sha256(routing.source.encode()).hexdigest()
    assert routing.maps == [(len(routing.source), digest)]
    assert routing.selects == ["Selected@fixture"]
    assert routing.calls == ["read", "apply_text_edits"]
    assert routing.writes[0]["precondition_sha256"] == digest
    assert edits == original_edits
    span = routing.writes[0]["edits"][0]
    assert span == {
        "startLine": 1,
        "startCol": 4,
        "endLine": 1,
        "endCol": 11 if case == "large" else 5,
        "newText": "updated",
    }
    if case == "regex":
        assert routing.writes[0]["edits"][1] == {
            "startLine": 1,
            "startCol": 6,
            "endLine": 1,
            "endCol": 11,
            "newText": "set",
        }


@pytest.mark.asyncio
async def test_metadata_is_not_reused_across_changed_documents(routing):
    for source in ("// 😀 ready\r\nnext", "// 😃 changed\nnew line\rfinal"):
        routing.source = source
        result = await module.script_apply_edits(None, "Fixture", "Assets", [lsp_edit()])
        assert result["success"] is True
    assert len(routing.maps) == 2
    assert routing.maps[0][1] != routing.maps[1][1]
    assert len(routing.selects) == 2
    assert routing.calls == ["read", "apply_text_edits"] * 2


@pytest.mark.asyncio
async def test_ordinary_append_keeps_default_helper_path(routing):
    result = await module.script_apply_edits(
        None, "Fixture", "Assets", [{"op": "append", "text": "last"}]
    )
    assert result["success"] is True and len(routing.maps) == 1
    assert routing.writes[0]["edits"] == [
        {"startLine": 4, "startCol": 1, "endLine": 4, "endCol": 1, "newText": "last"}
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("coordinate", [True, 4])
async def test_invalid_lsp_coordinates_keep_read_write_boundary(routing, coordinate):
    result = await module.script_apply_edits(None, "Fixture", "Assets", [lsp_edit(coordinate, 5)])
    assert result["success"] is False
    assert routing.calls == ([] if coordinate is True else ["read"])
    assert not routing.writes


@pytest.mark.asyncio
async def test_reused_map_does_not_bypass_work_budget(routing, monkeypatch):
    monkeypatch.setattr(module.bounded_regex, "MAX_WORK_CHARS", 1)
    result = await module.script_apply_edits(None, "Fixture", "Assets", [lsp_edit()])
    assert result["success"] is False and result["code"] == "conversion_failed"
    assert routing.calls == ["read"] and not routing.writes


@pytest.mark.asyncio
async def test_private_helper_default_and_reused_map_have_same_spans():
    contents = "// 😀 ready\r\nnext\rfinal\n"
    edits = [
        {"op": "regex_replace", "pattern": "ready", "replacement": "set"},
        {"op": "append", "text": "last"},
    ]
    expected = await module._text_edit_spans(contents, edits)
    metadata = module._script_lines_and_starts(contents)
    assert await module._text_edit_spans(contents, edits, line_metadata=metadata) == expected


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
def test_sdk_script_edit_uses_one_source_map(tmp_path, mode):
    source = r"""import asyncio, json, sys
sys.path[:0] = ["src", "tests"]
from fastmcp import Client, FastMCP
from pytest import MonkeyPatch
import test_script_line_metadata_reuse as regression

async def scenario():
    with MonkeyPatch.context() as monkeypatch:
        state = regression.routing.__wrapped__(monkeypatch)
        app = FastMCP("script-line-map-regression")
        app.tool(regression.module.script_apply_edits)
        async with Client(app, mode=MODE) as client:
            result = await client.call_tool("script_apply_edits", {"name":"Fixture", "path":"Assets", "edits":[regression.lsp_edit(), {"op":"regex_replace", "pattern":"ready", "replacement":"set"}]})
            assert json.loads(result.content[0].text)["success"] is True
            assert len(state.maps) == 1
            assert state.calls == ["read", "apply_text_edits"]
            assert len(state.selects) == 1
asyncio.run(scenario())
"""
    source = source.replace("MODE", repr(mode))
    env = {**os.environ, "UNITY_MCP_DISABLE_TELEMETRY": "true"}
    for key in ("APPDATA", "XDG_DATA_HOME", "UNITY_MCP_LOG_DIR", "TEMP", "TMP"):
        env[key] = str(tmp_path)
    result = subprocess.run(
        [sys.executable, "-B", "-c", source],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
