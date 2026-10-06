"""Cache public Unity compiler inputs without caching or launching the Editor."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tempfile

import unity_ci


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = 1
PLATFORM = "linux/amd64"
RECEIPT = "receipt.json"
IMAGE_DATA = "/opt/unity/Editor/Data"
DIRECTORIES = (
    "Managed", "NetStandard", "UnityReferenceAssemblies", "DotNetSdkRoslyn",
    "NetCoreRuntime", "Tools/Compilation/ApiUpdater", "Tools/ScriptUpdater",
)
PACKAGES = ("com.unity.test-framework", "com.unity.ext.nunit", "com.unity.ugui")
BUILTINS = "Resources/PackageManager/BuiltInPackages"
LIBCACHE = "Resources/PackageManager/ProjectTemplates/libcache"
UI_REFERENCES = ("UnityEngine.UI.dll", "UnityEditor.UI.dll")
ROSLYN_REFERENCES = tuple("MonoBleedingEdge/lib/mono/4.5/" + name for name in (
    "Microsoft.CodeAnalysis.dll", "Microsoft.CodeAnalysis.CSharp.dll",
    "System.Collections.Immutable.dll", "System.Reflection.Metadata.dll"))
# Only inspect the public Editor data tree; no host mounts, network or Editor entrypoint.
INVENTORY = """set -eu
d=/opt/unity/Editor/Data
test -d "$d"
for p in Managed NetStandard UnityReferenceAssemblies DotNetSdkRoslyn NetCoreRuntime Tools/Compilation/ApiUpdater Tools/ScriptUpdater; do
  if [ -d "$d/$p" ]; then printf '%s\\n' "$d/$p"; fi
done
for p in MonoBleedingEdge/lib/mono/4.5/Microsoft.CodeAnalysis.dll MonoBleedingEdge/lib/mono/4.5/Microsoft.CodeAnalysis.CSharp.dll MonoBleedingEdge/lib/mono/4.5/System.Collections.Immutable.dll MonoBleedingEdge/lib/mono/4.5/System.Reflection.Metadata.dll; do
  if [ -f "$d/$p" ]; then printf '%s\\n' "$d/$p"; fi
done
find "$d" -mindepth 1 -maxdepth 10 -type d -name DotNetSdk -print
for p in "$d"/Resources/PackageManager/BuiltInPackages/com.unity.modules.* "$d"/Resources/PackageManager/BuiltInPackages/com.unity.test-framework "$d"/Resources/PackageManager/BuiltInPackages/com.unity.ext.nunit "$d"/Resources/PackageManager/BuiltInPackages/com.unity.ugui; do
  if [ -d "$p" ]; then printf '%s\\n' "$p"; fi
done
if [ -d "$d/Resources/PackageManager/ProjectTemplates/libcache" ]; then
  find "$d/Resources/PackageManager/ProjectTemplates/libcache" -mindepth 1 -maxdepth 10 -type f \
    \\( -name UnityEngine.UI.dll -o -name UnityEditor.UI.dll \\) -path '*/ScriptAssemblies/*' -print
fi
"""


def identity(manifest: unity_ci.Manifest, version: str) -> dict:
    row = next((row for row in manifest.versions if row.id == version), None)
    if row is None:
        raise ValueError("Requested Unity version is not in the manifest")
    source = {"image": row.image} if row.image else {"editorDownload": asdict(row.download)}
    provenance = {"schema": SCHEMA, "platform": PLATFORM, "version": version, "source": source,
                  "extractor_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    digest = hashlib.sha256(json.dumps(provenance, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"cache_key": f"unity-ci-sdk-v{SCHEMA}-linux-amd64-{version}-{digest}",
            "cache_path": f".unity-ci-sdk/{version}", "provenance": provenance}


def _linked(path: Path) -> bool:
    if path.is_symlink():
        return True
    attributes = getattr(path.lstat(), "st_file_attributes", 0) if path.exists() else 0
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _destination(output: Path, version: str) -> Path:
    expected = ROOT / ".unity-ci-sdk" / version
    candidate = output if output.is_absolute() else ROOT / output
    if ".." in output.parts or candidate.absolute() != expected.absolute():
        raise ValueError(f"Cache output must be exactly .unity-ci-sdk/{version}")
    for path in (ROOT, expected.parent, expected):
        if _linked(path):
            raise ValueError(f"Linked cache output is not allowed: {path}")
    return expected


def _ui_reference(value: str) -> bool:
    path = PurePosixPath(value)
    prefix = PurePosixPath(LIBCACHE).parts
    tail = path.parts[len(prefix):]
    return (path.parts[:len(prefix)] == prefix and 1 <= len(tail) <= 10
            and "ScriptAssemblies" in tail[:-1] and path.name in UI_REFERENCES)


def _allowed_input(value: str) -> bool:
    if not isinstance(value, str):
        return False
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value or ":" in value:
        return False
    if value in DIRECTORIES:
        return True
    if value in ROSLYN_REFERENCES:
        return True
    if path.name == "DotNetSdk" and len(path.parts) <= 10 and path.parts[0] not in {"Resources", "PlaybackEngines"}:
        return True
    if path.parent.as_posix() == BUILTINS:
        return path.name in PACKAGES or path.name.startswith("com.unity.modules.")
    return _ui_reference(value)


def _inventory(image: str) -> list[str]:
    result = subprocess.run(["docker", "run", "--rm", "--platform", PLATFORM, "--network", "none",
                             "--entrypoint", "/bin/sh", image, "-c", INVENTORY],
                            check=True, capture_output=True, text=True)
    lines = result.stdout.splitlines()
    if len(lines) > 512 or len(result.stdout) > 65536:
        raise ValueError("Compiler input inventory exceeded its bound")
    directories = set()
    for line in lines:
        if not line.startswith(IMAGE_DATA + "/"):
            raise ValueError("Unexpected compiler input inventory path")
        relative = line[len(IMAGE_DATA) + 1:]
        if not _allowed_input(relative):
            raise ValueError(f"Unexpected compiler input directory: {relative}")
        directories.add(relative)
    # A complete SDK can contain another named SDK; copy only the outer directory.
    return [value for value in sorted(directories)
            if not any(value.startswith(parent + "/") for parent in directories)]


def _records(directory: Path) -> dict:
    records = {}
    for path in sorted(directory.rglob("*")):
        if _linked(path):
            raise ValueError(f"Linked compiler input is not allowed: {path.relative_to(directory)}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError("Compiler inputs must be ordinary files")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        details = path.stat()
        records[path.relative_to(directory).as_posix()] = {
            "size": details.st_size, "sha256": digest.hexdigest(), "mode": stat.S_IMODE(details.st_mode)}
    return records


def _check_inputs(data: Path, directories: list[str]) -> None:
    if not isinstance(directories, list) or not directories or any(not _allowed_input(value) for value in directories) or len(set(directories)) != len(directories):
        raise ValueError("Invalid compiler input directories")
    for required in ("Managed", "NetStandard", "UnityReferenceAssemblies"):
        if required not in directories or not any((data / required).rglob("*.dll")):
            raise ValueError(f"Required compiler references missing: {required}")
    old = (data / "DotNetSdkRoslyn" / "csc.dll").is_file() and (data / "NetCoreRuntime" / "dotnet").is_file()
    modern = any((data / value / "dotnet").is_file() and
                 any((data / value / "sdk").glob("*/Roslyn/bincore/csc.dll"))
                 for value in directories if PurePosixPath(value).name == "DotNetSdk")
    if not old and not modern:
        raise ValueError("Complete bundled compiler and .NET runtime missing")
    if not any(value.startswith(BUILTINS + "/com.unity.modules.") for value in directories):
        raise ValueError("Bundled Unity module metadata missing")
    for name in UI_REFERENCES:
        if not any(_ui_reference(value) and PurePosixPath(value).name == name for value in directories):
            raise ValueError(f"Required template UI reference missing: {name}")
    for value in ROSLYN_REFERENCES:
        if value not in directories or not (data / value).is_file():
            raise ValueError(f"Required optional Roslyn reference missing or has wrong type: {value}")
    for value in directories:
        present = (data / value).is_file() if _ui_reference(value) or value in ROSLYN_REFERENCES else (data / value).is_dir()
        if not present:
            raise ValueError(f"Compiler input missing or has wrong type: {value}")
    for path in data.rglob("*"):
        relative = path.relative_to(data).as_posix()
        if not any(relative == value or relative.startswith(value + "/") or value.startswith(relative + "/")
                   for value in directories):
            raise ValueError(f"Unexpected cached compiler input: {relative}")


def _validate(directory: Path, details: dict) -> None:
    try:
        if {path.name for path in directory.iterdir()} != {"Data", RECEIPT}:
            raise ValueError("Unexpected cache root contents")
        if _linked(directory / "Data") or _linked(directory / RECEIPT):
            raise ValueError("Linked cache contents")
        receipt = json.loads((directory / RECEIPT).read_text(encoding="utf-8"))
        if receipt["cache_key"] != details["cache_key"] or receipt["provenance"] != details["provenance"]:
            raise ValueError("Compiler input source identity changed")
        _check_inputs(directory / "Data", receipt["directories"])
        if not receipt["files"] or _records(directory / "Data") != receipt["files"]:
            raise ValueError("Compiler input size, mode or SHA256 mismatch")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"Invalid compiler cache at {directory}; remove only this exact version directory and rerun: {exc}") from exc


def _remove_staging(staging: Path, destination: Path) -> None:
    if (_linked(staging) or staging.resolve().parent != destination.parent.resolve()
            or not staging.name.startswith(f".{destination.name}-")):
        raise ValueError("Refusing cleanup outside the controlled compiler-cache staging directory")
    shutil.rmtree(staging)


def prepare(manifest: unity_ci.Manifest, version: str, output: Path) -> dict:
    details = identity(manifest, version)
    destination = _destination(output, version)
    populated = False
    if destination.exists():
        _validate(destination, details)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{version}-", dir=destination.parent))
        try:
            image = unity_ci.prepare(manifest, version, purpose="compile")
            directories = _inventory(image)
            container = subprocess.run(["docker", "create", "--platform", PLATFORM, "--entrypoint", "/bin/true", image],
                                       check=True, capture_output=True, text=True).stdout.strip()
            if not container or any(char not in "0123456789abcdef" for char in container):
                raise ValueError("Docker did not return a valid container id")
            try:
                data = staging / "Data"
                data.mkdir()
                for relative in directories:
                    target = data / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    subprocess.run(["docker", "cp", f"{container}:{IMAGE_DATA}/{relative}", str(target)], check=True,
                                   stdout=sys.stderr)
                _check_inputs(data, directories)
                files = _records(data)
                (staging / RECEIPT).write_text(json.dumps({"cache_key": details["cache_key"],
                    "provenance": details["provenance"], "directories": directories, "files": files},
                    sort_keys=True, indent=2) + "\n", encoding="utf-8")
            finally:
                subprocess.run(["docker", "rm", "-f", container], check=True, stdout=sys.stderr)
            _validate(staging, details)
            staging.rename(destination)
            populated = True
        finally:
            if staging.exists():
                _remove_staging(staging, destination)
    return {"unity_data": details["cache_path"] + "/Data", "runtime_image": manifest.preview_base_image,
            "populated": populated}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("identity", "prepare"):
        subparser = commands.add_parser(command)
        subparser.add_argument("version")
        subparser.add_argument("--manifest", type=Path, default=Path(__file__).with_name("unity-versions.json"))
        if command == "prepare":
            subparser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        manifest = unity_ci.load_manifest(args.manifest)
        if args.command == "identity":
            details = identity(manifest, args.version)
            result = {key: details[key] for key in ("cache_key", "cache_path")}
        else:
            result = prepare(manifest, args.version, args.output)
        output = os.environ.get("GITHUB_OUTPUT")
        if output:
            with Path(output).open("a", encoding="utf-8", newline="\n") as stream:
                for key, value in result.items():
                    stream.write(f"{key}={str(value).lower() if isinstance(value, bool) else value}\n")
        print(json.dumps(result, sort_keys=True))
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Unity compiler cache failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
