"""Script locators preserve the requested file until the selected Editor checks it."""

import importlib
from unittest.mock import AsyncMock

import pytest

scripts = importlib.import_module("services.tools.manage_script")
search = importlib.import_module("services.tools.find_in_file")
structured = importlib.import_module("services.tools.script_apply_edits")


@pytest.mark.parametrize("filename", ["Foo.json", "Foo%2easset"])
def test_structured_locator_rejects_non_script_suffix(filename):
    with pytest.raises(ValueError, match="suffix"):
        structured._normalize_script_locator(filename, "Assets/Scripts")


@pytest.mark.parametrize("module", [scripts, search])
@pytest.mark.parametrize(
    "uri,directory",
    [
        ("file:///C:/Assets/Game/Assets/Scripts/Foo.cs", "C:/Assets/Game/Assets/Scripts"),
        ("file:///work/Assets/Game/Assets/Scripts/Foo.cs", "/work/Assets/Game/Assets/Scripts"),
        ("file://server/share/Assets/Game/Assets/Foo.cs", "//server/share/Assets/Game/Assets"),
        ("C:/OtherProject/Assets/Foo.cs", "C:/OtherProject/Assets"),
        ("Assets/Nested/Assets/Foo.cs", "Assets/Nested/Assets"),
        ("mcpforunity://path/Assets/Scripts%252fNested/Foo.cs", "Assets/Scripts%2fNested"),
    ],
)
def test_script_uri_retains_absolute_project_identity(module, uri, directory):
    # Given a path containing several possible Assets segments or encoded characters.
    # When the shared wire locator is parsed.
    name, actual_directory = module._split_uri(uri)
    # Then only the selected Editor may reduce an absolute path to its own root.
    assert (name, actual_directory) == ("Foo", directory)


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", [".json", ".asset", "%2ejson"])
@pytest.mark.parametrize(
    "entrypoint", ["delete_script", "validate_script", "get_sha", "apply_text_edits"]
)
async def test_non_script_suffix_is_rejected_before_dispatch(monkeypatch, suffix, entrypoint):
    # Given a non-C# sibling of an existing C# script and an isolated transport boundary.
    sender = AsyncMock(return_value={"success": True, "data": {}})
    monkeypatch.setattr(scripts, "send_mutation", sender)
    monkeypatch.setattr(scripts, "send_with_unity_instance", sender)
    monkeypatch.setattr(scripts, "get_unity_instance_from_context", AsyncMock(return_value=None))
    parameters = {"ctx": None, "uri": "Assets/Scripts/Foo" + suffix}
    if entrypoint == "apply_text_edits":
        parameters["edits"] = [
            {"startLine": 1, "startCol": 1, "endLine": 1, "endCol": 1, "newText": ""}
        ]
    # When the public wrapper receives that exact filename.
    response = await getattr(scripts, entrypoint)(**parameters)
    # Then it refuses instead of dropping the suffix and addressing Foo.cs.
    assert response["success"] is False
    assert sender.await_count == 0


@pytest.mark.asyncio
async def test_delete_preserves_absolute_directory_for_editor_containment(monkeypatch):
    # Given a project path whose ancestor and asset subtree both contain Assets.
    sender = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(scripts, "send_mutation", sender)
    monkeypatch.setattr(
        scripts, "get_unity_instance_from_context", AsyncMock(return_value="selected")
    )
    # When deleting by an absolute URI.
    result = await scripts.delete_script(None, "file:///C:/Assets/Game/Assets/Nested/Assets/Foo.cs")
    # Then the original directory reaches the selected Editor's containment check.
    assert result["success"] is True
    assert sender.await_args.args[1:4] == (
        "selected",
        "manage_script",
        {"action": "delete", "name": "Foo", "path": "C:/Assets/Game/Assets/Nested/Assets"},
    )
