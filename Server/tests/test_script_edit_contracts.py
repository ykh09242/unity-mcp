"""Script edit locator and replacement behavior at the Unity transport boundary."""
import asyncio
import importlib
import re
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

script_edits = importlib.import_module("services.tools.script_apply_edits")


@pytest.mark.asyncio
async def test_advertised_payload_limit_matches_editor_contract():
    # Given: the actual Editor text-edit endpoint's byte budget.
    source = (Path(__file__).resolve().parents[2] / "MCPForUnity/Editor/Tools/ManageScript.cs").read_text(encoding="utf-8-sig")
    declared = re.search(r"const int MaxEditPayloadBytes\s*=\s*(\d+)\s*\*\s*(\d+)\s*;", source)
    assert declared is not None
    editor_limit = int(declared.group(1)) * int(declared.group(2))
    scripts = importlib.import_module("services.tools.manage_script")
    # When: a client asks what edit payload it can submit.
    response = await scripts.manage_script_capabilities(AsyncMock())
    # Then: the advertised limit is accepted by that Editor endpoint.
    assert response["data"]["max_edit_payload_bytes"] == editor_limit


@pytest.mark.parametrize("name,path,expected", [
    ("Foo.cs", "Assets/Scripts", ("Foo", "Assets/Scripts")),
    ("Foo.CS", "Assets/Scripts", ("Foo", "Assets/Scripts")),
    ("Assets/Scripts/Foo.cs", "Assets/Other", ("Foo", "Assets/Scripts")),
    ("", "Assets/Scripts/Foo.cs", ("Foo", "Assets/Scripts")),
    ("file:///C:/Project/Assets/Scripts/Foo%20Bar.cs", "", ("Foo Bar", "Assets/Scripts")),
    ("Foo.cs", r"Assets\Scripts", ("Foo", "Assets/Scripts")),
])
def test_locator_preserves_explicit_script_directory(name, path, expected):
    # Given: a script supplied using a supported name/path form.
    # When: the structured editor normalizes its Unity locator.
    result = script_edits._normalize_script_locator(name, path)
    # Then: it targets the same requested file rather than an Assets-root namesake.
    assert result == expected


@pytest.fixture
def script_transport(monkeypatch):
    monkeypatch.setattr(script_edits, "get_unity_instance_from_context", AsyncMock(return_value=None))
    reader = AsyncMock(return_value={"success": True, "data": {"contents": "name=abc"}})
    writer = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(script_edits, "send_with_unity_instance", reader)
    monkeypatch.setattr(script_edits, "send_mutation", writer)
    return reader, writer


@pytest.mark.asyncio
async def test_regex_replacement_preserves_declared_replacement(script_transport):
    # Given: the standard replacement field with a capture-group reference.
    _, writer = script_transport
    # When: the tool converts it into a Unity text edit.
    response = await script_edits.script_apply_edits(
        AsyncMock(), "Foo", "Assets/Scripts",
        [{"op": "regex_replace", "pattern": r"name=(\w+)", "replacement": "$1=value"}],
    )
    # Then: the replacement text survives conversion instead of deleting the match.
    assert response["success"] is True
    assert writer.call_args.args[3]["edits"][0]["newText"] == "abc=value"


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["apply_text_edits", "script_apply_edits"])
@pytest.mark.parametrize("contents,line,start,end,expected", [
    ("😀x", 0, 2, 3, (1, 2, 1, 3)),
    ("a\r\n🦊y", 1, 2, 3, (2, 2, 2, 3)),
    ("abx", 0, 2, 3, (1, 3, 1, 4)),
    ("😀x", 0, 99, 100, (1, 3, 1, 3)),
])
async def test_lsp_range_uses_utf16_input_and_codepoint_wire_coordinates(
    monkeypatch, entrypoint, contents, line, start, end, expected,
):
    # Given: an LSP-style range uses UTF-16 offsets, with no encoding negotiation.
    module = importlib.import_module("services.tools.manage_script") if entrypoint == "apply_text_edits" else script_edits
    reader = AsyncMock(return_value={"success": True, "data": {"contents": contents}})
    writer = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(module, "get_unity_instance_from_context", AsyncMock(return_value=None))
    monkeypatch.setattr(module, "send_with_unity_instance", reader)
    monkeypatch.setattr(module, "send_mutation", writer)
    edits = [{"range": {"start": {"line": line, "character": start}, "end": {"line": line, "character": end}}, "newText": "Z"}]
    # When: either documented Python editing entrypoint normalizes that range.
    if entrypoint == "apply_text_edits":
        response = await module.apply_text_edits(AsyncMock(), "Assets/Scripts/Foo.cs", edits)
    else:
        response = await module.script_apply_edits(AsyncMock(), "Foo", "Assets/Scripts", edits)
    # Then: Unity receives 1-based codepoint coordinates for precisely the requested span.
    assert response["success"] is True
    span = writer.call_args.args[3]["edits"][0]
    assert tuple(span[field] for field in ("startLine", "startCol", "endLine", "endCol")) == expected
    assert span["newText"] == "Z"


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["apply_text_edits", "script_apply_edits"])
@pytest.mark.parametrize("line,character", [(0, 1), (1, 0), (0, -1)])
async def test_invalid_lsp_positions_do_not_write(monkeypatch, entrypoint, line, character):
    # Given: a range splits a surrogate pair or lies outside the document.
    module = importlib.import_module("services.tools.manage_script") if entrypoint == "apply_text_edits" else script_edits
    monkeypatch.setattr(module, "get_unity_instance_from_context", AsyncMock(return_value=None))
    monkeypatch.setattr(module, "send_with_unity_instance", AsyncMock(return_value={"success": True, "data": {"contents": "😀x"}}))
    writer = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(module, "send_mutation", writer)
    position = {"line": line, "character": character}
    edits = [{"range": {"start": position, "end": position}, "newText": "Z"}]
    # When: either edit entrypoint normalizes the request.
    if entrypoint == "apply_text_edits":
        response = await module.apply_text_edits(AsyncMock(), "Assets/Scripts/Foo.cs", edits)
    else:
        response = await module.script_apply_edits(AsyncMock(), "Foo", "Assets/Scripts", edits)
    # Then: the invalid span is rejected before any mutation.
    assert response["success"] is False
    assert response["code"] == "invalid_range"
    writer.assert_not_awaited()


@pytest.mark.asyncio
async def test_text_regex_timeout_does_not_block_event_loop(script_transport):
    # Given: an actual bounded regex that times out while unrelated work is ready.
    reader, writer = script_transport
    reader.return_value = {"success": True, "data": {"contents": "a" * 30_000 + "!"}}
    ticks = []

    async def heartbeat():
        await asyncio.sleep(0.005)
        ticks.append(True)

    pulse = asyncio.create_task(heartbeat())
    # When: the production text-only conversion runs the pattern.
    response = await script_edits.script_apply_edits(
        AsyncMock(), "Foo", "Assets/Scripts",
        [{"op": "regex_replace", "pattern": r"(a+)+$", "replacement": "x"}],
    )
    responsive = bool(ticks)
    await pulse
    # Then: matching yields to the event loop and never writes after rejection.
    assert responsive
    assert response["success"] is False
    writer.assert_not_awaited()
