"""Cache immutable optional Unity dependencies and freshly prepare the current source every run."""

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
from typing import TypedDict

import unity_ci
import unity_ci_packages as packages


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = 2
BUILTINS = Path("Resources/PackageManager/BuiltInPackages")
LOGIC_INPUTS = (
    "tools/unity_ci_package_cache.py",
    "tools/unity_ci_packages.py",
    "tools/unity_ci.py",
    "tools/unity-ci/Dockerfile",
)
MANIFEST_INPUTS = (
    "MCPForUnity/package.json",
    "TestProjects/UnityMCPTests/Packages/manifest.json",
)


@dataclass(frozen=True, slots=True)
class CacheRequest:
    version: str
    repo: Path
    manifest_path: Path
    profiles_path: Path


@dataclass(frozen=True, slots=True)
class CacheIdentity:
    cache_key: str
    cache_path: str
    provenance: str
    registries: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class CachePreparation:
    preparation: packages.Preparation
    populated: bool


class InputRecord(TypedDict, total=False):
    kind: str
    mode: int
    size: int
    sha256: str


def _linked(path: Path) -> bool:
    if path.is_symlink():
        return True
    return path.exists() and bool(
        getattr(path.lstat(), "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def destination(request: CacheRequest) -> Path:
    """Restrict cache writes to a single version directory inside the selected repository."""
    packages.unity_numbers(request.version)
    root = request.repo.absolute()
    if root != ROOT.absolute():
        raise packages.PreparationError("Optional dependency cache must use the current repository")
    target = root / ".unity-ci" / "optional-inputs" / request.version
    for path in (target, *target.parents):
        if _linked(path):
            raise packages.PreparationError(f"Linked optional dependency cache path: {path}")
    return target


def identity(request: CacheRequest) -> CacheIdentity:
    """Include every dependency selector and preparation implementation, excluding source bodies."""
    destination(request)
    manifest = unity_ci.load_manifest(request.manifest_path)
    row = next((item for item in manifest.versions if item.id == request.version), None)
    if row is None or row.test_image is None:
        raise packages.PreparationError("Optional input cache requires a digest-pinned test image")
    config = packages.read_json(request.profiles_path)
    major, minor, _ = packages.unity_numbers(request.version)
    profiles = [
        profile
        for profile in config["profiles"].values()
        if f"{major}.{minor}" in profile.get("unityFamilies", [])
        or major in profile.get("unityMajors", [])
    ]
    optional = packages.optional_matches(config, request.version)
    if config.get("schemaVersion") != 1 or len(profiles) != 1 or len(optional) != 1:
        raise packages.PreparationError("Exactly one reviewed base and optional profile required")
    specs = {**profiles[0]["packages"], **optional[0][1]["packages"]}
    registries = []
    for name, spec in sorted(specs.items()):
        if not packages.PACKAGE_NAME.fullmatch(name):
            raise packages.PreparationError(f"Invalid package name: {name}")
        if spec["source"] == "registry":
            packages.package_numbers(spec["version"])
            registries.append((name, spec["version"]))
    hashes = {
        relative: hashlib.sha256((request.repo / relative).read_bytes()).hexdigest()
        for relative in (*LOGIC_INPUTS, *MANIFEST_INPUTS)
    }
    hashes["versionManifest"] = hashlib.sha256(request.manifest_path.read_bytes()).hexdigest()
    hashes["profiles"] = hashlib.sha256(request.profiles_path.read_bytes()).hexdigest()
    provenance = json.dumps(
        {
            "schema": SCHEMA,
            "platform": "linux/amd64",
            "version": request.version,
            "image": row.test_image,
            "inputs": hashes,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(provenance.encode()).hexdigest()
    return CacheIdentity(
        f"unity-ci-optional-v{SCHEMA}-linux-amd64-{request.version}-{digest}",
        f".unity-ci/optional-inputs/{request.version}",
        provenance,
        tuple(registries),
    )


def inventory(directory: Path) -> dict[str, InputRecord]:
    """Hash the complete restored inventory without following links or inspecting excluded data."""
    records: dict[str, InputRecord] = {}
    for parent, directories, files in os.walk(directory, followlinks=False):
        for name in sorted((*directories, *files)):
            path = Path(parent) / name
            relative = path.relative_to(directory)
            if relative.as_posix() == "receipt.json":
                continue
            if (
                name.startswith(".env")
                or name.casefold() == "gamedata"
                or path.suffix.lower() in {".key", ".pem", ".pfx", ".p12"}
            ):
                raise packages.PreparationError("Excluded input in optional dependency cache")
            if _linked(path):
                raise packages.PreparationError(f"Linked optional dependency input: {relative}")
            details = path.stat()
            record: InputRecord = {"mode": stat.S_IMODE(details.st_mode)}
            if stat.S_ISDIR(details.st_mode):
                record["kind"] = "directory"
            elif stat.S_ISREG(details.st_mode):
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    while chunk := stream.read(1024 * 1024):
                        digest.update(chunk)
                record.update(kind="file", size=details.st_size, sha256=digest.hexdigest())
            else:
                raise packages.PreparationError("Optional dependency inputs must be ordinary files")
            records[relative.as_posix()] = record
    return records


def _package_names(value: list[str]) -> tuple[str, ...]:
    """Parse the exact Editor package membership before it is used for filesystem operations."""
    if (
        not isinstance(value, list)
        or not value
        or any(
            not isinstance(name, str) or not packages.PACKAGE_NAME.fullmatch(name) for name in value
        )
        or len(set(value)) != len(value)
    ):
        raise packages.PreparationError("Invalid exact Editor package inventory")
    return tuple(sorted(value))


def _editor_packages(report_path: Path) -> tuple[str, ...]:
    report = packages.read_json(report_path)
    return _package_names(
        [package["name"] for package in report["packages"] if package["source"] == "editor"]
    )


def _compact_editor_inputs(directory: Path, editor_packages: tuple[str, ...]) -> None:
    """Move complete validated packages inside owned staging; leave other image inputs unpublished."""
    data = directory / "Data"
    full = directory.parent / "full-editor-inputs"
    if _linked(data) or any(
        _linked(data / BUILTINS / name) or not (data / BUILTINS / name).is_dir()
        for name in editor_packages
    ):
        raise packages.PreparationError("Required Editor package directory missing or linked")
    data.rename(full)
    builtin = data / BUILTINS
    builtin.mkdir(parents=True)
    for name in editor_packages:
        (full / BUILTINS / name).rename(builtin / name)


def _validate(directory: Path, details: CacheIdentity) -> dict[str, str]:
    try:
        if {path.name for path in directory.iterdir()} != {"Data", "Registry", "receipt.json"}:
            raise packages.PreparationError("Unexpected cache root inventory")
        if any(_linked(directory / name) for name in ("Data", "Registry", "receipt.json")):
            raise packages.PreparationError("Linked optional dependency cache contents")
        receipt = json.loads((directory / "receipt.json").read_text(encoding="utf-8"))
        if not isinstance(receipt, dict):
            raise packages.PreparationError("Optional dependency receipt must be an object")
        if receipt["cacheKey"] != details.cache_key or receipt["provenance"] != details.provenance:
            raise packages.PreparationError("Optional dependency input identity changed")
        if not receipt["files"] or inventory(directory) != receipt["files"]:
            raise packages.PreparationError("Optional dependency size, mode or SHA256 mismatch")
        archives = receipt["archiveSha256"]
        expected = {f"{name}@{version}" for name, version in details.registries}
        if (
            not isinstance(archives, dict)
            or set(archives) != expected
            or any(
                not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
                for value in archives.values()
            )
        ):
            raise packages.PreparationError("Missing exact registry archive provenance")
        if {path.name for path in (directory / "Registry").iterdir()} != expected:
            raise packages.PreparationError("Unexpected registry package inventory")
        data = directory / "Data"
        for parent, expected_child in (
            (data, "Resources"),
            (data / BUILTINS.parents[1], "PackageManager"),
            (data / BUILTINS.parent, "BuiltInPackages"),
        ):
            if {path.name for path in parent.iterdir()} != {expected_child}:
                raise packages.PreparationError("Unexpected Editor data inventory")
        editor_packages = _package_names(receipt["editorPackages"])
        if {path.name for path in (data / BUILTINS).iterdir()} != set(editor_packages):
            raise packages.PreparationError(
                "Editor package inventory does not match validated closure"
            )
        return archives
    except (OSError, ValueError, KeyError, TypeError, packages.PreparationError) as exc:
        raise packages.PreparationError(
            f"Invalid optional dependency cache at {directory}; delete the exact Actions cache "
            f"entry (key {details.cache_key}) or remove only this local version directory: {exc}"
        ) from exc


def _prepare_project(
    request: CacheRequest, directory: Path, output: Path, archives: dict[str, str]
) -> packages.Preparation:
    return packages.prepare(
        request.version,
        directory / "Data",
        output,
        repo=request.repo,
        profiles_path=request.profiles_path,
        registry_cache=directory / "Registry",
        registry_integrity=archives,
        include_optional=True,
    )


def prepare(request: CacheRequest, output: Path) -> CachePreparation:
    """Validate dependencies on both paths; publish a cold cache only after fresh staging passes."""
    details = identity(request)
    target = destination(request)
    project_output = output.resolve()
    if project_output.is_relative_to(target) or target.is_relative_to(project_output):
        raise packages.PreparationError(
            "Prepared source output must be separate from dependency cache"
        )
    if target.exists():
        archives = _validate(target, details)
        print(
            "Optional dependency cache: validated warm inputs; staging current source",
            file=sys.stderr,
        )
        result = _prepare_project(request, target, output, archives)
        editor_packages = _editor_packages(request.repo / result.resolution_report)
        if {path.name for path in (target / "Data" / BUILTINS).iterdir()} != set(editor_packages):
            raise packages.PreparationError(
                "Cached Editor packages do not match current dependency closure"
            )
        return CachePreparation(result, False)
    target.parent.mkdir(parents=True, exist_ok=True)
    print(
        "Optional dependency cache: cold inputs; preparing pinned Editor and registry packages",
        file=sys.stderr,
    )
    with tempfile.TemporaryDirectory(prefix=f".{request.version}-", dir=target.parent) as temporary:
        staging = Path(temporary) / "inputs"
        staging.mkdir()
        image = unity_ci.prepare(
            unity_ci.load_manifest(request.manifest_path), request.version, purpose="tests"
        )
        packages.copy_image_packages(image, staging / "Data", unity_version=request.version)
        registry = staging / "Registry"
        registry.mkdir()
        archives = {
            f"{name}@{version}": packages.fetch_registry_package(
                name, version, registry / f"{name}@{version}"
            )
            for name, version in details.registries
        }
        result = _prepare_project(request, staging, output, archives)
        editor_packages = _editor_packages(request.repo / result.resolution_report)
        _compact_editor_inputs(staging, editor_packages)
        (staging / "receipt.json").write_text(
            json.dumps(
                {
                    "cacheKey": details.cache_key,
                    "provenance": details.provenance,
                    "archiveSha256": archives,
                    "editorPackages": editor_packages,
                    "files": inventory(staging),
                },
                sort_keys=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        _validate(staging, details)
        staging.rename(target)
    return CachePreparation(result, True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("identity", "prepare"):
        subparser = commands.add_parser(command)
        subparser.add_argument("version")
        subparser.add_argument("--manifest", type=Path, default=ROOT / "tools/unity-versions.json")
        subparser.add_argument("--profiles", type=Path, default=packages.PROFILES)
        if command == "prepare":
            subparser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        request = CacheRequest(args.version, ROOT, args.manifest, args.profiles)
        if args.command == "identity":
            details = identity(request)
            result = {"cache_key": details.cache_key, "cache_path": details.cache_path}
        else:
            prepared = prepare(request, args.output)
            result = {
                **asdict(prepared.preparation),
                "cache_populated": str(prepared.populated).lower(),
            }
        github_output = os.environ.get("GITHUB_OUTPUT")
        if github_output:
            with Path(github_output).open("a", encoding="utf-8", newline="\n") as stream:
                for key, value in result.items():
                    stream.write(f"{key}={value}\n")
        print(json.dumps(result, sort_keys=True))
    except (
        OSError,
        ValueError,
        KeyError,
        packages.PreparationError,
        subprocess.CalledProcessError,
        tarfile.TarError,
    ) as exc:
        print(f"Optional dependency cache preparation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
