"""Nested JSON scalars must preserve the same types as top-level inputs."""

import importlib
from unittest.mock import AsyncMock

import pytest

scripts = importlib.import_module("services.tools.manage_script")
edits_tool = importlib.import_module("services.tools.script_apply_edits")
textures = importlib.import_module("services.tools.manage_texture")


@pytest.fixture
def script_wire(monkeypatch):
    reader = AsyncMock(return_value={"success": True, "data": {"contents": "class Foo {}\n"}})
    writer = AsyncMock(return_value={"success": True})
    for module in (scripts, edits_tool):
        monkeypatch.setattr(module, "get_unity_instance_from_context", AsyncMock(return_value="Unity@test"))
        monkeypatch.setattr(module, "send_with_unity_instance", reader)
        monkeypatch.setattr(module, "send_mutation", writer)
    return reader, writer


@pytest.mark.asyncio
@pytest.mark.parametrize("option", ["preview", "debug_preview", "force_sentinel_reload"])
async def test_text_option_false_string_applies_edit(script_wire, option):
    # Given an explicit false legacy option and a valid insertion.
    reader, writer = script_wire
    # When the public tool receives it.
    response = await scripts.apply_text_edits(None, "Assets/Foo.cs", [
        {"startLine": 1, "startCol": 1, "endLine": 1, "endCol": 1, "newText": "// next\n"}
    ], options={option: "false"})
    # Then it sends a write with a real false boolean.
    assert response["success"] is True
    assert writer.await_count == 1
    assert writer.await_args.args[3]["options"][option] is False
    reader.assert_not_awaited()


@pytest.mark.asyncio
async def test_script_preview_false_string_uses_mutation(script_wire):
    # Given a structured edit with an explicit false preview.
    reader, writer = script_wire
    # When it is submitted.
    result = await edits_tool.script_apply_edits(None, "Foo", "Assets", [
        {"op": "insert_method", "replacement": "void M() {}"}
    ], {"preview": "false"})
    # Then it routes to the mutation, with the normalized option.
    assert result["success"] is True
    assert writer.await_args.args[3]["options"]["preview"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [True, 1.0, 1.9, "1.0"])
@pytest.mark.parametrize("shape", ["explicit", "lsp", "index"])
async def test_invalid_edit_coordinate_rejected_before_transport(script_wire, shape, value):
    # Given an invalid scalar in one supported coordinate representation.
    reader, writer = script_wire
    edit = {"startLine": value, "startCol": 1, "endLine": 1, "endCol": 1, "newText": "x"}
    if shape == "lsp":
        edit = {"range": {"start": {"line": 0, "character": value}, "end": {"line": 0, "character": 1}}, "newText": "x"}
    if shape == "index":
        edit = {"range": [value, 1], "newText": "x"}
    # When it crosses the public tool boundary.
    result = await scripts.apply_text_edits(None, "Assets/Foo.cs", [edit])
    # Then no Unity command is sent.
    assert result["success"] is False
    assert result["code"] == "invalid_range"
    reader.assert_not_awaited()
    writer.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [0, 1, "yes", ""])
@pytest.mark.parametrize("option", ["preview", "debug_preview", "force_sentinel_reload"])
async def test_invalid_option_rejected_before_transport(script_wire, option, value):
    # Given an invalid boolean option.
    reader, writer = script_wire
    # When it crosses the public tool boundary.
    result = await scripts.apply_text_edits(None, "Assets/Foo.cs", [
        {"startLine": 1, "startCol": 1, "endLine": 1, "endCol": 1, "newText": "x"}
    ], options={option: value})
    # Then validation returns an ordinary tool error before transport.
    assert result["success"] is False
    assert result["code"] == "invalid_options"
    reader.assert_not_awaited()
    writer.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["ignore_case", "allow_noop", "prefer_last"])
async def test_local_edit_false_string_preserves_boolean_semantics(field):
    # Given options whose false values affect anchor selection or no-op behavior.
    edit = {"op": "anchor_insert", "anchor": "x", "text": "!", field: "false"}
    original = "X" if field == "ignore_case" else "x\nx"
    if field == "allow_noop":
        edit["anchor"] = "missing"
    # When local editing consumes the legacy option.
    if field == "allow_noop":
        with pytest.raises(RuntimeError):
            await edits_tool._apply_edits_locally(original, [edit])
        return
    result = await edits_tool._apply_edits_locally(original, [edit])
    # Then false is honored rather than treated as a truthy string.
    assert result == ("X" if field == "ignore_case" else "!x\nx")


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [True, 1.0, 1.9])
async def test_script_range_rejects_noninteger_before_transport(script_wire, value):
    # Given a text edit whose line would otherwise be silently truncated.
    reader, writer = script_wire
    # When the high-level tool consumes it.
    result = await edits_tool.script_apply_edits(None, "Foo", "Assets", [
        {"op": "replace_range", "startLine": value, "startCol": 1, "endLine": 1, "endCol": 1, "text": "x"}
    ])
    # Then validation rejects it before reading or writing.
    assert result["success"] is False
    assert result["code"] == "invalid_range"
    reader.assert_not_awaited()
    writer.assert_not_awaited()


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), "NaN", "Infinity"])
@pytest.mark.parametrize("field", ["pivot", "pixels_per_unit"])
def test_sprite_numbers_reject_boolean_and_nonfinite(field, value):
    # Given an invalid sprite numeric scalar.
    payload = {field: [value, 0.5] if field == "pivot" else value}
    # When sprite settings are parsed.
    settings, error = textures._normalize_sprite_settings(payload)
    # Then the invalid value cannot reach Unity.
    assert settings is None
    assert error is not None


@pytest.mark.parametrize("field,value", [
    ("srgb", 1), ("srgb", "yes"), ("aniso_level", True), ("aniso_level", 1.0),
    ("max_texture_size", 64.0), ("compression_quality", True), ("sprite_extrude", 1.0),
    ("sprite_pixels_per_unit", True), ("sprite_pixels_per_unit", float("inf")),
    ("sprite_pivot", [float("nan"), 0.5]), ("sprite_pivot", [True, 0.5]),
])
def test_import_settings_reject_invalid_scalar(field, value):
    # Given an invalid nested importer value.
    # When importer settings are parsed.
    settings, error = textures._normalize_import_settings({field: value})
    # Then parsing returns a validation error.
    assert settings is None
    assert error is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["x", "y", "width", "height"])
@pytest.mark.parametrize("value", [True, 1.0, "1.0"])
async def test_pixel_region_rejects_invalid_integer_before_preflight(monkeypatch, field, value):
    # Given an invalid region coordinate or size.
    preflight = AsyncMock(return_value=None)
    send = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(textures, "get_unity_instance_from_context", AsyncMock(return_value="Unity@test"))
    monkeypatch.setattr(textures, "preflight", preflight)
    monkeypatch.setattr(textures, "send_with_unity_instance", send)
    region = {"x": 0, "y": 0, "width": 1, "height": 1, "color": [255, 0, 0]}
    region[field] = value
    # When the public texture tool is called.
    result = await textures.manage_texture(None, "modify", "Assets/Test.png", set_pixels=region)
    # Then it rejects before preflight can refresh Unity.
    assert result["success"] is False
    preflight.assert_not_awaited()
    send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("option", ["preview", "debug_preview", "force_sentinel_reload"])
async def test_null_option_uses_false_default(script_wire, option):
    # Given a null optional flag.
    _, writer = script_wire
    # When the tool is called with a valid insertion.
    result = await scripts.apply_text_edits(None, "Assets/Foo.cs", [
        {"startLine": "1", "startCol": "1", "endLine": "1", "endCol": "1", "newText": "x"}
    ], options={option: None})
    # Then null selects the ordinary false default and integer strings normalize.
    assert result["success"] is True
    params = writer.await_args.args[3]
    assert params["options"][option] is False
    assert params["edits"][0]["startLine"] == 1


@pytest.mark.parametrize("normalize,payload,expected", [
    (textures._normalize_sprite_settings, {"pivot": ["0.25", "0.75"], "pixels_per_unit": "100.5"},
     {"pivot": [0.25, 0.75], "pixelsPerUnit": 100.5}),
    (textures._normalize_import_settings, {"srgb": "false", "aniso_level": "3", "sprite_pixels_per_unit": "100"},
     {"sRGBTexture": False, "anisoLevel": 3, "spritePixelsPerUnit": 100.0}),
    (textures._normalize_sprite_settings, {"pivot": None, "pixels_per_unit": None}, {}),
    (textures._normalize_import_settings, {"srgb": None, "aniso_level": None, "sprite_pixels_per_unit": None}, {}),
    (textures._normalize_sprite_settings, False, None),
])
def test_sprite_and_import_settings_accept_legacy_strings_and_defaults(normalize, payload, expected):
    # Given valid legacy strings or null optional settings.
    # When settings are parsed.
    settings, error = normalize(payload)
    # Then they retain their numeric values or use the optional default.
    assert error is None
    assert settings == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("ignore_case", 1), ("prefer_last", "yes"),
                                        ("allow_noop", 0), ("count", True), ("count", 1.5)])
async def test_invalid_nested_edit_scalar_rejected_before_transport(script_wire, field, value):
    # Given an invalid nested flag or count.
    reader, writer = script_wire
    # When the script tool parses the edit.
    result = await edits_tool.script_apply_edits(None, "Foo", "Assets", [
        {"op": "regex_replace", "pattern": "x", "replacement": "y", field: value}
    ])
    # Then it rejects without touching Unity.
    assert result["success"] is False
    reader.assert_not_awaited()
    writer.assert_not_awaited()
