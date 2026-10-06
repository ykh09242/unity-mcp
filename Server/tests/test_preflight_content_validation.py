"""Locally invalid content must not consult or refresh a Unity editor."""

import importlib
from unittest.mock import AsyncMock

import pytest

scripts = importlib.import_module("services.tools.manage_script")
edits = importlib.import_module("services.tools.script_apply_edits")
scenes = importlib.import_module("services.tools.manage_scene")
textures = importlib.import_module("services.tools.manage_texture")
finder = importlib.import_module("services.tools.find_in_file")


@pytest.fixture
def editor_io(monkeypatch):
    preflight = importlib.import_module("services.tools.preflight")
    refresh = importlib.import_module("services.tools.refresh_unity")
    state = importlib.import_module("services.resources.editor_state")
    wire = importlib.import_module("transport.unity_transport")
    monkeypatch.setattr(preflight, "_in_pytest", lambda: False)
    monkeypatch.setattr(refresh, "_in_pytest", lambda: False)
    readiness = AsyncMock(return_value={"success": True, "data": {
        "assets": {"external_changes_dirty": True},
        "advice": {"ready_for_tools": True},
    }})
    refresh_call = AsyncMock(return_value={"success": True})
    instance = AsyncMock(return_value="Editor@test")
    transport = AsyncMock(return_value={"success": True, "data": {
        "contents": "class Foo {}\n", "sha256": "a" * 64,
    }})
    monkeypatch.setattr(state, "get_editor_state", readiness)
    monkeypatch.setattr(refresh, "refresh_unity", refresh_call)
    monkeypatch.setattr(wire, "send_with_unity_instance", transport)
    for module in (scripts, edits, scenes, textures, finder):
        monkeypatch.setattr(module, "get_unity_instance_from_context", instance)
        monkeypatch.setattr(module, "send_with_unity_instance", transport)
    return instance, readiness, refresh_call, transport


INVALID_CALLS = [
    (scenes.manage_scene, {"action": "create"}),
    (scenes.manage_scene, {"action": "create", "name": "Foo", "template": "bad"}),
    (scenes.manage_scene, {"action": "load"}),
    (scenes.manage_scene, {"action": "load", "build_index": -1}),
    (scenes.manage_scene, {"action": "close_scene"}),
    (scenes.manage_scene, {"action": "move_to_scene", "scene_name": "Foo"}),
    (textures.manage_texture, {"action": "create"}),
    (textures.manage_texture, {"action": "modify", "path": "Assets/Foo.png", "set_pixels": {}}),
    (textures.manage_texture, {"action": "set_import_settings", "path": "Assets/Foo.png", "as_sprite": False}),
    (textures.manage_texture, {"action": "modify", "path": "Assets/Foo.png", "as_sprite": True, "import_settings": {"readable": False}}),
    (textures.manage_texture, {"action": "create", "path": "Assets/Foo.png", "width": 1, "height": 1, "pixels": "!!!!"}),
    (textures.manage_texture, {"action": "create", "path": "Assets/Foo.png", "width": 1, "height": 1, "pixels": "AA=="}),
    (finder.find_in_file, {"uri": "Assets/Foo.cs", "pattern": "["}),
    (finder.find_in_file, {"uri": "Assets/Foo%20Bar.cs", "pattern": "class"}),
    (scripts.create_script, {"path": "Outside/Foo.cs", "contents": ""}),
    (scripts.delete_script, {"uri": "Outside/Foo.cs"}),
    (scripts.validate_script, {"uri": "Assets/Foo.cs", "level": "bad"}),
    (scripts.get_sha, {"uri": "Assets/1Foo.cs"}),
    (scripts.manage_script, {"action": "read", "name": "", "path": "Assets"}),
    (scripts.apply_text_edits, {"uri": "Assets/Foo.cs", "strict": True, "edits": [{"startLine": 0, "startCol": 1, "endLine": 1, "endCol": 1, "newText": "x"}]}),
    (scripts.apply_text_edits, {"uri": "Assets/Foo.cs", "edits": []}),
    (scripts.apply_text_edits, {"uri": "Assets/Foo.cs", "edits": [{"newText": "x"}]}),
    (scripts.apply_text_edits, {"uri": "Assets/Foo.cs", "edits": [{"range": [-1, 0], "text": "x"}]}),
    (scripts.apply_text_edits, {"uri": "Assets/Foo.cs", "edits": [{"range": [0, 0], "newText": 2}]}),
    (scripts.apply_text_edits, {"uri": "Assets/Foo.cs", "edits": [{"range": {"start": {"line": -1}}, "newText": "x"}]}),
    (edits.script_apply_edits, {"name": "Foo", "path": "Assets", "edits": []}),
    (edits.script_apply_edits, {"name": "Foo", "path": "Assets", "edits": '[1]'}),
    (edits.script_apply_edits, {"name": "Foo", "path": "Assets", "edits": [{"op": "replace_range", "text": "x"}]}),
    (edits.script_apply_edits, {"name": "Foo", "path": "Assets", "edits": [{"op": "regex_replace", "pattern": "[", "text": "x"}]}),
    (edits.script_apply_edits, {"name": "Foo", "path": "Assets", "edits": [{"op": "append", "text": 2}]}),
    (edits.script_apply_edits, {"name": "Foo", "path": "Assets", "edits": [{"op": "replace_method"}]}),
    (edits.script_apply_edits, {"name": "Foo", "path": "Assets", "edits": [{"op": "replace_class"}]}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,kwargs", INVALID_CALLS)
async def test_invalid_content_precedes_editor_io(editor_io, tool, kwargs):
    result = await tool(None, **kwargs)
    assert result["success"] is False
    if tool is finder.find_in_file and kwargs.get("pattern") == "[":
        assert result["message"].startswith("Regex search rejected: ")
    for spy in editor_io:
        spy.assert_not_awaited()


@pytest.mark.asyncio
async def test_scene_zero_and_false_reach_active_preflight(editor_io):
    result = await scenes.manage_scene(None, "load", build_index=0, additive="false")
    assert result["success"] is True
    _, readiness, refresh_call, transport = editor_io
    readiness.assert_awaited_once()
    refresh_call.assert_awaited_once()
    assert transport.await_args.args[3] == {"action": "load", "buildIndex": 0, "additive": False}


@pytest.mark.asyncio
async def test_scene_name_precedes_unused_negative_index(editor_io):
    result = await scenes.manage_scene(None, "load", name="Foo", build_index=-1)
    assert result["success"] is True
    editor_io[1].assert_awaited_once()


@pytest.mark.asyncio
async def test_lsp_zero_offsets_and_empty_text_still_read_document(editor_io):
    result = await scripts.apply_text_edits(None, "Assets/Foo.cs", [{
        "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 0}},
        "text": "",
    }], options={"preview": None})
    assert result["success"] is True
    assert editor_io[3].await_args_list[0].args[3]["action"] == "read"
    assert editor_io[3].await_args.args[3]["edits"][0]["newText"] == ""


@pytest.mark.asyncio
async def test_texture_clipped_negative_integer_and_false_sprite_reach_editor(editor_io):
    result = await textures.manage_texture(None, "modify", path="Assets/Foo.png", as_sprite=False,
        set_pixels={"x": -1, "y": 0, "color": [0, 0, 0, 255]})
    assert result["success"] is True
    editor_io[1].assert_awaited_once()
    assert editor_io[3].await_args.args[3]["setPixels"]["x"] == -1


@pytest.mark.asyncio
@pytest.mark.parametrize("flag,expected", [("yes", 1), ("1", 1), ("false", 0), ("0", 0), ("no", 0), (None, 0)])
async def test_search_preserves_legacy_case_flags(editor_io, flag, expected):
    result = await finder.find_in_file(None, "Assets/Foo.cs", "CLASS", ignore_case=flag, max_results=0)
    assert result["success"] is True
    assert len(result["data"]["matches"]) == expected
    editor_io[3].assert_awaited_once()


@pytest.mark.asyncio
async def test_search_valid_underscore_name_reaches_reader(editor_io):
    result = await finder.find_in_file(None, "Assets/_Foo.cs", "class")
    assert result["success"] is True
    assert editor_io[3].await_args.args[3]["name"] == "_Foo"


@pytest.mark.asyncio
@pytest.mark.parametrize("pixels", ["AAAAAA==", "base64:AAAAAA=="])
async def test_valid_rgba_base64_reaches_editor(editor_io, pixels):
    result = await textures.manage_texture(None, "create", path="Assets/Foo.png",
        width=1, height=1, pixels=pixels)
    assert result["success"] is True
    editor_io[1].assert_awaited_once()


@pytest.mark.asyncio
async def test_create_texture_keeps_native_import_settings_precedence(editor_io):
    result = await textures.manage_texture(None, "create", path="Assets/Foo.png",
        as_sprite=True, import_settings={"readable": False})
    assert result["success"] is True
    params = editor_io[3].await_args.args[3]
    assert params["importSettings"]["isReadable"] is False
    assert "spriteSettings" in params
