"""Bounded artifact IO for the maintained native Player assurance harness."""

import hashlib
import json
import os
import re
import stat
from pathlib import Path, PurePosixPath

JSON_LIMIT = 2 * 1024 * 1024
INVENTORY_LIMIT = 1024 * 1024
PAYLOAD_LIMIT = 16 * 1024**3


def unique_object(pairs: list[tuple[str, object]]) -> dict:
    """Reject duplicate JSON keys instead of accepting an ambiguous last value."""
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate JSON artifact key")
        value[key] = item
    return value


def portable(relative: str) -> None:
    """Reject paths Unity/Python cannot consistently interpret on Windows."""
    for part in relative.split("/"):
        if (
            re.search(r'[<>:"\\|?*\x00-\x1f\x7f-\x9f]', part)
            or part.endswith((".", " "))
            or re.fullmatch(
                r"(?:CON|PRN|AUX|NUL|COM[1-9\u00b9\u00b2\u00b3]|LPT[1-9\u00b9\u00b2\u00b3])(?:\..*)?",
                part,
                re.I,
            )
        ):
            raise ValueError("Payload path is not portable to Windows")
        if part.lower().startswith(".env"):
            raise ValueError("Environment payload files are forbidden")
    if "/assets/resources/gamedata/" in ("/" + relative.lower() + "/"):
        raise ValueError("Protected GameData payload paths are forbidden")


def read_json(path: Path, limit: int = JSON_LIMIT) -> dict:
    """Read a regular bounded object; reject links and trailing/truncated JSON."""
    portable(path.name)
    if (
        not path.is_file()
        or any(linked(parent) for parent in path.absolute().parents)
        or linked(path)
        or path.stat().st_size > limit
    ):
        raise ValueError(f"Missing, linked or oversized artifact: {path.name}")
    with path.open("rb") as stream:
        payload = stream.read(limit + 1)
    if len(payload) > limit:
        raise ValueError("Artifact grew beyond its byte bound")
    value = json.loads(payload, object_pairs_hook=unique_object)
    if not isinstance(value, dict):
        raise ValueError(f"Artifact must be an object: {path.name}")
    return value


def write_json(path: Path, value: dict) -> None:
    """Create immutable evidence, refusing to overwrite earlier proof."""
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=True, indent=2)
        stream.write("\n")


def linked(path: Path) -> bool:
    """Reject symbolic links, junctions and other Windows reparse points."""
    metadata = path.lstat()
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0) & 0x400
    )


def digest(path: Path) -> str:
    """Hash only the bounded observed file size; detect concurrent growth or truncation."""
    size = path.stat().st_size
    if linked(path) or size > PAYLOAD_LIMIT:
        raise ValueError("Linked or oversized hash input")
    remaining = size
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        while remaining:
            chunk = stream.read(min(1024 * 1024, remaining))
            if not chunk:
                raise ValueError("Hash input was truncated")
            remaining -= len(chunk)
            checksum.update(chunk)
        if stream.read(1):
            raise ValueError("Hash input grew during verification")
    return checksum.hexdigest()


def contained(root: Path, relative: str) -> Path:
    """Resolve an artifact only within its selected root, with no links in its chain."""
    portable(relative)
    pure = PurePosixPath(relative)
    if not relative or "\\" in relative or ":" in relative or pure.is_absolute():
        raise ValueError("Artifact path must be a relative portable path")
    if any(part in (".", "..") for part in relative.split("/")):
        raise ValueError("Artifact traversal is forbidden")
    if any(linked(parent) for parent in root.absolute().parents) or (
        root.exists() and linked(root)
    ):
        raise ValueError("Artifact root reparse paths are forbidden")
    path = root
    for part in pure.parts:
        path /= part
        if path.exists() and linked(path):
            raise ValueError("Linked artifact paths are forbidden")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Artifact escapes its root")
    return path


def inventory(root: Path) -> list[dict]:
    """Stream bounded discovery before sorting; never enumerate an unbounded tree."""
    entries = []
    total = 0
    directories = 1
    pending = [root]
    names = set()
    while pending:
        directory = pending.pop()
        if linked(directory):
            raise ValueError("Build payload reparse paths are forbidden")
        with os.scandir(directory) as children:
            for child in children:
                path = Path(child.path)
                if linked(path):
                    raise ValueError("Build payload links are forbidden")
                relative = path.relative_to(root).as_posix()
                portable(relative)
                if relative == "scenario-bundle.json":
                    continue
                if child.is_dir(follow_symlinks=False):
                    directories += 1
                    if directories > 4096:
                        raise ValueError("Build directory count exceeds its bound")
                    pending.append(path)
                    continue
                if not child.is_file(follow_symlinks=False):
                    raise ValueError("Build payload must contain only regular files")
                if relative.casefold() in names:
                    raise ValueError("Build payload has case-insensitive path collisions")
                names.add(relative.casefold())
                size = child.stat(follow_symlinks=False).st_size
                total += size
                if len(entries) >= 1024 or total > PAYLOAD_LIMIT:
                    raise ValueError("Build payload exceeds its count/byte bounds")
                entries.append({"path": relative, "size_bytes": size, "sha256": digest(path)})
    if not entries:
        raise ValueError("Build payload is empty")
    return sorted(
        entries, key=lambda entry: entry["path"].encode("utf-16-be", errors="surrogatepass")
    )


def verify_bundle(root: Path, revision: str) -> dict:
    """Verify v2 exact canonical inventory plus every real file, not only the executable."""
    bundle = read_json(root / "scenario-bundle.json")
    if bundle.get("schema_version") != 2 or bundle.get("build_source_revision") != revision:
        raise ValueError("A v2 build with the trusted expected revision is required")
    if not isinstance(bundle.get("build_id"), str) or not re.fullmatch(
        r"[0-9a-f]{32}", bundle["build_id"]
    ):
        raise ValueError("Build identity must be fresh lowercase 32-hex")
    canonical = bundle.get("payload_inventory_json")
    if not isinstance(canonical, str) or len(canonical.encode()) > INVENTORY_LIMIT:
        raise ValueError("Missing or oversized canonical payload inventory")
    entries = json.loads(canonical, object_pairs_hook=unique_object)
    if entries != bundle.get("payload_inventory"):
        raise ValueError("Canonical inventory differs from bundle inventory")
    if hashlib.sha256(canonical.encode()).hexdigest() != bundle.get("payload_hash"):
        raise ValueError("Bundle payload inventory hash differs")
    observed = inventory(root)
    if (
        sorted(entries, key=lambda item: item["path"].encode("utf-16-be", errors="surrogatepass"))
        != observed
    ):
        raise ValueError("Build payload files differ from the frozen inventory")
    return bundle
