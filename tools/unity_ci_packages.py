# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
# Run: python tools/unity_ci_packages.py prepare --unity-version VERSION --image IMAGE --output .unity-ci/VERSION
"""Prepare isolated, version-specific package sources for Unity CI without launching Unity."""

import argparse
from dataclasses import asdict, dataclass
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from urllib.parse import urlsplit
from urllib.request import urlopen
from typing import TypedDict


ROOT = Path(__file__).resolve().parents[1]
PROFILES = Path(__file__).with_name("unity-ci-packages.json")
PACKAGE_NAME = re.compile(r"[a-z0-9]+(?:[.-][a-z0-9]+)+\Z")
PACKAGE_VERSION = re.compile(r"\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?\Z")
UNITY_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)([abfp])(\d+)\Z")


class PackageSpec(TypedDict, total=False):
    source: str
    version: str


class ProfileSpec(TypedDict, total=False):
    unityFamilies: list[str]
    unityMajors: list[int]
    packages: dict[str, PackageSpec]


class JsonDocument(TypedDict, total=False):
    name: str
    version: str
    unity: str
    unityRelease: str
    dependencies: dict[str, str]
    schemaVersion: int
    requiredModules: list[str]
    profiles: dict[str, ProfileSpec]


class PreparationError(RuntimeError):
    """A package/profile boundary could not be verified."""


@dataclass(frozen=True, slots=True)
class Preparation:
    refs: str
    test_framework_source: str
    project_path: str
    resolution_report: str


@dataclass(frozen=True, slots=True)
class ResolvedPackage:
    name: str
    version: str
    directory: Path
    source: str
    minimum_unity: str | None
    dependencies: tuple[tuple[str, str], ...]
    archive_sha256: str | None = None


def unity_numbers(version: str) -> tuple[int, int, int]:
    match = UNITY_VERSION.fullmatch(version)
    if not match:
        raise PreparationError(f"Invalid Unity version: {version}")
    return tuple(int(match.group(index)) for index in (1, 2, 3))


def unity_order(version: str) -> tuple[int, int, int, int, int]:
    match = UNITY_VERSION.fullmatch(version)
    if not match:
        raise PreparationError(f"Invalid Unity version: {version}")
    return (*unity_numbers(version), "abfp".index(match.group(4)), int(match.group(5)))


def package_numbers(version: str) -> tuple[int, int, int]:
    if not PACKAGE_VERSION.fullmatch(version):
        raise PreparationError(f"Unpinned package version: {version}")
    return tuple(int(part) for part in version.split("-", 1)[0].split("."))


def read_json(path: Path) -> JsonDocument:
    if not path.is_file():
        raise PreparationError(f"Required package metadata not found: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PreparationError(f"Expected JSON object: {path}")
    return value


def extract_package(content: bytes, destination: Path) -> None:
    """Validate every member before extracting only ordinary package files/directories."""
    with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as archive:
        members = archive.getmembers()
        for member in members:
            path = PurePosixPath(member.name)
            if (path.is_absolute() or ".." in path.parts or "\\" in member.name or ":" in member.name
                    or not path.parts or path.parts[0] != "package" or not (member.isfile() or member.isdir())):
                raise PreparationError(f"Unsafe package archive member: {member.name}")
        for member in members:
            target = destination.joinpath(*PurePosixPath(member.name).parts[1:])
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                stream = archive.extractfile(member)
                if stream is None:
                    raise PreparationError(f"Missing package archive content: {member.name}")
                with stream, target.open("wb") as output:
                    shutil.copyfileobj(stream, output)


def fetch_registry_package(name: str, version: str, destination: Path) -> str:
    """Fetch an exact official-registry version and verify the registry's archive digest."""
    with urlopen(f"https://packages.unity.com/{name}", timeout=60) as response:
        metadata = json.load(response)
    release = metadata["versions"].get(version)
    if not release or release.get("name") != name or release.get("version") != version:
        raise PreparationError(f"Pinned registry package unavailable: {name}@{version}")
    dist = release["dist"]
    url = dist["tarball"]
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in {"packages.unity.com", "download.packages.unity.com"} or parsed.username:
        raise PreparationError(f"Untrusted registry archive URL for {name}")
    expected = dist.get("shasum", "")
    if not re.fullmatch(r"[0-9a-f]{40}", expected):
        raise PreparationError(f"Missing registry integrity digest: {name}@{version}")
    with urlopen(url, timeout=60) as response:
        content = response.read()
    if hashlib.sha1(content).hexdigest() != expected:
        raise PreparationError(f"Registry integrity mismatch: {name}@{version}")
    extract_package(content, destination)
    return hashlib.sha256(content).hexdigest()


def copy_source(source: Path, destination: Path) -> None:
    def excluded(directory: str, names: list[str]) -> set[str]:
        ignored = {name for name in names if name in {"Library", "Temp", "Logs", "obj", ".git", ".omo", ".codegraph", "node_modules", "__pycache__"}
                   or name.startswith(".env") or Path(name).suffix.lower() in {".key", ".pem", ".pfx", ".p12"}}
        if Path(directory).name == "Resources" and "Assets" in Path(directory).parts:
            ignored.add("GameData")
        for name in set(names) - ignored:
            if (Path(directory) / name).is_symlink():
                raise PreparationError(f"Symbolic link is not allowed in CI source: {name}")
        return ignored

    if source.is_symlink() or not source.is_dir():
        raise PreparationError(f"Required source directory not found or linked: {source}")
    shutil.copytree(source, destination, ignore=excluded)


def read_package(path: Path, name: str, unity_version: str, source: str, expected: str | None = None, archive_sha256: str | None = None) -> ResolvedPackage:
    metadata = read_json(path / "package.json")
    version = metadata.get("version", "")
    if metadata.get("name") != name or (expected is not None and version != expected):
        raise PreparationError(f"Package identity/version mismatch: {name}")
    package_numbers(version)
    minimum = metadata.get("unity")
    if minimum:
        if not re.fullmatch(r"\d+\.\d+", minimum):
            raise PreparationError(f"Invalid minimum Unity version for {name}: {minimum}")
        release = metadata.get("unityRelease")
        required = f"{minimum}.{release}" if release else minimum
        compatible = (unity_order(unity_version) >= unity_order(required) if release
                      else unity_numbers(unity_version)[:2] >= tuple(map(int, minimum.split("."))))
        if not compatible:
            raise PreparationError(f"{name}@{version} requires Unity {required}, selected {unity_version}")
        minimum = required
    dependencies = metadata.get("dependencies", {})
    if not isinstance(dependencies, dict):
        raise PreparationError(f"Invalid dependencies for {name}")
    for dependency, required in dependencies.items():
        if not PACKAGE_NAME.fullmatch(dependency):
            raise PreparationError(f"Invalid dependency name: {dependency}")
        package_numbers(required)
    return ResolvedPackage(name, version, path, source, minimum, tuple(dependencies.items()), archive_sha256)


def reference_diagnostic(unity_version: str, package: ResolvedPackage, root: Path, filename: str) -> str:
    """Expose bounded import evidence, never infer compatibility from a DLL filename."""
    directory = root / package.name
    dlls = sorted(directory.rglob("*.dll"))
    metadata = []
    for dll in (path for path in dlls if path.name == filename):
        if len(metadata) == 4:
            break
        path = dll.with_suffix(".dll.meta")
        if not path.is_file() or path.is_symlink():
            continue
        try:
            with path.open(encoding="utf-8", errors="replace") as stream:
                content = stream.read(4097)
            metadata.append({"path": path.relative_to(directory).as_posix(), "content": content[:4096], "truncated": len(content) > 4096})
        except OSError:
            metadata.append({"path": path.relative_to(directory).as_posix(), "unreadable": True})
    assemblies = []
    framework = root / "com.unity.test-framework"
    for path in sorted(framework.rglob("*.asmdef")):
        if len(assemblies) == 8:
            break
        try:
            with path.open(encoding="utf-8") as stream:
                content = stream.read(16385)
            if len(content) > 16384:
                continue
            definition = json.loads(content)
            if not isinstance(definition, dict) or filename not in definition.get("precompiledReferences", []):
                continue
            fields = {key: definition[key] for key in ("name", "includePlatforms", "excludePlatforms", "precompiledReferences", "overrideReferences", "defineConstraints") if key in definition}
            # Bound both candidate count and JSON field sizes from package-provided metadata.
            if len(json.dumps(fields)) > 2048:
                fields = {"metadataTruncated": True}
            assemblies.append({"path": path.relative_to(root).as_posix(), **fields})
        except (OSError, ValueError, TypeError):
            continue
    return json.dumps({"unityVersion": unity_version, "package": f"{package.name}@{package.version}",
                       "dllPaths": [path.relative_to(directory).as_posix() for path in dlls[:20]],
                       "dllPathsTruncated": len(dlls) > 20, "pluginMetadata": metadata,
                       "referencingAssemblies": assemblies}, separators=(",", ":"))


def nunit_reference_path(unity_version: str, package: ResolvedPackage, root: Path) -> str:
    legacy = "net40/unity-custom/nunit.framework.dll"
    if package.version.startswith("2.0.") and "-" not in package.version:
        return legacy
    relative = "net472/unity-custom/nunit.framework.dll"
    directory = root / package.name
    reason = None
    major, minor, _ = unity_numbers(unity_version)
    if package.version not in {"2.1.0", "2.1.1"} or package.source != "editor" or major != 6000 or minor < 6:
        reason = "Unverified NUnit package/layout"
    elif sorted(path.relative_to(directory).as_posix() for path in directory.rglob("nunit.framework.dll")) != [relative]:
        reason = "Required reference DLL missing or ambiguous"
    else:
        # Hosted Unity 6.6/6.7 bundles 2.1.0/2.1.1 with this path and default Editor metadata.
        # Do not interpret arbitrary PluginImporter restrictions as a compatible alternative.
        metadata = (directory / relative).with_suffix(".dll.meta")
        if not metadata.is_file() or metadata.is_symlink():
            reason = "Unverified NUnit Editor import metadata"
        else:
            with metadata.open(encoding="utf-8") as stream:
                content = stream.read(4097)
            if not re.fullmatch(r"fileFormatVersion: 2\nguid: [0-9a-f]{32}\n?", content):
                reason = "Unverified NUnit Editor import metadata"
    if reason:
        diagnostic = reference_diagnostic(unity_version, package, root, "nunit.framework.dll")
        raise PreparationError(f"{reason}: {package.name}@{package.version}/{relative}; diagnostic={diagnostic}")
    return relative


def prepare(unity_version: str, unity_data: Path, output: Path, *, repo: Path = ROOT, profiles_path: Path = PROFILES, registry_cache: Path | None = None) -> Preparation:
    major, minor, _ = unity_numbers(unity_version)
    config = read_json(profiles_path)
    if config.get("schemaVersion") != 1:
        raise PreparationError("Unsupported CI package profile schema")
    matching = [(name, profile) for name, profile in config["profiles"].items()
                if f"{major}.{minor}" in profile.get("unityFamilies", []) or major in profile.get("unityMajors", [])]
    if len(matching) != 1:
        raise PreparationError(f"Exactly one package profile required for Unity {unity_version}")
    profile_name, profile = matching[0]
    repo, output = repo.resolve(), output.resolve()
    package_root = repo / "MCPForUnity"
    project_root = repo / "TestProjects" / "UnityMCPTests"
    if not output.is_relative_to(repo) or output == repo or output.is_relative_to(package_root) or output.is_relative_to(project_root) or output.exists():
        raise PreparationError("Output must be a new isolated repository directory outside source trees")
    if "\n" in str(output) or "\r" in str(output):
        raise PreparationError("Output path must not contain line breaks")
    original_package = read_json(package_root / "package.json")
    specs = dict(profile["packages"])
    for name in config["requiredModules"]:
        specs[name] = {"source": "editor"}
    for name in original_package.get("dependencies", {}):
        if name not in specs:
            if not name.startswith("com.unity.modules."):
                raise PreparationError(f"Unprofiled package dependency: {name}")
            specs[name] = {"source": "editor"}
    builtin = unity_data / "Resources" / "PackageManager" / "BuiltInPackages"
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="unity-ci-", dir=output.parent) as temporary:
        scratch = Path(temporary)
        resolved: dict[str, ResolvedPackage] = {}
        pending = list(specs)
        while pending:
            name = pending.pop(0)
            if name in resolved:
                continue
            if not PACKAGE_NAME.fullmatch(name):
                raise PreparationError(f"Invalid package name: {name}")
            spec = specs.get(name, {"source": "editor"})
            destination = scratch / "packages" / name
            sha256 = None
            match spec["source"]:
                case "editor":
                    package = read_package(builtin / name, name, unity_version, "editor")
                case "registry":
                    version = spec["version"]
                    package_numbers(version)
                    if registry_cache is not None:
                        package = read_package(registry_cache / f"{name}@{version}", name, unity_version, "registry-cache", expected=version)
                    else:
                        sha256 = fetch_registry_package(name, version, destination)
                        package = read_package(destination, name, unity_version, "registry", expected=version, archive_sha256=sha256)
                case _:
                    raise PreparationError(f"Unsupported package source for {name}")
            # Native modules remain built-in; package code is materialized once and referenced by file:.
            if not name.startswith("com.unity.modules.") and package.directory != destination:
                destination.parent.mkdir(parents=True, exist_ok=True)
                copy_source(package.directory, destination)
            resolved[name] = package
            for dependency, _ in package.dependencies:
                if dependency not in specs and not dependency.startswith("com.unity.modules."):
                    raise PreparationError(f"Unprofiled transitive dependency: {dependency}")
                pending.append(dependency)
        for package in resolved.values():
            for dependency, minimum in package.dependencies:
                if package_numbers(resolved[dependency].version) < package_numbers(minimum):
                    raise PreparationError(f"{package.name} requires {dependency}>={minimum}")
        for name, minimum in original_package.get("dependencies", {}).items():
            if package_numbers(resolved[name].version) < package_numbers(minimum):
                raise PreparationError(f"MCP package requires {name}>={minimum}")
        refs = scratch / "refs"
        refs.mkdir()
        nunit = nunit_reference_path(unity_version, resolved["com.unity.ext.nunit"], scratch / "packages")
        for name, relative, filename in (("com.unity.ext.nunit", nunit, "nunit.framework.dll"),
                                         ("com.unity.nuget.newtonsoft-json", "Runtime/Newtonsoft.Json.dll", "Newtonsoft.Json.dll")):
            source = scratch / "packages" / name / relative
            if not source.is_file() or source.is_symlink():
                diagnostic = reference_diagnostic(unity_version, resolved[name], scratch / "packages", filename)
                raise PreparationError(f"Required reference DLL missing: {name}/{relative}; diagnostic={diagnostic}")
            shutil.copyfile(source, refs / filename)
        framework = scratch / "packages" / "com.unity.test-framework"
        for assembly in ("UnityEngine.TestRunner", "UnityEditor.TestRunner"):
            if not (framework / assembly).is_dir():
                raise PreparationError(f"Test Framework source missing: {assembly}")
        copy_source(package_root, scratch / "package")
        isolated_package = dict(original_package)
        isolated_package["dependencies"] = {name: resolved[name].version for name in original_package.get("dependencies", {})}
        (scratch / "package" / "package.json").write_text(json.dumps(isolated_package, indent=2) + "\n", encoding="utf-8")
        project = scratch / "project"
        for directory in ("Assets", "ProjectSettings"):
            copy_source(project_root / directory, project / directory)
        (project / "Packages").mkdir()
        dependencies = {name: package.version if name.startswith("com.unity.modules.") else f"file:../../packages/{name}" for name, package in resolved.items()}
        dependencies[original_package["name"]] = "file:../../package"
        (project / "Packages" / "manifest.json").write_text(json.dumps({"dependencies": dependencies}, indent=2) + "\n", encoding="utf-8")
        report = {"unityVersion": unity_version, "profile": profile_name, "packages": [
            {"name": package.name, "version": package.version, "source": package.source,
             "minimumUnity": package.minimum_unity, "dependencies": dict(package.dependencies),
             "integrity": {"archiveSha256": package.archive_sha256, "packageMetadataSha256": hashlib.sha256((package.directory / "package.json").read_bytes()).hexdigest()}}
            for package in sorted(resolved.values(), key=lambda item: item.name)]}
        (scratch / "resolved-packages.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        scratch.replace(output)
    relative = output.relative_to(repo).as_posix()
    return Preparation(f"{relative}/refs", f"{relative}/packages/com.unity.test-framework", f"{relative}/project", f"{relative}/resolved-packages.json")


def copy_image_packages(image: str, destination: Path, *, unity_version: str | None = None) -> Path:
    """Copy metadata/sources from a stopped container, then remove that container."""
    version_pattern = r"[0-9]{4}\.[0-9]+\.[0-9]+[abfp][0-9]+"
    official = re.fullmatch(rf"unityci/editor:ubuntu-({version_pattern})-(?:base|linux-il2cpp)-3@sha256:[0-9a-f]{{64}}", image)
    local = re.fullmatch(rf"unity-mcp-editor:({version_pattern})(?:-tests)?", image)
    selected = official or local
    if not selected or (unity_version is not None and selected.group(1) != unity_version):
        raise PreparationError("Expected a version-matching digest-pinned GameCI image or prepared local Unity image")
    container = subprocess.run(["docker", "create", image], check=True, capture_output=True, text=True).stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{64}", container):
        raise PreparationError("Docker did not return a valid container ID")
    manager = destination / "Resources" / "PackageManager"
    manager.mkdir(parents=True)
    try:
        subprocess.run(["docker", "cp", f"{container}:/opt/unity/Editor/Data/Resources/PackageManager/BuiltInPackages", str(manager)], check=True)
    finally:
        subprocess.run(["docker", "rm", container], check=True, capture_output=True)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare"])
    parser.add_argument("--unity-version", required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--image")
    source.add_argument("--unity-data", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--registry-cache", type=Path)
    parser.add_argument("--github-output", type=Path, default=os.environ.get("GITHUB_OUTPUT"))
    args = parser.parse_args()
    try:
        with tempfile.TemporaryDirectory(prefix="unity-ci-editor-") as temporary:
            data = args.unity_data or copy_image_packages(args.image, Path(temporary), unity_version=args.unity_version)
            result = prepare(args.unity_version, data, args.output, registry_cache=args.registry_cache)
        if args.github_output:
            with args.github_output.open("a", encoding="utf-8") as output:
                for key, value in asdict(result).items():
                    output.write(f"{key}={value}\n")
        print(json.dumps(asdict(result)))
        return 0
    except (PreparationError, OSError, ValueError, KeyError, subprocess.CalledProcessError, tarfile.TarError) as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
