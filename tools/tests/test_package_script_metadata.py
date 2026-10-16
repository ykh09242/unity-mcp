"""Keep owned package scripts importable without Unity generating missing metadata."""

from pathlib import Path
import re
from typing import Final

import pytest


PACKAGE: Final = Path(__file__).resolve().parents[2] / "MCPForUnity"
GUID: Final = re.compile(r"^guid: ([0-9a-fA-F]{32})[ \t]*$", re.MULTILINE)
SCRIPT_METADATA: Final = (
    "fileFormatVersion: 2\nguid: {guid}\nMonoImporter:\n"
    "  externalObjects: {{}}\n  serializedVersion: 2\n"
)


def _metadata_issues(package: Path) -> list[str]:
    """Inspect the shipped tree, including collisions with non-script package assets."""
    issues: list[str] = []
    for script in sorted(package.rglob("*.cs")):
        metadata = script.with_suffix(".cs.meta")
        if not metadata.is_file():
            issues.append(f"Missing script metadata: {script.relative_to(package)}")
            continue
        text = metadata.read_text(encoding="utf-8")
        guids = GUID.findall(text)
        if (
            len(guids) != 1
            or len(re.findall(r"^guid:.*$", text, re.MULTILINE)) != 1
            or int(guids[0], 16) == 0
        ):
            issues.append(f"Invalid script GUID: {metadata.relative_to(package)}")
        if not re.search(r"^fileFormatVersion: 2$", text, re.MULTILINE):
            issues.append(f"Invalid metadata format: {metadata.relative_to(package)}")

    owners: dict[str, Path] = {}
    for metadata in sorted(package.rglob("*.meta")):
        for guid in GUID.findall(metadata.read_text(encoding="utf-8")):
            normalized = guid.lower()
            if normalized in owners:
                issues.append(
                    f"Duplicate GUID {normalized}: {owners[normalized].relative_to(package)}"
                    f" and {metadata.relative_to(package)}"
                )
            owners[normalized] = metadata
    return issues


def _write_script(package: Path, relative: str, guid: str) -> Path:
    script = package / relative
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("class Fixture {}\n", encoding="utf-8")
    metadata = script.with_suffix(".cs.meta")
    metadata.write_text(SCRIPT_METADATA.format(guid=guid), encoding="utf-8")
    return metadata


def test_shipped_package_scripts_have_valid_unique_metadata() -> None:
    assert list(PACKAGE.rglob("*.cs")), "The package script selection must not be empty"
    assert _metadata_issues(PACKAGE) == []


def test_nested_editor_and_runtime_scripts_with_unique_metadata_pass(tmp_path: Path) -> None:
    _write_script(tmp_path, "Editor/Security/Helper.cs", "1" * 32)
    metadata = _write_script(tmp_path, "Runtime/Component.cs", "2" * 32)
    metadata.write_text(f"fileFormatVersion: 2\nguid: {'2' * 32}\n", encoding="utf-8")
    assert _metadata_issues(tmp_path) == []


def test_missing_script_metadata_is_reported(tmp_path: Path) -> None:
    metadata = _write_script(tmp_path, "Editor/Helper.cs", "1" * 32)
    metadata.unlink()
    assert _metadata_issues(tmp_path) == [f"Missing script metadata: {Path('Editor/Helper.cs')}"]


@pytest.mark.parametrize("guid", ["", "short", "g" * 32, "0" * 32, "1" * 33])
def test_invalid_script_guid_is_reported(tmp_path: Path, guid: str) -> None:
    _write_script(tmp_path, "Helper.cs", guid)
    assert _metadata_issues(tmp_path) == ["Invalid script GUID: Helper.cs.meta"]


def test_multiple_guid_fields_are_reported(tmp_path: Path) -> None:
    metadata = _write_script(tmp_path, "Helper.cs", "1" * 32)
    with metadata.open("a", encoding="utf-8") as stream:
        stream.write(f"guid: {'2' * 32}\n")
    assert _metadata_issues(tmp_path) == ["Invalid script GUID: Helper.cs.meta"]


def test_missing_metadata_format_is_reported(tmp_path: Path) -> None:
    metadata = _write_script(tmp_path, "Helper.cs", "1" * 32)
    metadata.write_text(f"guid: {'1' * 32}\nDefaultImporter:\n", encoding="utf-8")
    assert _metadata_issues(tmp_path) == ["Invalid metadata format: Helper.cs.meta"]


@pytest.mark.parametrize("other_asset", ["Other.cs", "Texture.png"])
def test_script_guid_collision_with_package_asset_is_reported(
    tmp_path: Path, other_asset: str
) -> None:
    _write_script(tmp_path, "Helper.cs", "a" * 32)
    asset = tmp_path / other_asset
    asset.write_text("fixture", encoding="utf-8")
    asset.with_suffix(f"{asset.suffix}.meta").write_text(
        SCRIPT_METADATA.format(guid="A" * 32), encoding="utf-8"
    )
    issues = _metadata_issues(tmp_path)
    assert len(issues) == 1
    assert issues[0] == f"Duplicate GUID {'a' * 32}: Helper.cs.meta and {other_asset}.meta"
