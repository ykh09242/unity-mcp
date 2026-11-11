"""Base64 method payloads follow the same preflight and dispatch contract as inline text."""

import base64
import copy
import hashlib
import importlib
from unittest.mock import AsyncMock

import pytest


tools = importlib.import_module("services.tools.script_apply_edits")


@pytest.fixture
def wire(monkeypatch):
    original, candidate = "class Foo {}", "class Foo { void M() {} }"
    digest = lambda text: hashlib.sha256(text.encode()).hexdigest()
    response = {
        "success": True,
        "data": {
            "preview": True,
            "complete": True,
            "truncated": False,
            "path": "Assets/Foo.cs",
            "uri": "mcpforunity://path/Assets/Foo.cs",
            "project_root": "C:/Project",
            "absolute_path": "C:/Project/Assets/Foo.cs",
            "original_contents": original,
            "new_contents": candidate,
            "original_sha256": digest(original),
            "candidate_sha256": digest(candidate),
            "candidate_bytes_sha256": digest(candidate),
            "sha256": digest(original),
            "encoding": "utf-8",
            "bom": False,
            "no_op": False,
            "editsApplied": 0,
            "editsPrepared": 1,
            "scheduledRefresh": False,
        },
    }
    instance = AsyncMock(return_value="Unity@test")
    reader = AsyncMock(return_value=response)
    writer = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(tools, "get_unity_instance_from_context", instance)
    monkeypatch.setattr(tools, "send_with_unity_instance", reader)
    monkeypatch.setattr(tools, "send_mutation", writer)
    return instance, reader, writer


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["replace_method", "insert_method"])
@pytest.mark.parametrize("preview", [True, False])
@pytest.mark.parametrize("wrapped", [True, False])
async def test_base64_method_reaches_preview_or_apply_as_normalized_text(
    wire, operation, preview, wrapped
):
    _, reader, writer = wire
    replacement = 'void M() { var text = "한😀"; }'
    edit = {
        "op": operation,
        "methodName": "M",
        "replacementBase64": base64.b64encode(replacement.encode()).decode("ascii"),
    }
    if wrapped:
        edit = {operation: {key: value for key, value in edit.items() if key != "op"}}
    original = copy.deepcopy(edit)
    result = await tools.script_apply_edits(
        AsyncMock(), "Foo", "Assets", [edit], {"preview": preview}
    )
    assert result["success"] is True, result
    params = (reader if preview else writer).await_args.args[3]
    assert params["action"] == ("preview_edit" if preview else "edit")
    assert params["edits"][0]["replacement"] == replacement
    assert params["edits"][0]["className"] == "Foo"
    assert result["data"]["normalizedEdits"][0]["replacement"] == replacement
    assert edit == original
    if preview:
        writer.assert_not_awaited()
    else:
        writer.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["replace_method", "insert_method"])
@pytest.mark.parametrize("preview", [True, False])
@pytest.mark.parametrize(
    "payload,code",
    [
        ("%%%", "invalid_edit"),
        ("dm9pZA==\n", "invalid_edit"),
        ("/w==", "invalid_edit"),
        (123, "invalid_edit"),
        ("A" * (((2 * 1024 * 1024 + 2) // 3) * 4 + 4), "payload_too_large"),
        (base64.b64encode(b"x" * (2 * 1024 * 1024 + 1)).decode(), "payload_too_large"),
    ],
)
async def test_bad_base64_is_rejected_before_any_routing(wire, operation, preview, payload, code):
    instance, reader, writer = wire
    result = await tools.script_apply_edits(
        AsyncMock(),
        "Foo",
        "Assets",
        [{"op": operation, "methodName": "M", "replacementBase64": payload}],
        {"preview": preview},
    )
    assert result["success"] is False and result["code"] == code, result
    instance.assert_not_awaited()
    reader.assert_not_awaited()
    writer.assert_not_awaited()


@pytest.mark.asyncio
async def test_inline_replacement_preserves_precedence_but_bad_base64_is_still_rejected(wire):
    instance, reader, writer = wire
    result = await tools.script_apply_edits(
        AsyncMock(),
        "Foo",
        "Assets",
        [{"op": "insert_method", "replacement": "void M() {}", "replacementBase64": "%%%"}],
    )
    assert result["success"] is False and result["code"] == "invalid_edit", result
    instance.assert_not_awaited()
    reader.assert_not_awaited()
    writer.assert_not_awaited()


@pytest.mark.asyncio
async def test_nonempty_inline_replacement_wins_over_valid_base64(wire):
    _, _, writer = wire
    result = await tools.script_apply_edits(
        AsyncMock(),
        "Foo",
        "Assets",
        [
            {
                "op": "insert_method",
                "replacement": "void Inline() {}",
                "replacementBase64": base64.b64encode(b"void Encoded() {}").decode(),
            }
        ],
    )
    assert result["success"] is True, result
    assert writer.await_args.args[3]["edits"][0]["replacement"] == "void Inline() {}"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["replace_method", "insert_method"])
@pytest.mark.parametrize("preview", [True, False])
async def test_null_optional_base64_preserves_inline_method_payload(wire, operation, preview):
    _, reader, writer = wire
    replacement = "void M() {}"
    result = await tools.script_apply_edits(
        AsyncMock(),
        "Foo",
        "Assets",
        [
            {
                "op": operation,
                "methodName": "M",
                "replacement": replacement,
                "replacementBase64": None,
            }
        ],
        {"preview": preview},
    )
    assert result["success"] is True, result
    params = (reader if preview else writer).await_args.args[3]
    assert params["action"] == ("preview_edit" if preview else "edit")
    assert params["edits"][0]["replacement"] == replacement
    assert params["edits"][0]["replacementBase64"] is None
    if preview:
        writer.assert_not_awaited()
    else:
        writer.assert_awaited_once()
