"""Create a fresh, reproducible Unity lifecycle sample; never launch or install Unity."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

from unity_ci_packages import copy_source

REPOSITORY = Path(__file__).resolve().parents[1]
TEMPLATE = Path("tools/fixtures/play_scenario_lifecycle")
CACHED_PACKAGES = (
    "com.unity.editorcoroutines",
    "com.unity.ext.nunit",
    "com.unity.nuget.newtonsoft-json",
    "com.unity.test-framework",
    "com.unity.ugui",
)


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.check_output(["git", "-C", str(repository), *arguments], text=True).strip()


def _hashes(folder: Path) -> dict[str, str]:
    return {
        path.relative_to(folder).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(folder.rglob("*"))
        if path.is_file()
    }


def create_sample(
    output: Path, package_cache: Path | None = None, *, repository: Path = REPOSITORY
) -> Path:
    """Copy the template and clean tracked package into a never-existing destination."""
    repository = repository.resolve()
    template = repository / TEMPLATE
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"Sample destination already exists: {output}")
    output = output.resolve()
    if output.is_relative_to(repository) and not output.is_relative_to(repository / ".tmp"):
        raise ValueError("Choose a destination outside source folders (or under .tmp).")
    if package_cache is not None and output.is_relative_to(package_cache.resolve()):
        raise ValueError("Choose a destination outside the package cache source.")
    if _git(repository, "status", "--porcelain", "--untracked-files=all", "--", "MCPForUnity"):
        raise ValueError("MCPForUnity must be clean before snapshotting its commit.")
    revision = _git(repository, "rev-parse", "HEAD")
    tracked = _git(repository, "ls-files", "-z", "--", "MCPForUnity").split("\0")
    sources = []
    for relative in filter(None, tracked):
        path = repository / relative
        if (
            path.is_symlink()
            or not path.is_file()
            or not path.resolve().is_relative_to(repository / "MCPForUnity")
            or path.name.startswith(".env")
            or path.suffix.lower() in {".key", ".pem", ".pfx", ".p12"}
            or "Assets/Resources/GameData" in path.as_posix()
        ):
            raise ValueError("The tracked package contains an unsupported source path.")
        sources.append(path)
    if not sources:
        raise ValueError("The clean tracked package is empty.")
    manifest = json.loads((template / "Packages/manifest.json").read_text(encoding="utf-8"))
    dependencies = manifest["dependencies"]
    if package_cache is not None:
        for name in CACHED_PACKAGES:
            folder = (package_cache / name).resolve()
            metadata = folder / "package.json"
            if not metadata.is_file() or folder.is_symlink():
                raise ValueError(f"Required local package cache is unavailable: {name}")
            package = json.loads(metadata.read_text(encoding="utf-8-sig"))
            if package.get("name") != name or package.get("version") != dependencies[name]:
                raise ValueError(f"Local package cache identity/version mismatch: {name}")
            dependencies[name] = "file:" + folder.as_posix()
    dependencies["com.ykh09242.unity-mcp"] = "file:../../package"
    output.mkdir(parents=True, exist_ok=False)
    project = output / "project"
    copy_source(template, project)
    package_root = output / "package"
    for source in sources:
        target = package_root / source.relative_to(repository / "MCPForUnity")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    (project / "Packages/manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    provenance = {
        "schema_version": 1,
        "source_revision": revision,
        "project": str(project),
        "package_sha256": _hashes(package_root),
        "project_sha256": _hashes(project),
        "local_package_cache": str(package_cache.resolve()) if package_cache else None,
    }
    (output / "sample-provenance.json").write_text(
        json.dumps(provenance, indent=2) + "\n", encoding="utf-8"
    )
    return project


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="New destination root.")
    parser.add_argument(
        "--package-cache", type=Path, help="Existing packages with pinned versions."
    )
    args = parser.parse_args()
    try:
        project = create_sample(args.output, args.package_cache)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(2, f"Sample creation failed: {error}\n")
    print(project)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
