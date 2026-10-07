"""Resolve the Unity CI matrix and prepare its pinned Editor images."""

from __future__ import annotations

import argparse
import base64
import binascii
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import sys


@dataclass(frozen=True, slots=True)
class EditorDownload:
    url: str
    md5: str
    size: int


@dataclass(frozen=True, slots=True)
class TestModule:
    name: str
    archive: EditorDownload
    destination: str


@dataclass(frozen=True, slots=True)
class Version:
    id: str
    channel: str
    image: str | None
    download: EditorDownload | None
    test_image: str | None
    test_modules: tuple[TestModule, ...]


@dataclass(frozen=True, slots=True)
class Manifest:
    default_version: str
    preview_base_image: str
    versions: tuple[Version, ...]


def _parse_download(details, pattern: str) -> EditorDownload:
    if not isinstance(details, dict):
        raise ValueError("Preview archives require official download metadata")
    url = details.get("url")
    if not isinstance(url, str) or not re.fullmatch(pattern, url):
        raise ValueError("Preview archive must use its exact official Unity Linux tar URL")
    integrity = details.get("integrity")
    if not isinstance(integrity, str) or not integrity.startswith("md5-"):
        raise ValueError("Preview archive requires official MD5 integrity")
    try:
        digest = base64.b64decode(integrity[4:], validate=True)
    except binascii.Error as exc:
        raise ValueError("Invalid preview MD5 integrity") from exc
    if len(digest) != 16 or base64.b64encode(digest).decode("ascii") != integrity[4:]:
        raise ValueError("Invalid preview MD5 integrity")
    size = details.get("size")
    if type(size) is not int or size <= 0:
        raise ValueError("Preview archive size must be a positive integer")
    return EditorDownload(url, digest.hex(), size)


def _parse_image(image, version: str, suffix: str) -> str:
    pattern = (
        r"unityci/editor:ubuntu-" + re.escape(version) + "-" + suffix + r"@sha256:[a-f0-9]{64}"
    )
    if not isinstance(image, str) or not re.fullmatch(pattern, image):
        raise ValueError(
            "Stable images must match the exact Unity version, purpose and GameCI digest pin"
        )
    return image


def load_manifest(path: Path) -> Manifest:
    """Parse all providers before any row may trigger Docker or publish output."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Unity manifest must be an object")
    base = data.get("previewBaseImage")
    if not isinstance(base, str) or not re.fullmatch(
        r"unityci/base:ubuntu-3(?:\.[0-9]+)*@sha256:[a-f0-9]{64}", base
    ):
        raise ValueError("previewBaseImage must be a digest-pinned GameCI Ubuntu 3 base")
    rows = data.get("versions")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Unity manifest must contain non-empty versions")
    versions = []
    ids = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Unity version row must be an object")
        version = row.get("id")
        if not isinstance(version, str) or not re.fullmatch(
            r"[0-9]{4}\.[0-9]+\.[0-9]+[abfp][0-9]+", version
        ):
            raise ValueError("Invalid Unity version id")
        if version in ids:
            raise ValueError("Duplicate Unity version id")
        ids.add(version)
        if not isinstance(row.get("role"), str) or not row["role"].strip():
            raise ValueError("Unity version role must be non-empty")
        channel = row.get("channel")
        if not isinstance(channel, str) or channel not in {"lts", "supported", "beta", "alpha"}:
            raise ValueError("Invalid Unity release channel")
        stage = re.search(r"([abfp])\d+$", version).group(1)
        expected_stage = {"beta": "b", "alpha": "a"}.get(channel)
        if (expected_stage and stage != expected_stage) or (
            not expected_stage and stage not in "fp"
        ):
            raise ValueError("Unity version id does not match its release channel")
        if ("image" in row) == ("editorDownload" in row):
            raise ValueError("Unity version must declare exactly one image provider")
        image, download, test_image = None, None, None
        modules = []
        if expected_stage:
            pattern = (
                r"https://download\.unity3d\.com/download_unity/[a-f0-9]{12}/"
                r"LinuxEditorInstaller/Unity-" + re.escape(version) + r"\.tar\.xz"
            )
            download = _parse_download(row.get("editorDownload"), pattern)
            if "testImage" in row:
                raise ValueError(
                    "Preview test images require official module archives, not a public image"
                )
            if "testModules" in row:
                module_rows = row["testModules"]
                if not isinstance(module_rows, list) or len(module_rows) != 2:
                    raise ValueError(
                        "Preview tests require both Linux IL2CPP and Linux Server archives"
                    )
                names = set()
                prefix = (
                    download.url.split("/LinuxEditorInstaller/")[0] + "/LinuxEditorTargetInstaller/"
                )
                for module in module_rows:
                    if not isinstance(module, dict):
                        raise ValueError("Preview test module must be an object")
                    name = module.get("name")
                    if (
                        not isinstance(name, str)
                        or name not in {"linux-il2cpp", "linux-server"}
                        or name in names
                    ):
                        raise ValueError(
                            "Preview test modules must be distinct Linux IL2CPP and Linux Server archives"
                        )
                    names.add(name)
                    destination = module.get("destination")
                    if destination != "Editor/Data/PlaybackEngines/LinuxStandaloneSupport":
                        raise ValueError(
                            "Preview module destination must match the official Linux support path"
                        )
                    target = "IL2CPP" if name == "linux-il2cpp" else "Server"
                    pattern = (
                        re.escape(prefix)
                        + "UnitySetup-Linux-"
                        + target
                        + "-Support-for-Editor-"
                        + re.escape(version)
                        + r"\.tar\.xz"
                    )
                    modules.append(TestModule(name, _parse_download(module, pattern), destination))
        else:
            image = _parse_image(row.get("image"), version, "base-3")
            if "testModules" in row:
                raise ValueError("Stable test images must use a pinned public GameCI image")
            if "testImage" in row:
                test_image = _parse_image(row["testImage"], version, "linux-il2cpp-3")
        versions.append(Version(version, channel, image, download, test_image, tuple(modules)))
    default = data.get("defaultVersion")
    if not isinstance(default, str) or default not in ids:
        raise ValueError("defaultVersion must name a manifest version")
    return Manifest(default, base, tuple(versions))


def prepare(
    manifest: Manifest, version: str, output: Path | None = None, *, purpose: str = "compile"
) -> str:
    """Resolve a public image or build a preview; publish only after success."""
    if purpose not in {"compile", "tests"}:
        raise ValueError("Unity image purpose must be compile or tests")
    row = next((row for row in manifest.versions if row.id == version), None)
    if row is None:
        raise ValueError("Requested Unity version is not in the manifest")
    image = row.test_image if purpose == "tests" else row.image
    if purpose == "tests" and (
        row.download is None and image is None or row.download is not None and not row.test_modules
    ):
        raise ValueError("Unity version has no verified test image provider")
    if row.download is not None:
        image = f"unity-mcp-editor:{row.id}" + ("-tests" if purpose == "tests" else "")
        args = ["docker", "build", "--platform", "linux/amd64", "--tag", image]
        for name, value in (
            ("PREVIEW_BASE_IMAGE", manifest.preview_base_image),
            ("UNITY_VERSION", row.id),
            ("IMAGE_PURPOSE", purpose),
            ("EDITOR_URL", row.download.url),
            ("EDITOR_MD5", row.download.md5),
            ("EDITOR_SIZE", str(row.download.size)),
        ):
            args.extend(["--build-arg", f"{name}={value}"])
        if purpose == "tests":
            args.extend(["--build-arg", f"MODULE_DESTINATION={row.test_modules[0].destination}"])
            for module in row.test_modules:
                prefix = "IL2CPP" if module.name == "linux-il2cpp" else "SERVER"
                for name, value in (
                    ("URL", module.archive.url),
                    ("MD5", module.archive.md5),
                    ("SIZE", str(module.archive.size)),
                ):
                    args.extend(["--build-arg", f"{prefix}_{name}={value}"])
        args.append(str(Path(__file__).resolve().parent / "unity-ci"))
        # Keep stdout a single image reference for callers capturing the CLI.
        subprocess.run(args, check=True, stdout=sys.stderr)
    if image is None:
        raise ValueError("Unity version has no prepared image")
    if output is not None:
        with output.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(f"image={image}\n")
    return image


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    default_manifest = Path(__file__).resolve().with_name("unity-versions.json")
    matrix = commands.add_parser("matrix", help="Emit all Unity versions as a JSON matrix")
    matrix.add_argument("--manifest", type=Path, default=default_manifest)
    preparation = commands.add_parser("prepare", help="Resolve or build a manifest Editor image")
    preparation.add_argument("version")
    preparation.add_argument("--purpose", choices=("compile", "tests"), default="compile")
    preparation.add_argument("--manifest", type=Path, default=default_manifest)
    args = parser.parse_args(argv)
    try:
        manifest = load_manifest(args.manifest)
        if args.command == "matrix":
            print(
                json.dumps(
                    [{"version": row.id, "channel": row.channel} for row in manifest.versions]
                )
            )
        else:
            output = os.environ.get("GITHUB_OUTPUT")
            print(
                prepare(
                    manifest, args.version, Path(output) if output else None, purpose=args.purpose
                )
            )
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Unity CI preparation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
