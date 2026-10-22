"""Prepare synthetic source files before starting an isolated Unity measurement."""

from __future__ import annotations

import argparse
from pathlib import Path
import uuid


def prepare(project: Path) -> Path:
    project = project.absolute()
    for parent in (project, *project.parents):
        if parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction()):
            raise ValueError("The measurement project must not use linked ancestors")
    if not (project / "Assets").is_dir() or (project / "Temp/UnityLockfile").exists():
        raise ValueError("An existing, closed isolated Unity project is required")
    root = project / "Assets/__McpReferenceMeasureFixtures_e11"
    if root.exists() or Path(str(root) + ".meta").exists():
        raise ValueError("Measurement fixtures already exist; no files were overwritten")
    if (project / "Assets").is_symlink() or (
        hasattr(project / "Assets", "is_junction") and (project / "Assets").is_junction()
    ):
        raise ValueError("The Assets directory must not be a link")
    root.mkdir()
    payload = "x" * (8 * 1024 * 1024 + 123)
    (root / "Owned.cs").write_text("/*" + payload + "*/\n", encoding="utf-8", newline="\n")
    (root / "Owned.asset").write_text(
        "%YAML 1.1\n%TAG !u! tag:unity3d.com,2011:\n--- !u!49 &4900000\nTextAsset:\n"
        "  m_ObjectHideFlags: 0\n  m_Name: Owned\n  m_Script: " + payload + "\n",
        encoding="utf-8",
        newline="\n",
    )
    for path in (root, root / "Owned.cs", root / "Owned.asset"):
        Path(str(path) + ".meta").write_text(
            "fileFormatVersion: 2\nguid: "
            + uuid.uuid4().hex
            + ("\nfolderAsset: yes" if path.is_dir() else "")
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
    return root


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--isolated-project", required=True, type=Path)
    args = parser.parse_args()
    print(prepare(args.isolated_project))


if __name__ == "__main__":
    main()
