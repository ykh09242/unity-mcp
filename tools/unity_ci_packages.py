# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# Run: python tools/unity_ci_packages.py prepare --unity-version VERSION --image IMAGE --output .unity-ci/VERSION
"""Prepare isolated, version-specific package sources for Unity CI without launching Unity."""

import argparse
from dataclasses import asdict, dataclass
import hashlib
from http.client import IncompleteRead
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
from time import sleep
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import urlopen
from typing import TypedDict


ROOT = Path(__file__).resolve().parents[1]
PROFILES = Path(__file__).with_name("unity-ci-packages.json")
PACKAGE_NAME = re.compile(r"[a-z0-9]+(?:[.-][a-z0-9]+)+\Z")
PACKAGE_VERSION = re.compile(r"\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?\Z")
UNITY_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)([abfp])(\d+)\Z")
TMP_REQUIRED_ASSETS = (
    "Assets/TextMesh Pro/Resources/TMP Settings.asset",
    "Assets/TextMesh Pro/Fonts/LiberationSans.ttf",
    "Assets/TextMesh Pro/Resources/Fonts & Materials/LiberationSans SDF.asset",
)


class PackageSpec(TypedDict, total=False):
    source: str
    version: str


class ProfileSpec(TypedDict, total=False):
    unityFamilies: list[str]
    unityMajors: list[int]
    packages: dict[str, PackageSpec]
    testables: list[str]
    activeInputHandler: int
    essentialResources: list[str]


class JsonDocument(TypedDict, total=False):
    name: str
    version: str
    unity: str
    unityRelease: str
    dependencies: dict[str, str]
    schemaVersion: int
    requiredModules: list[str]
    profiles: dict[str, ProfileSpec]
    optionalProfiles: dict[str, ProfileSpec]


class MatrixRow(TypedDict):
    version: str
    channel: str


class PreparationError(RuntimeError):
    """A package/profile boundary could not be verified."""


@dataclass(frozen=True, slots=True)
class Preparation:
    refs: str
    test_framework_source: str
    project_path: str
    resolution_report: str
    editor_coroutines_source: str


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
        planned: dict[str, tuple[PurePosixPath, tarfile.TarInfo]] = {}
        for member in members:
            path = PurePosixPath(member.name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or "\\" in member.name
                or ":" in member.name
                or any(character in member.name for character in '<>"|?*')
                or any(ord(character) < 32 for character in member.name)
                or any(part.endswith((".", " ")) for part in path.parts)
                or any(
                    re.fullmatch(r"(?i)(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part)
                    for part in path.parts
                )
                or not path.parts
                or path.parts[0] != "package"
                or not (member.isfile() or member.isdir())
                or (member.isfile() and len(path.parts) == 1)
            ):
                raise PreparationError(f"Unsafe package archive member: {member.name}")
            relative = PurePosixPath(*path.parts[1:])
            key = relative.as_posix().casefold()
            previous = planned.get(key)
            if previous is not None and not (
                previous[0] == relative and previous[1].isdir() and member.isdir()
            ):
                raise PreparationError(f"Conflicting package archive member: {member.name}")
            planned[key] = relative, member
        for relative, member in planned.values():
            for parent in relative.parents:
                ancestor = planned.get(parent.as_posix().casefold())
                if ancestor is not None and ancestor[1].isfile():
                    raise PreparationError(f"Package archive ancestor is a file: {parent}")
            target = destination.absolute().joinpath(*relative.parts)
            for current in (target, *target.parents):
                junction = getattr(current, "is_junction", lambda: False)()
                if current.is_symlink() or junction:
                    raise PreparationError(f"Linked package archive destination: {current}")
                if current.exists():
                    if current == target and member.isfile():
                        raise PreparationError(
                            f"Package archive cannot overwrite existing path: {current}"
                        )
                    if not current.is_dir():
                        raise PreparationError(f"Package archive ancestor is a file: {current}")
        for member in members:
            target = destination.joinpath(*PurePosixPath(member.name).parts[1:])
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                stream = archive.extractfile(member)
                if stream is None:
                    raise PreparationError(f"Missing package archive content: {member.name}")
                with stream, target.open("xb") as output:
                    shutil.copyfileobj(stream, output)


def _read_registry_bytes(url: str, identity: str) -> bytes:
    """Retry interrupted GETs without retaining partial responses between attempts."""
    attempt = 1
    while True:
        try:
            with urlopen(url, timeout=60) as response:
                return response.read()
        except HTTPError as error:
            error.close()
            if error.code not in {408, 429, 500, 502, 503, 504} or attempt == 3:
                raise PreparationError(
                    f"Registry download failed for {identity} after {attempt} attempts: {error}"
                ) from error
        except URLError as error:
            if not isinstance(error.reason, (TimeoutError, ConnectionError)) or attempt == 3:
                raise PreparationError(
                    f"Registry download failed for {identity} after {attempt} attempts: {error}"
                ) from error
        except (IncompleteRead, TimeoutError, ConnectionError) as error:
            if attempt == 3:
                raise PreparationError(
                    f"Registry download failed for {identity} after {attempt} attempts: {error}"
                ) from error
        sleep(attempt)
        attempt += 1


def fetch_registry_package(name: str, version: str, destination: Path) -> str:
    """Fetch an exact official-registry version and verify the registry's archive digest."""
    identity = f"{name}@{version}"
    metadata = json.loads(_read_registry_bytes(f"https://packages.unity.com/{name}", identity))
    release = metadata["versions"].get(version)
    if not release or release.get("name") != name or release.get("version") != version:
        raise PreparationError(f"Pinned registry package unavailable: {name}@{version}")
    dist = release["dist"]
    url = dist["tarball"]
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"packages.unity.com", "download.packages.unity.com"}
        or parsed.username
    ):
        raise PreparationError(f"Untrusted registry archive URL for {name}")
    expected = dist.get("shasum", "")
    if not re.fullmatch(r"[0-9a-f]{40}", expected):
        raise PreparationError(f"Missing registry integrity digest: {name}@{version}")
    content = _read_registry_bytes(url, identity)
    if hashlib.sha1(content).hexdigest() != expected:
        raise PreparationError(f"Registry integrity mismatch: {name}@{version}")
    extract_package(content, destination)
    return hashlib.sha256(content).hexdigest()


def copy_source(source: Path, destination: Path) -> None:
    def excluded(directory: str, names: list[str]) -> set[str]:
        ignored = {
            name
            for name in names
            if name
            in {
                "Library",
                "Temp",
                "Logs",
                "obj",
                ".git",
                ".omo",
                ".codegraph",
                "node_modules",
                "__pycache__",
            }
            or name.startswith(".env")
            or Path(name).suffix.lower() in {".key", ".pem", ".pfx", ".p12"}
        }
        if Path(directory).name == "Resources" and "Assets" in Path(directory).parts:
            ignored.add("GameData")
        for name in set(names) - ignored:
            if (Path(directory) / name).is_symlink():
                raise PreparationError(f"Symbolic link is not allowed in CI source: {name}")
        return ignored

    if source.is_symlink() or not source.is_dir():
        raise PreparationError(f"Required source directory not found or linked: {source}")
    shutil.copytree(source, destination, ignore=excluded)


def read_package(
    path: Path,
    name: str,
    unity_version: str,
    source: str,
    expected: str | None = None,
    archive_sha256: str | None = None,
) -> ResolvedPackage:
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
        compatible = (
            unity_order(unity_version) >= unity_order(required)
            if release
            else unity_numbers(unity_version)[:2] >= tuple(map(int, minimum.split(".")))
        )
        if not compatible:
            raise PreparationError(
                f"{name}@{version} requires Unity {required}, selected {unity_version}"
            )
        minimum = required
    dependencies = metadata.get("dependencies", {})
    if not isinstance(dependencies, dict):
        raise PreparationError(f"Invalid dependencies for {name}")
    for dependency, required in dependencies.items():
        if not PACKAGE_NAME.fullmatch(dependency):
            raise PreparationError(f"Invalid dependency name: {dependency}")
        package_numbers(required)
    return ResolvedPackage(
        name, version, path, source, minimum, tuple(dependencies.items()), archive_sha256
    )


def reference_diagnostic(
    unity_version: str, package: ResolvedPackage, root: Path, filename: str
) -> str:
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
            metadata.append(
                {
                    "path": path.relative_to(directory).as_posix(),
                    "content": content[:4096],
                    "truncated": len(content) > 4096,
                }
            )
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
            if not isinstance(definition, dict) or filename not in definition.get(
                "precompiledReferences", []
            ):
                continue
            fields = {
                key: definition[key]
                for key in (
                    "name",
                    "includePlatforms",
                    "excludePlatforms",
                    "precompiledReferences",
                    "overrideReferences",
                    "defineConstraints",
                )
                if key in definition
            }
            # Bound both candidate count and JSON field sizes from package-provided metadata.
            if len(json.dumps(fields)) > 2048:
                fields = {"metadataTruncated": True}
            assemblies.append({"path": path.relative_to(root).as_posix(), **fields})
        except (OSError, ValueError, TypeError):
            continue
    return json.dumps(
        {
            "unityVersion": unity_version,
            "package": f"{package.name}@{package.version}",
            "dllPaths": [path.relative_to(directory).as_posix() for path in dlls[:20]],
            "dllPathsTruncated": len(dlls) > 20,
            "pluginMetadata": metadata,
            "referencingAssemblies": assemblies,
        },
        separators=(",", ":"),
    )


def nunit_reference_path(unity_version: str, package: ResolvedPackage, root: Path) -> str:
    legacy = "net40/unity-custom/nunit.framework.dll"
    if package.version.startswith("2.0.") and "-" not in package.version:
        return legacy
    relative = "net472/unity-custom/nunit.framework.dll"
    directory = root / package.name
    reason = None
    major, minor, _ = unity_numbers(unity_version)
    verified = (major == 6000 and minor >= 6 and package.version in {"2.1.0", "2.1.1"}) or (
        (major, minor) == (7000, 0) and package.version == "2.1.2"
    )
    if not verified or package.source != "editor":
        reason = "Unverified NUnit package/layout"
    elif sorted(
        path.relative_to(directory).as_posix() for path in directory.rglob("nunit.framework.dll")
    ) != [relative]:
        reason = "Required reference DLL missing or ambiguous"
    else:
        # Unity 6.6/6.7 bundles 2.1.0/2.1.1; the verified 7000.0.0a7 archive bundles 2.1.2.
        # Both use this path and default Editor metadata.
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
        raise PreparationError(
            f"{reason}: {package.name}@{package.version}/{relative}; diagnostic={diagnostic}"
        )
    return relative


def optional_matches(config: JsonDocument, unity_version: str) -> list[tuple[str, ProfileSpec]]:
    """Select reviewed optional integrations by exact Unity family, never by major alone."""
    major, minor, _ = unity_numbers(unity_version)
    return [
        (name, profile)
        for name, profile in config.get("optionalProfiles", {}).items()
        if f"{major}.{minor}" in profile.get("unityFamilies", [])
    ]


def optional_matrix(manifest: Path, *, profiles_path: Path = PROFILES) -> list[MatrixRow]:
    """Filter the existing validated Unity matrix without introducing separate version pins."""
    config = read_json(profiles_path)
    if config.get("schemaVersion") != 1:
        raise PreparationError("Unsupported CI package profile schema")
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(Path(__file__).with_name("unity_ci.py")),
            "matrix",
            "--manifest",
            str(manifest),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    rows: list[MatrixRow] = json.loads(result.stdout)
    selected = []
    for row in rows:
        matching = optional_matches(config, row["version"])
        if len(matching) > 1:
            raise PreparationError(
                f"Exactly one optional package profile required for Unity {row['version']}"
            )
        if matching:
            selected.append(row)
    if not selected:
        raise PreparationError("No compatible Unity rows for optional package profiles")
    return selected


def apply_optional_settings(project: Path, profile: ProfileSpec) -> None:
    """Enable both input backends only in the generated isolated project."""
    if type(profile.get("activeInputHandler")) is not int or profile["activeInputHandler"] != 2:
        raise PreparationError("Optional integrations require activeInputHandler: 2")
    settings = project / "ProjectSettings/ProjectSettings.asset"
    content = (
        settings.read_text(encoding="utf-8")
        if settings.is_file()
        else ("%YAML 1.1\n%TAG !u! tag:unity3d.com,2011:\n--- !u!129 &1\nPlayerSettings:\n")
    )
    pattern = r"(?m)^([ \t]+activeInputHandler:)[^\r\n]*$"
    count = len(re.findall(pattern, content))
    if count > 1:
        raise PreparationError("Ambiguous activeInputHandler setting in isolated project")
    if count:
        content = re.sub(pattern, r"\g<1> 2", content)
    else:
        player_settings = r"(?m)^PlayerSettings:[ \t]*$"
        if len(re.findall(player_settings, content)) != 1:
            raise PreparationError("Missing or ambiguous PlayerSettings in isolated project")
        content = re.sub(player_settings, "PlayerSettings:\n  activeInputHandler: 2", content)
    settings.write_text(content, encoding="utf-8")


def tmp_resource_path(value: str) -> PurePosixPath:
    """Validate Unity pathname records independently of the host path syntax."""
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or ".." in path.parts
        or path.parts[:2] != ("Assets", "TextMesh Pro")
        or any(character in value for character in '\\:<>"|?*')
        or any(ord(character) < 32 for character in value)
        or any(part.endswith((".", " ")) for part in path.parts)
        or any(
            re.fullmatch(r"(?i)(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part)
            for part in path.parts
        )
        or "/resources/gamedata/" in "/" + value.casefold() + "/"
    ):
        raise PreparationError(f"Unsafe TMP resource pathname: {value!r}")
    return path


def stage_tmp_essential_resources(project: Path, packages_root: Path) -> None:
    """Stage the official uGUI TMP essentials only after validating the complete archive."""
    archive_path = (
        packages_root / "com.unity.ugui/Package Resources/TMP Essential Resources.unitypackage"
    )
    if not archive_path.is_file() or archive_path.is_symlink():
        raise PreparationError(
            "Required official TMP Essential Resources archive missing or linked"
        )
    if not project.is_dir() or project.is_symlink():
        raise PreparationError("TMP resources require an existing isolated project directory")
    groups: dict[str, dict[str, tarfile.TarInfo]] = {}
    with tarfile.open(archive_path, "r:gz") as archive:
        seen: set[str] = set()
        for member in archive.getmembers():
            path = PurePosixPath(member.name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or "\\" in member.name
                or ":" in member.name
                or not (member.isdir() or member.isfile())
                or (path.parts and not re.fullmatch(r"[0-9a-f]{32}", path.parts[0]))
                or (member.isdir() and len(path.parts) > 1)
                or (
                    member.isfile()
                    and (
                        len(path.parts) != 2
                        or path.name
                        not in {"pathname", "asset", "asset.meta", "preview.png", "._asset"}
                    )
                )
                or path.as_posix() in seen
            ):
                raise PreparationError(f"Unsafe TMP archive member: {member.name}")
            seen.add(path.as_posix())
            if path.parts:
                group = groups.setdefault(path.parts[0], {})
                if member.isfile():
                    group[path.name] = member
        planned: dict[str, tuple[PurePosixPath, bytes | None]] = {}
        for guid, members in groups.items():
            if not {"pathname", "asset.meta"} <= members.keys():
                raise PreparationError(f"TMP resource requires pathname and asset.meta: {guid}")
            payload = {}
            for name, member in members.items():
                if name in {"preview.png", "._asset"}:
                    continue
                stream = archive.extractfile(member)
                if stream is None:
                    raise PreparationError(f"TMP resource content missing: {guid}/{name}")
                with stream:
                    payload[name] = stream.read()
            try:
                path = tmp_resource_path(payload["pathname"].decode("utf-8"))
                metadata = payload["asset.meta"].decode("utf-8")
            except UnicodeError as error:
                raise PreparationError(f"Invalid TMP resource metadata: {guid}") from error
            if not re.search(rf"(?m)^guid: {guid}\r?$", metadata):
                raise PreparationError(f"TMP resource GUID mismatch: {guid}")
            directory = re.search(r"(?m)^folderAsset: yes\r?$", metadata) is not None
            if directory == ("asset" in payload) or (not directory and not payload.get("asset")):
                raise PreparationError(f"TMP resource asset is missing or empty: {path}")
            for target, content in (
                (path, None if directory else payload["asset"]),
                (PurePosixPath(f"{path}.meta"), payload["asset.meta"]),
            ):
                key = target.as_posix().casefold()
                if key in planned:
                    raise PreparationError(f"Duplicate TMP resource pathname: {target}")
                planned[key] = target, content
        for required in TMP_REQUIRED_ASSETS:
            resource = planned.get(required.casefold())
            if resource is None or not resource[1]:
                raise PreparationError(f"Required TMP resource missing or empty: {required}")
        # Reject all file/folder collisions and linked ancestors before creating any output.
        for path, content in planned.values():
            destination = project.joinpath(*path.parts)
            current = destination
            while current != project.parent:
                junction = getattr(current, "is_junction", lambda: False)()
                if current.is_symlink() or junction:
                    raise PreparationError(f"Linked TMP resource destination: {current}")
                if (
                    current.exists()
                    and (current != destination or content is None)
                    and not current.is_dir()
                ):
                    raise PreparationError(f"TMP resource ancestor is a file: {current}")
                current = current.parent
            if content is not None and destination.exists():
                raise PreparationError(f"TMP resources cannot overwrite project files: {path}")
            for parent in path.parents:
                ancestor = planned.get(parent.as_posix().casefold())
                if ancestor is not None and ancestor[1] is not None:
                    raise PreparationError(f"TMP resource file/folder conflict: {parent}")
        for path, content in planned.values():
            destination = project.joinpath(*path.parts)
            if content is None:
                destination.mkdir(parents=True, exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("xb") as output:
                    output.write(content)


def prepare(
    unity_version: str,
    unity_data: Path,
    output: Path,
    *,
    repo: Path = ROOT,
    profiles_path: Path = PROFILES,
    registry_cache: Path | None = None,
    include_optional: bool = False,
) -> Preparation:
    major, minor, _ = unity_numbers(unity_version)
    config = read_json(profiles_path)
    if config.get("schemaVersion") != 1:
        raise PreparationError("Unsupported CI package profile schema")
    matching = [
        (name, profile)
        for name, profile in config["profiles"].items()
        if f"{major}.{minor}" in profile.get("unityFamilies", [])
        or major in profile.get("unityMajors", [])
    ]
    if len(matching) != 1:
        raise PreparationError(f"Exactly one package profile required for Unity {unity_version}")
    profile_name, profile = matching[0]
    optional_name, optional_profile = None, None
    essential_resources: list[str] = []
    if include_optional:
        optional = optional_matches(config, unity_version)
        if len(optional) != 1:
            raise PreparationError(
                f"Exactly one optional package profile required for Unity {unity_version}"
            )
        optional_name, optional_profile = optional[0]
        essential_resources = optional_profile.get("essentialResources", [])
        if (
            not isinstance(essential_resources, list)
            or any(resource != "textmeshpro" for resource in essential_resources)
            or len(essential_resources) != len(set(essential_resources))
        ):
            raise PreparationError("Invalid or duplicate optional essential resource selector")
    repo, output = repo.resolve(), output.resolve()
    package_root = repo / "MCPForUnity"
    project_root = repo / "TestProjects" / "UnityMCPTests"
    input_roots = [unity_data.resolve()]
    if registry_cache is not None:
        input_roots.append(registry_cache.resolve())
    if (
        not output.is_relative_to(repo)
        or output == repo
        or output.is_relative_to(package_root)
        or output.is_relative_to(project_root)
        or any(output.is_relative_to(root) for root in input_roots)
        or output.exists()
    ):
        raise PreparationError(
            "Output must be a new isolated repository directory outside source trees"
        )
    if "\n" in str(output) or "\r" in str(output):
        raise PreparationError("Output path must not contain line breaks")
    original_package = read_json(package_root / "package.json")
    specs = dict(profile["packages"])
    if optional_profile is not None:
        specs.update(optional_profile["packages"])
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
                        package = read_package(
                            registry_cache / f"{name}@{version}",
                            name,
                            unity_version,
                            "registry-cache",
                            expected=version,
                        )
                    else:
                        sha256 = fetch_registry_package(name, version, destination)
                        package = read_package(
                            destination,
                            name,
                            unity_version,
                            "registry",
                            expected=version,
                            archive_sha256=sha256,
                        )
                case _:
                    raise PreparationError(f"Unsupported package source for {name}")
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
        # Validate the complete dependency closure before copying potentially large source trees.
        # Downloaded packages already occupy their destination; native modules remain built-in.
        for name, package in resolved.items():
            destination = scratch / "packages" / name
            if not name.startswith("com.unity.modules.") and package.directory != destination:
                destination.parent.mkdir(parents=True, exist_ok=True)
                copy_source(package.directory, destination)
        refs = scratch / "refs"
        refs.mkdir()
        nunit = nunit_reference_path(
            unity_version, resolved["com.unity.ext.nunit"], scratch / "packages"
        )
        for name, relative, filename in (
            ("com.unity.ext.nunit", nunit, "nunit.framework.dll"),
            (
                "com.unity.nuget.newtonsoft-json",
                "Runtime/Newtonsoft.Json.dll",
                "Newtonsoft.Json.dll",
            ),
        ):
            source = scratch / "packages" / name / relative
            if not source.is_file() or source.is_symlink():
                diagnostic = reference_diagnostic(
                    unity_version, resolved[name], scratch / "packages", filename
                )
                raise PreparationError(
                    f"Required reference DLL missing: {name}/{relative}; diagnostic={diagnostic}"
                )
            shutil.copyfile(source, refs / filename)
        framework = scratch / "packages" / "com.unity.test-framework"
        for assembly in ("UnityEngine.TestRunner", "UnityEditor.TestRunner"):
            if not (framework / assembly).is_dir():
                raise PreparationError(f"Test Framework source missing: {assembly}")
        coroutines_editor = scratch / "packages" / "com.unity.editorcoroutines" / "Editor"
        coroutines_asmdef = coroutines_editor / "Unity.EditorCoroutines.Editor.asmdef"
        if not coroutines_asmdef.is_file():
            raise PreparationError(
                "Editor Coroutines assembly definition missing: Unity.EditorCoroutines.Editor"
            )
        if read_json(coroutines_asmdef).get("name") != "Unity.EditorCoroutines.Editor":
            raise PreparationError(
                "Editor Coroutines assembly name must be Unity.EditorCoroutines.Editor"
            )
        if not any(path.is_file() for path in coroutines_editor.rglob("*.cs")):
            raise PreparationError("Editor Coroutines Editor sources missing")
        copy_source(package_root, scratch / "package")
        isolated_package = dict(original_package)
        isolated_package["dependencies"] = {
            name: resolved[name].version for name in original_package.get("dependencies", {})
        }
        (scratch / "package" / "package.json").write_text(
            json.dumps(isolated_package, indent=2) + "\n", encoding="utf-8"
        )
        project = scratch / "project"
        for directory in ("Assets", "ProjectSettings"):
            copy_source(project_root / directory, project / directory)
        (project / "Packages").mkdir()
        dependencies = {
            name: package.version
            if name.startswith("com.unity.modules.")
            else f"file:../../packages/{name}"
            for name, package in resolved.items()
        }
        dependencies[original_package["name"]] = "file:../../package"
        manifest = {"dependencies": dependencies}
        if optional_profile is not None:
            testables = optional_profile.get("testables", [])
            if not isinstance(testables, list) or any(
                not isinstance(name, str) or name not in resolved for name in testables
            ):
                raise PreparationError("Optional testables must name resolved packages")
            manifest["testables"] = testables
            apply_optional_settings(project, optional_profile)
            if "textmeshpro" in essential_resources:
                stage_tmp_essential_resources(project, scratch / "packages")
        (project / "Packages" / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        report = {
            "unityVersion": unity_version,
            "profile": profile_name,
            "packages": [
                {
                    "name": package.name,
                    "version": package.version,
                    "source": package.source,
                    "minimumUnity": package.minimum_unity,
                    "dependencies": dict(package.dependencies),
                    "integrity": {
                        "archiveSha256": package.archive_sha256,
                        "packageMetadataSha256": hashlib.sha256(
                            (package.directory / "package.json").read_bytes()
                        ).hexdigest(),
                    },
                }
                for package in sorted(resolved.values(), key=lambda item: item.name)
            ],
        }
        if optional_name is not None:
            report["optionalProfile"] = optional_name
        (scratch / "resolved-packages.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )
        scratch.replace(output)
    relative = output.relative_to(repo).as_posix()
    return Preparation(
        f"{relative}/refs",
        f"{relative}/packages/com.unity.test-framework",
        f"{relative}/project",
        f"{relative}/resolved-packages.json",
        f"{relative}/packages/com.unity.editorcoroutines",
    )


def copy_image_packages(image: str, destination: Path, *, unity_version: str | None = None) -> Path:
    """Copy metadata/sources from a stopped container, then remove that container."""
    version_pattern = r"[0-9]{4}\.[0-9]+\.[0-9]+[abfp][0-9]+"
    official = re.fullmatch(
        rf"unityci/editor:ubuntu-({version_pattern})-(?:base|linux-il2cpp)-3@sha256:[0-9a-f]{{64}}",
        image,
    )
    local = re.fullmatch(rf"unity-mcp-editor:({version_pattern})(?:-tests)?", image)
    selected = official or local
    if not selected or (unity_version is not None and selected.group(1) != unity_version):
        raise PreparationError(
            "Expected a version-matching digest-pinned GameCI image or prepared local Unity image"
        )
    container = subprocess.run(
        ["docker", "create", image], check=True, capture_output=True, text=True
    ).stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{64}", container):
        raise PreparationError("Docker did not return a valid container ID")
    manager = destination / "Resources" / "PackageManager"
    manager.mkdir(parents=True)
    try:
        subprocess.run(
            [
                "docker",
                "cp",
                f"{container}:/opt/unity/Editor/Data/Resources/PackageManager/BuiltInPackages",
                str(manager),
            ],
            check=True,
        )
    finally:
        subprocess.run(["docker", "rm", container], check=True, capture_output=True)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    preparation = commands.add_parser("prepare", help="Prepare an isolated Unity project")
    preparation.add_argument("--unity-version", required=True)
    source = preparation.add_mutually_exclusive_group(required=True)
    source.add_argument("--image")
    source.add_argument("--unity-data", type=Path)
    preparation.add_argument("--output", type=Path, required=True)
    preparation.add_argument("--registry-cache", type=Path)
    preparation.add_argument("--github-output", type=Path, default=os.environ.get("GITHUB_OUTPUT"))
    preparation.add_argument("--profiles", type=Path, default=PROFILES)
    preparation.add_argument("--include-optional", action="store_true")
    matrix = commands.add_parser(
        "optional-matrix", help="Emit compatible optional integration rows"
    )
    matrix.add_argument(
        "--manifest", type=Path, default=Path(__file__).with_name("unity-versions.json")
    )
    matrix.add_argument("--profiles", type=Path, default=PROFILES)
    args = parser.parse_args()
    try:
        if args.command == "optional-matrix":
            print(json.dumps(optional_matrix(args.manifest, profiles_path=args.profiles)))
            return 0
        with tempfile.TemporaryDirectory(prefix="unity-ci-editor-") as temporary:
            data = args.unity_data or copy_image_packages(
                args.image, Path(temporary), unity_version=args.unity_version
            )
            result = prepare(
                args.unity_version,
                data,
                args.output,
                registry_cache=args.registry_cache,
                profiles_path=args.profiles,
                include_optional=args.include_optional,
            )
        if args.github_output:
            with args.github_output.open("a", encoding="utf-8") as output:
                for key, value in asdict(result).items():
                    output.write(f"{key}={value}\n")
        print(json.dumps(asdict(result)))
        return 0
    except (
        PreparationError,
        OSError,
        ValueError,
        KeyError,
        subprocess.CalledProcessError,
        tarfile.TarError,
    ) as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
