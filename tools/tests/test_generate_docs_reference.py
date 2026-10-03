import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType
from typing import Annotated, Union

import pytest
from pydantic import BeforeValidator, Field

from tools.generate_docs_reference import _annotation_description, _diff_trees, _render_type
from tools import generate_docs_reference as docs


def test_pep604_and_typing_unions_render_bare_collections_consistently():
    cases = [
        (list | None, Union[list, None], "list[Any] | None"),
        (dict | bool | None, Union[dict, bool, None], "dict[Any] | bool | None"),
        (None | tuple | str, Union[None, tuple, str], "tuple[Any] | str | None"),
    ]
    for modern, legacy, expected in cases:
        assert _render_type(modern) == expected
        assert _render_type(legacy) == expected


def test_union_rendering_preserves_nested_generic_types():
    modern = list[dict[str, int | None]] | None
    legacy = Union[list[dict[str, Union[int, None]]], None]
    expected = "list[dict[str, int | None]] | None"
    assert _render_type(modern) == expected
    assert _render_type(legacy) == expected


def test_union_description_preserves_nested_annotated_metadata():
    described = Annotated[str, "Nested description"]
    modern = Annotated[described | None, 7]
    legacy = Annotated[Union[described, None], 7]
    assert _annotation_description(modern) == "Nested description"
    assert _annotation_description(legacy) == "Nested description"


def test_unannotated_unions_have_no_description():
    assert _annotation_description(str | None) is None
    assert _annotation_description(Union[str, None]) is None


def test_field_description_survives_validator_metadata_and_optional_union():
    described = Annotated[int | None, Field(description="Component index"), BeforeValidator(lambda value: value)]
    assert _annotation_description(described) == "Component index"
    assert _annotation_description(Annotated[described | str, 7]) == "Component index"
    assert _render_type(described) == "int | None"


def test_existing_string_description_precedes_field_metadata():
    described = Annotated[int, Field(description="Field description"), "Existing description"]
    assert _annotation_description(described) == "Existing description"
    assert _annotation_description(Annotated[int, Field(), 7]) is None


@pytest.mark.parametrize("committed,generated,expected", [
    (b"same\n", b"same\r\n", []),
    (b"old\n", b"new\n", ["differs: index.md"]),
])
def test_reference_drift_compares_text_not_newlines_or_file_metadata(
    tmp_path: Path, committed: bytes, generated: bytes, expected: list[str],
) -> None:
    left, right = tmp_path / "committed", tmp_path / "generated"
    for directory, content in ((left, committed), (right, generated)):
        directory.mkdir()
        path = directory / "index.md"
        path.write_bytes(content)
        os.utime(path, (1_700_000_000, 1_700_000_000))

    assert _diff_trees(left, right) == expected


def test_registry_loading_preserves_cached_module_registrations() -> None:
    # Given: an isolated interpreter with the real registry and its decorated modules.
    script = """
from tools.generate_docs_reference import load_registries
first = load_registries()
second = load_registries()
assert all(first), 'expected populated tool and resource registries'
assert [[item['name'] for item in group] for group in second] == [
    [item['name'] for item in group] for group in first
], 'cached imports must retain their registrations'
"""
    # When: registry loading is repeated without restarting Python.
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=docs.REPO_ROOT,
        capture_output=True, text=True, check=False,
    )
    # Then: both calls contain the same registered tools and resources.
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("new_group", [None, "new_group"])
def test_regeneration_removes_obsolete_generated_pages_and_passes_drift_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, new_group: str | None,
) -> None:
    # Given: generated documentation for a tool that is later removed or regrouped.
    def example_tool() -> None:
        pass

    tools = [{"name": "example_tool", "group": "old_group", "func": example_tool}]
    tools_root, resources_root = tmp_path / "tools", tmp_path / "resources"
    monkeypatch.setattr(docs, "load_registries", lambda: (tools, []))
    monkeypatch.setattr(docs, "TOOLS_OUT", tools_root)
    monkeypatch.setattr(docs, "RESOURCES_OUT", resources_root)
    docs.generate(tools_root, resources_root)
    old_page = tools_root / "old_group" / "example_tool.md"
    old_page.write_text(
        old_page.read_text(encoding="utf-8").replace(
            docs.EXAMPLES_PLACEHOLDER,
            f"{docs.EXAMPLES_OPEN}\nPreserved usage example.\n{docs.EXAMPLES_CLOSE}\n",
        ), encoding="utf-8",
    )
    if new_group is None:
        tools.clear()
    else:
        tools[0]["group"] = new_group

    # When: contributors follow the drift check's instruction to regenerate.
    docs.generate(tools_root, resources_root)

    # Then: obsolete generated pages disappear and --check accepts the regenerated tree.
    assert not old_page.exists()
    assert not (tools_root / "old_group" / "index.md").exists()
    assert not (tools_root / "old_group" / "_category_.json").exists()
    if new_group is not None:
        assert "Preserved usage example." in (
            tools_root / new_group / "example_tool.md"
        ).read_text(encoding="utf-8")
    assert docs.main(["--check"]) == 0


def test_regeneration_and_drift_check_preserve_authored_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: authored documentation alongside an obsolete generated tool page.
    def example_tool() -> None:
        pass

    tools = [{"name": "example_tool", "group": "old_group", "func": example_tool}]
    tools_root, resources_root = tmp_path / "tools", tmp_path / "resources"
    monkeypatch.setattr(docs, "load_registries", lambda: (tools, []))
    monkeypatch.setattr(docs, "TOOLS_OUT", tools_root)
    monkeypatch.setattr(docs, "RESOURCES_OUT", resources_root)
    docs.generate(tools_root, resources_root)
    authored = tools_root / "old_group" / "handbook.md"
    authored.write_text("# Hand-authored guide\nKeep this text.\n", encoding="utf-8")
    authored_image = tools_root / "old_group" / "illustration.png"
    authored_image.write_bytes(b"\x89PNG\r\n\x1a\n")
    custom_category = tools_root / "old_group" / "_category_.json"
    custom_category.write_text('{"label": "Authored category"}\n', encoding="utf-8")
    authored_index = tools_root / "old_group" / "index.md"
    authored_index.write_text("# Authored category landing page\n", encoding="utf-8")
    tools.clear()

    # When: the live tool is removed and its reference is regenerated.
    docs.generate(tools_root, resources_root)

    # Then: authored content survives both generation and the temporary drift check.
    assert authored.read_text(encoding="utf-8") == "# Hand-authored guide\nKeep this text.\n"
    assert authored_image.read_bytes() == b"\x89PNG\r\n\x1a\n"
    assert custom_category.read_text(encoding="utf-8") == '{"label": "Authored category"}\n'
    assert authored_index.read_text(encoding="utf-8") == "# Authored category landing page\n"
    assert docs.main(["--check"]) == 0


@pytest.mark.parametrize("new_group", [None, "new_group"])
def test_drift_check_detects_obsolete_pages_without_modifying_committed_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, new_group: str | None,
) -> None:
    # Given: a committed generated tree whose tool registry changed.
    def example_tool() -> None:
        pass

    tools = [{"name": "example_tool", "group": "old_group", "func": example_tool}]
    tools_root, resources_root = tmp_path / "tools", tmp_path / "resources"
    monkeypatch.setattr(docs, "load_registries", lambda: (tools, []))
    monkeypatch.setattr(docs, "TOOLS_OUT", tools_root)
    monkeypatch.setattr(docs, "RESOURCES_OUT", resources_root)
    docs.generate(tools_root, resources_root)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    if new_group is None:
        tools.clear()
    else:
        tools[0]["group"] = new_group

    # When: CI checks the documentation without regenerating committed output.
    result = docs.main(["--check"])

    # Then: stale owned output is detected and the original tree stays byte-for-byte intact.
    assert result == 1
    assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before


def test_generation_stops_before_writes_when_a_public_module_cannot_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: discoverable tool source with an import failure and existing owned output.
    tools_package = tmp_path / "tool_source"
    tools_package.mkdir()
    (tools_package / "broken_tool.py").write_text("raise ImportError('broken tool')\n", encoding="utf-8")
    resources_package = tmp_path / "resource_source"
    resources_package.mkdir()
    packages = {}
    for name, directory in (("services.tools", tools_package), ("services.resources", resources_package)):
        module = ModuleType(name)
        module.__file__ = str(directory / "__init__.py")
        packages[name] = module
    real_import = docs.importlib.import_module

    def import_module(name: str, package: str | None = None) -> ModuleType:
        if name in packages:
            return packages[name]
        if name == "services.tools.broken_tool" or (package == "services.tools" and name == ".broken_tool"):
            raise ImportError("broken tool")
        return real_import(name, package)

    monkeypatch.setattr(docs.importlib, "import_module", import_module)
    tools_root, resources_root = tmp_path / "docs_tools", tmp_path / "docs_resources"
    tools_root.mkdir()
    sentinel = tools_root / "index.md"
    sentinel.write_text("Existing reference must stay intact.\n", encoding="utf-8")

    # When: generation discovers a public module that cannot be loaded.
    with pytest.raises(ImportError, match="broken tool"):
        docs.generate(tools_root, resources_root)

    # Then: no catalogs or pages have been overwritten or pruned.
    assert sentinel.read_text(encoding="utf-8") == "Existing reference must stay intact.\n"
    assert not resources_root.exists()
