"""Stage a fresh UPM installation smoke without rewriting the distributed package manifest."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile

if __package__:
    from tools import unity_ci_packages as packages
else:
    import unity_ci_packages as packages


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tools/unity-ci/upm-smoke"


def matrix(manifest: Path) -> list[packages.MatrixRow]:
    """Select exact existing support roles; never create another Editor version pin."""
    config = packages.read_json(manifest)
    rows = [row for row in config["versions"] if row["role"] in {"floor", "current"}]
    if sorted(row["role"] for row in rows) != ["current", "floor"]:
        raise ValueError("UPM smoke requires exactly one floor and one current Unity role")
    for row in rows:
        packages.unity_numbers(row["id"])
    return [{"version": row["id"], "channel": row["channel"]} for row in rows]


def prepare(unity_version: str, output: Path, *, repo: Path = ROOT) -> Path:
    """Copy only the package and a minimal native fixture; UPM owns the dependency closure."""
    packages.unity_numbers(unity_version)
    repo = repo.resolve()
    output = output.absolute()
    for path in (output, *output.parents):
        if path.is_symlink() or (
            path.exists()
            and getattr(path.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise packages.PreparationError(f"Linked smoke output path: {path}")
    output = output.resolve()
    if (
        not output.is_relative_to(repo)
        or output == repo
        or output.exists()
        or any(
            output.is_relative_to(repo / name) for name in ("MCPForUnity", "TestProjects", "tools")
        )
    ):
        raise packages.PreparationError("Smoke output must be a new isolated repository directory")
    original = (repo / "MCPForUnity/package.json").read_bytes()
    metadata = packages.read_json(repo / "MCPForUnity/package.json")
    if not packages.PACKAGE_NAME.fullmatch(metadata["name"]):
        raise packages.PreparationError("Invalid distributed package identity")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="upm-smoke-", dir=output.parent) as temporary:
        scratch = Path(temporary)
        packages.copy_source(repo / "MCPForUnity", scratch / "package")
        if (scratch / "package/package.json").read_bytes() != original:
            raise packages.PreparationError("Distributed package manifest changed during staging")
        project = scratch / "project"
        packages.copy_source(FIXTURE, project / "Assets/Tests")
        (project / "Packages").mkdir()
        (project / "ProjectSettings").mkdir()
        (project / "ProjectSettings/ProjectVersion.txt").write_text(
            f"m_EditorVersion: {unity_version}\n", encoding="utf-8"
        )
        (project / "Packages/manifest.json").write_text(
            json.dumps({"dependencies": {metadata["name"]: "file:../../package"}}, indent=2) + "\n",
            encoding="utf-8",
        )
        expected = {
            "packageName": metadata["name"],
            "manifestSha256": hashlib.sha256(original).hexdigest(),
            "dependencies": [
                {"name": name, "version": version}
                for name, version in metadata.get("dependencies", {}).items()
            ],
        }
        (project / ".upm-smoke.json").write_text(
            json.dumps(expected, indent=2) + "\n", encoding="utf-8"
        )
        scratch.replace(output)
    return output / "project"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    selection = commands.add_parser("matrix")
    selection.add_argument("--manifest", type=Path, default=ROOT / "tools/unity-versions.json")
    stage = commands.add_parser("prepare")
    stage.add_argument("--unity-version", required=True)
    stage.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "matrix":
            print(json.dumps(matrix(args.manifest)))
        else:
            project = prepare(args.unity_version, args.output)
            relative = project.relative_to(ROOT).as_posix()
            if output := os.environ.get("GITHUB_OUTPUT"):
                with Path(output).open("a", encoding="utf-8") as stream:
                    stream.write(f"project_path={relative}\n")
            print(json.dumps({"project_path": relative}))
    except (OSError, ValueError, KeyError, packages.PreparationError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
