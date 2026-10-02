"""Literal Python edit payloads and preview must agree with the Unity wire."""
import difflib
import importlib
from unittest.mock import AsyncMock

import pytest

tools = importlib.import_module("services.tools.script_apply_edits")


@pytest.fixture
def wire(monkeypatch):
    reader = AsyncMock()
    writer = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(tools, "get_unity_instance_from_context", AsyncMock(return_value=None))
    monkeypatch.setattr(tools, "send_with_unity_instance", reader)
    monkeypatch.setattr(tools, "send_mutation", writer)
    return reader, writer


@pytest.mark.asyncio
@pytest.mark.parametrize("mixed", [False, True])
@pytest.mark.parametrize("source", ["middle", "middle\n", "middle\r\n"])
@pytest.mark.parametrize("op", ["prepend", "append"])
async def test_literal_boundary_payload_survives_each_route(wire, mixed, source, op):
    reader, writer = wire
    reader.return_value = {"success": True, "data": {"contents": source}}
    payload = '\tconst string x = @"a\nb";\n'
    edits = [{"op": op, "text": payload}]
    if mixed:
        edits.append({"op": "replace_method", "methodName": "M", "replacement": "void M() {}"})
    result = await tools.script_apply_edits(AsyncMock(), "Foo", "Assets", edits)
    assert result["success"] is True
    params = writer.await_args_list[0].args[3]
    assert params["action"] == "apply_text_edits"
    assert params["edits"][0]["newText"] == payload
    if mixed:
        assert writer.await_args_list[1].args[3]["edits"][0]["replacement"] == "void M() {}"


@pytest.mark.asyncio
@pytest.mark.parametrize("op", ["prepend", "append"])
async def test_local_literal_boundary_does_not_add_separators(op):
    source = "middle\r\n"
    payload = "inline"
    expected = payload + source if op == "prepend" else source + payload
    assert await tools._apply_edits_locally(source, [{"op": op, "text": payload}]) == expected


@pytest.mark.asyncio
async def test_preview_matches_production_regex_occurrence(wire):
    reader, writer = wire
    source = "name=first\r\nname=last\r\n"
    reader.return_value = {"success": True, "data": {"contents": source}}
    edits = [{"op": "regex_replace", "pattern": r"name=(\w+)", "replacement": "$1=done\n"}]
    preview = await tools.script_apply_edits(AsyncMock(), "Foo", "Assets", edits, {"preview": True})
    writer.assert_not_awaited()
    applied = await tools.script_apply_edits(AsyncMock(), "Foo", "Assets", edits)
    assert preview["success"] is True and applied["success"] is True
    span = writer.await_args.args[3]["edits"][0]
    assert (span["startLine"], span["newText"]) == (2, "last=done\n")
    expected = "name=first\r\nlast=done\n\r\n"
    diff = "".join(difflib.unified_diff(source.splitlines(keepends=True), expected.splitlines(keepends=True), fromfile="before", tofile="after", n=3))
    assert preview["data"]["diff"] == diff


@pytest.mark.asyncio
async def test_structured_preview_rejects_before_io(wire):
    reader, writer = wire
    result = await tools.script_apply_edits(AsyncMock(), "Foo", "Assets", [{"op": "insert_method", "replacement": "void M() {}"}], {"preview": True})
    assert result["code"] == "unsupported_preview"
    reader.assert_not_awaited()
    writer.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("op", ["anchor_insert", "anchor_replace"])
async def test_anchor_payload_is_forwarded_literally(wire, op):
    reader, writer = wire
    reader.return_value = {"success": True, "data": {"sha256": "previous"}}
    payload = 'inline\n\t@"raw\r\n  value"'
    response = await tools.script_apply_edits(AsyncMock(), "Foo", "Assets", [{"op": op, "anchor": "target", "text": payload, "position": "before"}])
    assert response["success"] is True
    edit = writer.await_args.args[3]["edits"][0]
    assert (edit["text"], edit["position"]) == (payload, "before")
    assert await tools._apply_edits_locally("target", [{"op": "anchor_insert", "anchor": "target", "text": payload, "position": "before"}]) == payload + "target"


@pytest.mark.asyncio
async def test_range_literal_and_options_survive_wire(wire):
    reader, writer = wire
    reader.return_value = {"success": True, "data": {"contents": "\tline\r\n"}}
    payload = '\n  """\r\n    raw\n  """'
    response = await tools.script_apply_edits(AsyncMock(), "Foo", "Assets", [{"op": "replace_range", "startLine": 1, "startCol": 2, "endLine": 1, "endCol": 6, "text": payload}], {"validate": "relaxed", "refresh": "none"})
    assert response["success"] is True
    params = writer.await_args.args[3]
    assert params["edits"][0]["newText"] == payload
    assert params["options"] == {"validate": "relaxed", "refresh": "none", "applyMode": "sequential"}
    assert await tools._apply_edits_locally("x", [{"op": "append", "text": payload}] * 2) == "x" + payload * 2


@pytest.mark.asyncio
async def test_explicit_empty_literal_is_not_replaced_by_alias(wire):
    reader, writer = wire
    reader.return_value = {"success": True, "data": {"contents": "x"}}
    response = await tools.script_apply_edits(AsyncMock(), "Foo", "Assets", [{"op": "replace_range", "startLine": 1, "startCol": 1, "endLine": 1, "endCol": 2, "text": "", "replacement": "fallback"}])
    assert response["success"] is True
    assert writer.await_args.args[3]["edits"][0]["newText"] == ""


@pytest.mark.asyncio
async def test_same_position_preview_uses_unity_stable_insert_order(wire):
    reader, writer = wire
    source = "middle\r\n"
    reader.return_value = {"success": True, "data": {"contents": source}}
    edits = [{"op": "append", "text": "first\n"}, {"op": "append", "text": "second\n"}]
    preview = await tools.script_apply_edits(AsyncMock(), "Foo", "Assets", edits, {"preview": True})
    assert preview["success"] is True
    expected = source + "second\nfirst\n"
    assert preview["data"]["diff"] == "".join(difflib.unified_diff(source.splitlines(keepends=True), expected.splitlines(keepends=True), fromfile="before", tofile="after", n=3))
    writer.assert_not_awaited()
    response = await tools.script_apply_edits(AsyncMock(), "Foo", "Assets", edits)
    assert response["success"] is True
    params = writer.await_args.args[3]
    assert params["options"]["applyMode"] == "atomic"
    assert [edit["newText"] for edit in params["edits"]] == ["first\n", "second\n"]


@pytest.mark.asyncio
@pytest.mark.parametrize("insert_first", [True, False])
async def test_preview_overlap_matches_unity_same_start_order(wire, insert_first):
    reader, writer = wire
    reader.return_value = {"success": True, "data": {"contents": "abc"}}
    insert = {"op": "replace_range", "startLine": 1, "startCol": 2, "endLine": 1, "endCol": 2, "text": "X"}
    replace = {"op": "replace_range", "startLine": 1, "startCol": 2, "endLine": 1, "endCol": 3, "text": "Y"}
    edits = [insert, replace] if insert_first else [replace, insert]
    preview = await tools.script_apply_edits(AsyncMock(), "Foo", "Assets", edits, {"preview": True})
    writer.assert_not_awaited()
    if insert_first:
        assert preview["success"] is False
        assert preview["code"] == "preview_failed"
        # Unity rejects the unchanged wire batch under its stable descending-start check.
        writer.return_value = {"success": False, "data": {"status": "overlap"}}
    else:
        assert preview["success"] is True
        assert preview["data"]["diff"] == "".join(difflib.unified_diff(["abc"], ["aXYc"], fromfile="before", tofile="after", n=3))
    response = await tools.script_apply_edits(AsyncMock(), "Foo", "Assets", edits)
    assert response["success"] is not insert_first
    params = writer.await_args.args[3]
    assert params["options"]["applyMode"] == "atomic"
    assert [(edit["startCol"], edit["endCol"], edit["newText"]) for edit in params["edits"]] == [(item["startCol"], item["endCol"], item["text"]) for item in edits]
