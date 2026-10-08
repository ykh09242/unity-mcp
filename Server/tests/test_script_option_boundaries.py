"""Script options are rejected before public tools route or issue Unity work."""

import hashlib
import importlib
from unittest.mock import AsyncMock

import pytest

scripts = importlib.import_module("services.tools.manage_script")
structured = importlib.import_module("services.tools.script_apply_edits")


@pytest.fixture
def script_boundary(monkeypatch):
    """Observe only the public tools' external routing and transport boundaries."""
    context = AsyncMock()
    instance = AsyncMock(return_value="Unity@test")
    reader = AsyncMock(
        return_value={"success": True, "data": {"contents": "class Foo {}\n", "sha256": "a" * 64}}
    )
    writer = AsyncMock(return_value={"success": True})
    verify = AsyncMock(return_value=True)
    for module in (scripts, structured):
        monkeypatch.setattr(module, "get_unity_instance_from_context", instance)
        monkeypatch.setattr(module, "send_with_unity_instance", reader)
        monkeypatch.setattr(module, "send_mutation", writer)
        monkeypatch.setattr(module, "verify_edit_by_sha", verify)
    return context, instance, reader, writer, verify


async def invoke(entrypoint, context, options=None, *, normalize=False, omit=False):
    """Invoke the complete public coroutine with a valid edit."""
    kwargs = {} if omit else {"options": options}
    if entrypoint == "apply_text_edits":
        edit = {"startLine": 1, "startCol": 1, "endLine": 1, "endCol": 1, "newText": "// next\n"}
        if normalize:
            edit = {"range": [0, 0], "newText": "// next\n"}
        return await scripts.apply_text_edits(context, "Assets/Foo.cs", [edit], **kwargs)
    edit = {"op": "insert_method", "replacement": "void Added() {}"}
    if normalize:
        edit = {"op": "append", "text": "// next\n"}
    return await structured.script_apply_edits(context, "Foo", "Assets", [edit], **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["apply_text_edits", "script_apply_edits"])
@pytest.mark.parametrize("normalize", [False, True])
@pytest.mark.parametrize("field", ["refresh", "validate", "applyMode", "apply_mode"])
@pytest.mark.parametrize("value", [{}, [], 0, False, 1.5])
async def test_nonstring_selector_rejected_before_routing(
    script_boundary, entrypoint, normalize, field, value
):
    # Given a valid edit and a malformed known string selector.
    context, instance, reader, writer, verify = script_boundary
    # When the complete public tool coroutine receives the nested value.
    result = await invoke(entrypoint, context, {field: value}, normalize=normalize)
    # Then routing, context/settings access, reads/SHA, writes and verification remain untouched.
    assert result.get("code") == "invalid_options", (
        result,
        instance.await_count,
        reader.await_count,
        writer.await_count,
    )
    assert result["success"] is False
    instance.assert_not_awaited()
    assert context.mock_calls == []
    reader.assert_not_awaited()
    writer.assert_not_awaited()
    verify.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["apply_text_edits", "script_apply_edits"])
@pytest.mark.parametrize("options", [[], "", False, 0, [["refresh", "none"]]])
async def test_nonobject_options_rejected_before_routing(script_boundary, entrypoint, options):
    # Given a malformed outer options shape, including falsey JSON scalars and an object-like list.
    context, instance, reader, writer, verify = script_boundary
    # When it reaches the public coroutine directly.
    result = await invoke(entrypoint, context, options)
    # Then the tool returns its ordinary options error before any external work.
    assert result.get("code") == "invalid_options", result
    assert result["success"] is False
    instance.assert_not_awaited()
    assert context.mock_calls == []
    reader.assert_not_awaited()
    writer.assert_not_awaited()
    verify.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["apply_text_edits", "script_apply_edits"])
async def test_malformed_last_alias_rejected_even_with_valid_canonical_selector(
    script_boundary, entrypoint
):
    # Given valid selectors followed by a malformed snake-case alias.
    context, instance, reader, writer, verify = script_boundary
    options = {"refresh": "none", "validate": "standard", "applyMode": "atomic", "apply_mode": []}
    # When the complete options object is parsed.
    result = await invoke(entrypoint, context, options)
    # Then a valid first alias cannot hide the malformed final input.
    assert result.get("code") == "invalid_options", result
    instance.assert_not_awaited()
    assert context.mock_calls == []
    reader.assert_not_awaited()
    writer.assert_not_awaited()
    verify.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["apply_text_edits", "script_apply_edits"])
@pytest.mark.parametrize("omit", [False, True])
async def test_omitted_and_null_options_preserve_ordinary_mutation(
    script_boundary, entrypoint, omit
):
    # Given a valid edit with options omitted or explicitly null.
    context, instance, _, writer, _ = script_boundary
    # When the public coroutine receives the request.
    result = await invoke(entrypoint, context, omit=omit)
    # Then normal routing and mutation still occur.
    assert result["success"] is True
    instance.assert_awaited_once_with(context)
    writer.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["apply_text_edits", "script_apply_edits"])
@pytest.mark.parametrize(
    "options",
    [
        {},
        {"refresh": None, "validate": None, "applyMode": None, "apply_mode": None},
        {"refresh": "deferred", "validate": "standard", "applyMode": "atomic"},
        {"refresh": "none", "validate": "none", "apply_mode": "sequential"},
        {"refresh": "immediate", "validate": "strict", "applyMode": "sequential"},
        {"refresh": "", "validate": "future-mode", "applyMode": "future-mode"},
        {"extension": {"nested": [False, None]}, "applyMode": "atomic", "apply_mode": "atomic"},
    ],
)
async def test_selector_strings_nulls_aliases_and_extension_keys_are_preserved(
    script_boundary, entrypoint, options
):
    # Given compatible selectors or an unknown extension object.
    context, _, _, writer, _ = script_boundary
    # When the public coroutine sends the valid edit.
    result = await invoke(entrypoint, context, options)
    # Then every supplied option survives without enum filtering or alias rewriting.
    assert result["success"] is True
    sent = writer.await_args.args[3]["options"]
    for field, value in options.items():
        assert sent[field] == value


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["apply_text_edits", "script_apply_edits"])
@pytest.mark.parametrize("value", [False, "false", None])
async def test_canonical_false_flags_keep_mutation_enabled(script_boundary, entrypoint, value):
    # Given false or null legacy boolean options alongside string selectors.
    context, _, _, writer, _ = script_boundary
    flags = ("preview", "debug_preview", "force_sentinel_reload")
    options = {field: value for field in flags}
    options.update({"refresh": "none", "validate": "standard", "apply_mode": "atomic"})
    # When the public coroutine normalizes the request.
    result = await invoke(entrypoint, context, options)
    # Then it applies rather than previewing and forwards real false values.
    assert result["success"] is True
    assert writer.await_count == 1
    sent = writer.await_args.args[3]["options"]
    assert all(sent[field] is False for field in flags)


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["apply_text_edits", "script_apply_edits"])
@pytest.mark.parametrize("preview", [True, "true"])
async def test_preview_flag_still_routes_to_read_only_preparation(
    script_boundary, entrypoint, preview
):
    # Given a valid preview and a complete Unity preparation response.
    context, instance, reader, writer, _ = script_boundary
    original, candidate = "class Foo {}\n", "// next\nclass Foo {}\n"
    original_sha = hashlib.sha256(original.encode("utf-8")).hexdigest()
    candidate_sha = hashlib.sha256(candidate.encode("utf-8")).hexdigest()
    reader.return_value = {
        "success": True,
        "data": {
            "preview": True,
            "complete": True,
            "truncated": False,
            "original_contents": original,
            "new_contents": candidate,
            "original_sha256": original_sha,
            "sha256": original_sha,
            "candidate_sha256": candidate_sha,
            "candidate_bytes_sha256": candidate_sha,
            "project_root": "C:/Unity",
            "absolute_path": "C:/Unity/Assets/Foo.cs",
            "path": "Assets/Foo.cs",
            "encoding": "utf-8",
            "bom": False,
            "editsApplied": 0,
            "editsPrepared": 1,
            "scheduledRefresh": False,
            "no_op": False,
        },
    }
    # When the public tool receives a canonical preview flag with selector options.
    result = await invoke(entrypoint, context, {"preview": preview, "refresh": "none"})
    # Then it prepares a preview successfully without requesting a mutation.
    assert result["success"] is True
    assert result["data"]["preview"] is True
    instance.assert_awaited_once_with(context)
    assert reader.await_args.args[3]["action"] in ("preview_edit", "preview_text_edits")
    assert reader.await_args.args[3]["options"]["preview"] is True
    writer.assert_not_awaited()
