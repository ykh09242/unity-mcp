"""Projection validation is local; selection reaches Unity in one command."""

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def console(monkeypatch):
    module = importlib.import_module("services.tools.read_console")
    lookup = AsyncMock(return_value="Fixture@owned")
    send = AsyncMock(return_value={"success": True, "data": []})
    monkeypatch.setattr(module, "get_unity_instance_from_context", lookup)
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    return module, lookup, send


@pytest.mark.asyncio
@pytest.mark.parametrize("fields,options", [
    ([], {}), (["message"], {}), (["type"], {}), (["file", "line"], {}),
    (["type", "message", "unknown"], {}), (["type", "message", "message"], {}),
    (["type", "message", 1], {}), ("not-json", {}), ('{"type": "message"}', {}),
    ('["type", "message"', {}), (["type", "message"], {"format": "plain"}),
    (["type", "message"], {"action": "clear"}),
    (["type", "message", "stackTrace"], {}),
], ids=["empty", "no-type", "no-message", "metadata-only", "unknown", "duplicate",
        "not-string", "not-json", "object", "malformed", "plain", "clear", "stack-disabled"])
async def test_invalid_fields_fail_before_instance_or_editor_io(console, fields, options):
    # Given an invalid selection or incompatible operation.
    module, lookup, send = console
    arguments = {"format": "json", **options}
    # When the public wrapper validates it.
    response = await module.read_console(SimpleNamespace(), fields=fields, **arguments)
    # Then neither routing nor Editor dispatch occurs.
    assert response["success"] is False
    lookup.assert_not_awaited()
    send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [["type", "message", "file"], '["type", "message", "file"]'])
async def test_projection_is_forwarded_without_an_extra_editor_call(console, fields):
    # Given valid structured field selection and paging.
    module, lookup, send = console
    # When the tool is read.
    response = await module.read_console(SimpleNamespace(), fields=fields, format="detailed", page_size="3", cursor="7")
    # Then the existing single Editor command carries the selection.
    assert response["success"] is True
    lookup.assert_awaited_once()
    send.assert_awaited_once()
    assert send.call_args.args[1:3] == ("Fixture@owned", "read_console")
    assert send.call_args.args[3]["fields"] == ["type", "message", "file"]
    assert send.call_args.args[3]["pageSize"] == 3
    assert send.call_args.args[3]["cursor"] == 7


@pytest.mark.asyncio
@pytest.mark.parametrize("format", ["plain", "json", "detailed"])
async def test_omitted_fields_preserve_legacy_wire_shape(console, format):
    # Given an existing client that supplies no field projection.
    module, _, send = console
    # When it reads console output in any legacy format.
    await module.read_console(SimpleNamespace(), format=format)
    # Then the new optional field is absent and defaults are unchanged.
    assert send.call_args.args[3] == {
        "action": "get", "types": ["error", "warning", "log"], "count": 10,
        "format": format, "includeStacktrace": False,
    }


@pytest.mark.asyncio
async def test_selected_stacktrace_requires_and_preserves_explicit_enablement(console):
    # Given explicit selection and enablement of stack data.
    module, _, send = console
    # When the request is dispatched.
    await module.read_console(SimpleNamespace(), fields=["type", "message", "stackTrace"],
                              format="json", include_stacktrace="true")
    # Then no projection option silently overrides the stack flag.
    assert send.call_args.args[3]["includeStacktrace"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("cursor", ["bad", True, 1.5])
async def test_invalid_cursor_is_rejected_before_projection_dispatch(console, cursor):
    # Given a valid projection and invalid cursor input.
    module, lookup, send = console
    # When integer coercion validates the cursor.
    with pytest.raises(ValueError):
        await module.read_console(SimpleNamespace(), fields=["type", "message"], format="json", cursor=cursor)
    # Then there is no routing or Editor dispatch.
    lookup.assert_not_awaited()
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_projection_does_not_modify_native_failure_metadata(console):
    # Given an Editor failure carrying a full diagnostic and metadata.
    module, _, send = console
    failure = {"success": False, "error": "Complete native diagnostic", "hint": "retry",
               "data": {"reason": "controlled_failure", "stackTrace": "Preserved failure stack"}}
    send.return_value = failure
    # When the caller requests compact successful log entries.
    result = await module.read_console(SimpleNamespace(), fields=["type", "message"], format="json")
    # Then the native failure envelope is untouched.
    assert result == failure
