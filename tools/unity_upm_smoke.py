"""Stage a fresh UPM installation smoke without rewriting the distributed package manifest."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile

if __package__:
    from tools import unity_ci_packages as packages
else:
    import unity_ci_packages as packages


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tools/unity-ci/upm-smoke"
PACKAGE_FOLDER = "MCPForUnity"


def matrix(manifest: Path) -> list[packages.MatrixRow]:
    """Select exact existing support roles; never create another Editor version pin."""
    config = packages.read_json(manifest)
    rows = [row for row in config["versions"] if row["role"] in {"floor", "current"}]
    if sorted(row["role"] for row in rows) != ["current", "floor"]:
        raise ValueError("UPM smoke requires exactly one floor and one current Unity role")
    for row in rows:
        packages.unity_numbers(row["id"])
    return [{"version": row["id"], "channel": row["channel"]} for row in rows]


def git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "--no-replace-objects", "-C", str(repo), *args], capture_output=True, check=False
    )
    if result.returncode:
        raise packages.PreparationError(
            f"Git {args[0]} failed: {result.stderr.decode(errors='replace')}"
        )
    return result.stdout


def prepare(
    unity_version: str,
    output: Path,
    *,
    repo: Path = ROOT,
    git_revision: str | None = None,
    editor_workspace: Path | None = None,
) -> Path:
    """Stage a clean project resolving the local folder or an exact Git dependency."""
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
    if git_revision is not None and (
        not re.fullmatch(r"[0-9a-f]{40}", git_revision)
        or git(repo, "rev-parse", "HEAD").decode().strip() != git_revision
    ):
        raise packages.PreparationError("Git smoke requires the exact checked-out candidate commit")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="upm-smoke-", dir=output.parent) as temporary:
        scratch = Path(temporary)
        if git_revision is None:
            original = (repo / PACKAGE_FOLDER / "package.json").read_bytes()
            packages.copy_source(repo / PACKAGE_FOLDER, scratch / "package")
            if (scratch / "package/package.json").read_bytes() != original:
                raise packages.PreparationError(
                    "Distributed package manifest changed during staging"
                )
            dependency = "file:../../package"
        else:
            mirror = scratch / "source.git"
            git(repo, "init", "--bare", "--initial-branch=candidate", str(mirror))
            git(
                mirror,
                "fetch",
                "--depth=1",
                "--update-shallow",
                "--no-tags",
                repo.as_uri(),
                f"{git_revision}:refs/heads/candidate",
            )
            if git(mirror, "rev-parse", "HEAD").decode().strip() != git_revision:
                raise packages.PreparationError(
                    "Staged Git mirror does not contain the requested HEAD"
                )
            original = git(mirror, "show", f"{git_revision}:{PACKAGE_FOLDER}/package.json")
            editor_mirror = (editor_workspace or repo) / output.relative_to(repo) / mirror.name
            dependency = f"{editor_mirror.as_uri()}?path=/{PACKAGE_FOLDER}#{git_revision}"
        metadata = json.loads(original)
        if not isinstance(metadata, dict) or not packages.PACKAGE_NAME.fullmatch(metadata["name"]):
            raise packages.PreparationError("Invalid distributed package identity")
        project = scratch / "project"
        packages.copy_source(FIXTURE, project / "Assets/Tests")
        (project / "Packages").mkdir()
        (project / "ProjectSettings").mkdir()
        (project / "ProjectSettings/ProjectVersion.txt").write_text(
            f"m_EditorVersion: {unity_version}\n", encoding="utf-8"
        )
        (project / "Packages/manifest.json").write_text(
            json.dumps({"dependencies": {metadata["name"]: dependency}}, indent=2) + "\n",
            encoding="utf-8",
        )
        expected = {
            "packageName": metadata["name"],
            "manifestSha256": hashlib.sha256(original).hexdigest(),
            "gitRevision": git_revision,
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


def check_git_lock(project: Path, revision: str) -> None:
    """Require the direct UPM lock entry to select the same Git mirror, subfolder and commit."""
    expected = packages.read_json(project / ".upm-smoke.json")
    name = expected["packageName"]
    request = packages.read_json(project / "Packages/manifest.json")["dependencies"][name]
    entry = packages.read_json(project / "Packages/packages-lock.json")["dependencies"][name]
    if (
        not re.fullmatch(r"[0-9a-f]{40}", revision)
        or expected["gitRevision"] != revision
        or not request.startswith("file:///")
        or not request.endswith(f"/source.git?path=/{PACKAGE_FOLDER}#{revision}")
        or entry.get("version") != request
        or entry.get("source") != "git"
        or entry.get("depth") != 0
        or entry.get("hash") != revision
    ):
        raise packages.PreparationError(
            "UPM lock must select the candidate Git commit and package subfolder"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    selection = commands.add_parser("matrix")
    selection.add_argument("--manifest", type=Path, default=ROOT / "tools/unity-versions.json")
    stage = commands.add_parser("prepare")
    stage.add_argument("--unity-version", required=True)
    stage.add_argument("--output", type=Path, required=True)
    stage.add_argument(
        "--git-revision", help="Install the exact checked-out commit from a bare mirror"
    )
    stage.add_argument(
        "--editor-workspace", type=Path, help="Repository mount inside the Editor container"
    )
    lock = commands.add_parser("check-git-lock")
    lock.add_argument("--project", required=True, type=Path)
    lock.add_argument("--revision", required=True)
    args = parser.parse_args()
    try:
        if args.command == "matrix":
            print(json.dumps(matrix(args.manifest)))
        elif args.command == "check-git-lock":
            check_git_lock(args.project, args.revision)
        else:
            project = prepare(
                args.unity_version,
                args.output,
                git_revision=args.git_revision,
                editor_workspace=args.editor_workspace,
            )
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
