"""Read-first inventory, recovery and explicit reclamation of native runtime spools."""

from contextlib import ExitStack
import argparse
from dataclasses import asdict, dataclass
import errno
import hashlib
from itertools import islice
import json
import os
from pathlib import Path
import re
import time
from typing import Final

from services.reference_graph.native_spool_paths import identity, lease, owned
from services.reference_graph.store import check_owned_path


MAX_METADATA: Final = 64 * 1024
MAX_FILES: Final = 4096
FRESH_SECONDS: Final = 300
FORMAT: Final = "unity-mcp-native-runtime"
SUFFIXES: Final = (".json", ".events", ".json.pending", ".lease")


@dataclass(frozen=True, slots=True)
class SpoolFile:
    name: str
    bytes: int
    sha256: str
    device: int
    inode: int
    modified_ns: int


@dataclass(frozen=True, slots=True)
class Selection:
    root: Path
    editor: str
    capture: str

    def path(self, suffix: str) -> Path:
        return self.root / self.editor / (self.capture + suffix)


def _root(path: Path) -> Path:
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Native spool directory must be absolute without parent traversal")
    owned(path, directory=True)
    return path


def _selection(root: Path, editor: str, capture: str) -> Selection:
    if not re.fullmatch(r"[a-f0-9]{32}", editor) or not re.fullmatch(r"[a-f0-9]{64}", capture):
        raise ValueError("Select one exact lowercase editor ID and capture ID")
    selection = Selection(_root(root), editor, capture)
    owned(root / editor, directory=True)
    _exact_name(root / editor)
    return selection


def _exact_name(path: Path) -> None:
    entries = tuple(islice(path.parent.iterdir(), MAX_FILES + 1))
    if len(entries) > MAX_FILES:
        raise ValueError("Native directory exceeds its scan limit; preserve it")
    matches = [entry.name for entry in entries if entry.name.casefold() == path.name.casefold()]
    if matches != [path.name]:
        raise ValueError("Ambiguous native spool filename; preserve the directory")


def _present(path: Path) -> bool:
    try:
        owned(path)
    except FileNotFoundError:
        return False
    _exact_name(path)
    return True


def _read(path: Path) -> dict:
    owned(path)
    with path.open("rb") as stream:
        payload = stream.read(MAX_METADATA + 1)
    if len(payload) > MAX_METADATA:
        raise ValueError("Native spool metadata exceeds the inspection limit")
    result = json.loads(payload.decode("utf-8-sig"))
    if not isinstance(result, dict):
        raise ValueError("Native spool metadata is not an object")
    return result


def _digest(path: Path) -> SpoolFile:
    before = owned(path)
    digest = hashlib.sha256()
    # POSIX record locks belong to the process: closing ANY other descriptor
    # for the same inode would release our held lease. Ownership files are
    # specified to be empty, so fingerprint their identity without opening them.
    if path.name.endswith(".lease"):
        if before.st_size:
            raise ValueError("Native spool lease must be empty; preserve it")
        return SpoolFile(
            path.name, 0, digest.hexdigest(), before.st_dev, before.st_ino, before.st_mtime_ns
        )
    with path.open("rb") as stream:
        current = os.fstat(stream.fileno())
        if (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino):
            raise ValueError("Native spool file changed during inspection")
        # LockFile byte locks are handle-specific on Windows, including byte
        # ranges beyond EOF. Empty ownership files need identity, not a read.
        if before.st_size:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        after = os.fstat(stream.fileno())
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("Native spool changed during inspection; preserve it")
    if identity(path) != (before.st_dev, before.st_ino):
        raise ValueError("Native spool path changed during inspection; preserve it")
    return SpoolFile(
        path.name,
        before.st_size,
        digest.hexdigest(),
        before.st_dev,
        before.st_ino,
        before.st_mtime_ns,
    )


def _inspect(selection: Selection) -> dict:
    root_identity = identity(selection.root, directory=True)
    editor_identity = identity(selection.root / selection.editor, directory=True)
    metadata = _read(selection.path(".json"))
    if (
        not isinstance(metadata.get("runtime_session"), str)
        or not re.fullmatch(r"[a-f0-9]{32}", metadata["runtime_session"])
        or type(metadata.get("count")) is not int
        or metadata["count"] < 0
        or not isinstance(metadata.get("state"), str)
    ):
        raise ValueError("Unknown native capture layout; preserve all files")
    versioned = metadata.get("format") == FORMAT
    if versioned and (
        metadata.get("version") != 1
        or metadata.get("editor_id") != selection.editor
        or metadata.get("capture_id") != selection.capture
        or metadata.get("lease_protocol") != "byte-range-v1"
    ):
        raise ValueError("Native spool identity does not match its selected path")
    if "format" in metadata and not versioned:
        raise ValueError("Unsupported native spool format")
    files = [
        asdict(_digest(selection.path(suffix)))
        for suffix in SUFFIXES
        if _present(selection.path(suffix))
    ]
    if selection.capture + ".events" not in {item["name"] for item in files}:
        raise ValueError("Native spool event evidence is missing")
    if (
        identity(selection.root, directory=True) != root_identity
        or identity(selection.root / selection.editor, directory=True) != editor_identity
    ):
        raise ValueError("Native spool directory changed during inspection; preserve it")
    proof = {
        "root": str(selection.root),
        "editor": selection.editor,
        "capture": selection.capture,
        "root_identity": root_identity,
        "editor_identity": editor_identity,
        "files": files,
    }
    fingerprint = hashlib.sha256(json.dumps(proof, sort_keys=True).encode()).hexdigest()
    return {
        **proof,
        "fingerprint": fingerprint,
        "metadata": metadata,
        "effective_state": "interrupted" if metadata["state"] == "active" else metadata["state"],
        "stored_bytes": sum(item["bytes"] for item in files),
        "reclaim_supported": versioned and selection.path(".lease").exists(),
    }


def inventory(root: Path, *, after: str = "", limit: int = 50) -> dict:
    """Bounded, non-mutating inventory. Active entries are never hashed or exported."""
    root = _root(root)
    if not 1 <= limit <= 100:
        raise ValueError("Native inventory page limit must be between 1 and 100")
    candidates, scanned, total = [], 0, 0
    for editor in root.iterdir():
        scanned += 1
        if scanned > MAX_FILES:
            raise ValueError("Native inventory exceeds its scan limit; preserve the directory")
        if not re.fullmatch(r"[a-f0-9]{32}", editor.name):
            continue
        owned(editor, directory=True)
        for path in editor.iterdir():
            scanned += 1
            if scanned > MAX_FILES:
                raise ValueError("Native inventory exceeds its scan limit; preserve the directory")
            info = owned(path)
            total += info.st_size
            if re.fullmatch(r"[a-f0-9]{64}\.json", path.name):
                key = editor.name + "/" + path.stem
                if key > after:
                    candidates.append((key, editor.name, path.stem))
    items = []
    for key, editor, capture in sorted(candidates)[:limit]:
        selection = _selection(root, editor, capture)
        metadata = _read(selection.path(".json"))
        status = "legacy_unverified"
        if metadata.get("format") == FORMAT:
            try:
                with lease(selection.path(".lease")):
                    _inspect(selection)
                status = "retained"
            except ValueError as error:
                if "active or maintenance" not in str(error):
                    raise
                status = "active"
        items.append(
            {
                "editor": editor,
                "capture": capture,
                "status": status,
                "state": metadata.get("state"),
                "metadata_bytes": selection.path(".json").stat().st_size,
                "stored_bytes": sum(
                    owned(selection.path(suffix)).st_size
                    for suffix in SUFFIXES
                    if _present(selection.path(suffix))
                ),
            }
        )
    return {
        "captures": items,
        "stored_bytes": total,
        "next_after": items[-1]["editor"] + "/" + items[-1]["capture"]
        if len(candidates) > limit
        else None,
    }


def _proven_lease(selection: Selection) -> bool:
    metadata = _read(selection.path(".json"))
    if "format" not in metadata:
        return False
    if metadata["format"] != FORMAT:
        raise ValueError("Unsupported native spool format")
    proven = (
        type(metadata.get("version")) is int
        and metadata["version"] == 1
        and metadata.get("editor_id") == selection.editor
        and metadata.get("capture_id") == selection.capture
        and metadata.get("lease_protocol") == "byte-range-v1"
    )
    if not proven:
        raise ValueError("Native spool identity does not match its selected path")
    return True


def _verify_record(path: Path, record: dict) -> None:
    info = owned(path)
    _exact_name(path)
    if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != (
        record["device"],
        record["inode"],
        record["bytes"],
        record["modified_ns"],
    ):
        raise ValueError("Native spool path changed before reclamation; preserve it")


def _prune_empty_owner(selection: Selection, report: dict) -> None:
    """Caller retains the common producer lease through this nonrecursive removal."""
    directory = selection.root / selection.editor

    def check_owner():
        if (
            identity(selection.root, directory=True) != report["root_identity"]
            or identity(directory, directory=True) != report["editor_identity"]
        ):
            raise ValueError("Native spool directory changed; preserve it")

    check_owner()
    with os.scandir(directory) as entries:
        if next(entries, None) is not None:
            return
    check_owner()
    try:
        directory.rmdir()
    except OSError as error:
        # An uncooperative writer can add unknown contents despite our lease.
        # Never recurse or remove those contents to complete owner housekeeping.
        if error.errno not in (errno.ENOTEMPTY, errno.EEXIST):
            raise


def inspect_capture(
    root: Path, editor: str, capture: str, *, all_editors_stopped: bool = False
) -> dict:
    selection = _selection(root, editor, capture)
    with ExitStack() as stack:
        if _proven_lease(selection):
            stack.enter_context(lease(selection.path(".lease")))
        elif not all_editors_stopped:
            raise ValueError(
                "Legacy capture export requires --all-editors-stopped; reclamation is unsupported"
            )
        report = _inspect(selection)
        return {**report, "inspected_at": int(time.time()), "outcome": "preview"}


def _fresh(selection: Selection, fingerprint: str, inspected_at: int) -> dict:
    if not re.fullmatch(r"[a-f0-9]{64}", fingerprint):
        raise ValueError("Apply requires the exact preview fingerprint")
    if not 0 <= time.time() - inspected_at <= FRESH_SECONDS:
        raise ValueError("Native spool preview is stale; inspect again")
    report = _inspect(selection)
    if report["fingerprint"] != fingerprint:
        raise ValueError("Native spool preview changed; inspect again and preserve the evidence")
    return report


def export_capture(
    root: Path,
    editor: str,
    capture: str,
    *,
    destination: Path,
    all_editors_stopped: bool = False,
    apply: bool = False,
) -> dict:
    """Copy exact raw evidence into one new directory. Never remove the source."""
    selection = _selection(root, editor, capture)
    check_owned_path(destination)
    if not destination.is_absolute() or ".." in destination.parts or destination.exists():
        raise ValueError("Native export requires a new absolute destination")
    if root == destination or root in destination.parents or destination in root.parents:
        raise ValueError("Native export destination must be outside the spool root")
    owned(destination.parent, directory=True)
    with ExitStack() as stack:
        if _proven_lease(selection):
            stack.enter_context(lease(selection.path(".lease")))
        elif not all_editors_stopped:
            raise ValueError("Legacy capture export requires --all-editors-stopped")
        report = _inspect(selection)
        if not apply:
            return {**report, "outcome": "preview", "destination": str(destination)}
        destination.mkdir(mode=0o700)
        target_identity = identity(destination, directory=True)
        for record in report["files"]:
            if (
                identity(destination, directory=True) != target_identity
                or _inspect(selection)["fingerprint"] != report["fingerprint"]
            ):
                raise ValueError("Native spool or export path changed; preserve both copies")
            target = destination / record["name"]
            with target.open("xb") as output:
                if record["bytes"]:
                    with (root / editor / record["name"]).open("rb") as source:
                        while chunk := source.read(1024 * 1024):
                            output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            copied = _digest(target)
            if copied.sha256 != record["sha256"] or copied.bytes != record["bytes"]:
                raise ValueError("Native export verification failed; source evidence is retained")
        with (destination / "recovery.json").open("x", encoding="utf-8") as stream:
            json.dump(report, stream, sort_keys=True)
        return {**report, "outcome": "exported", "destination": str(destination)}


def reclaim_capture(
    root: Path,
    editor: str,
    capture: str,
    *,
    fingerprint: str = "",
    inspected_at: int = 0,
    apply: bool = False,
) -> dict:
    """Delete only an explicitly selected fresh, leased, versioned capture."""
    selection = _selection(root, editor, capture)
    with ExitStack() as capture_owner:
        capture_owner.enter_context(lease(selection.path(".lease")))
        report = _inspect(selection)
        if not report["reclaim_supported"]:
            raise ValueError("Legacy capture ownership cannot authorize reclamation; export it")
        if not apply:
            return {**report, "inspected_at": int(time.time()), "outcome": "preview"}
        report = _fresh(selection, fingerprint, inspected_at)
        # Hash the selected idle capture before taking the global producer
        # fence. Under that fence only identity/stat checks and deletion remain.
        with lease(root / ".spool.lock"):
            for record in report["files"]:
                path = root / editor / record["name"]
                if (
                    identity(root, directory=True) != report["root_identity"]
                    or identity(root / editor, directory=True) != report["editor_identity"]
                ):
                    raise ValueError("Native spool directory changed; preserve it")
                _verify_record(path, record)
            for record in report["files"]:
                if record["name"].endswith(".lease"):
                    continue
                path = root / editor / record["name"]
                if (
                    identity(root, directory=True) != report["root_identity"]
                    or identity(root / editor, directory=True) != report["editor_identity"]
                ):
                    raise ValueError("Native spool directory changed; preserve it")
                _verify_record(path, record)
                path.unlink()
            # Keep the root fence until the selected capture descriptor is
            # closed and its exact empty ownership file has been removed.
            capture_owner.close()
            lease_record = next(item for item in report["files"] if item["name"].endswith(".lease"))
            if asdict(_digest(selection.path(".lease"))) != lease_record:
                raise ValueError("Native spool lease path changed; preserve it")
            selection.path(".lease").unlink()
            _prune_empty_owner(selection, report)
            return {
                "outcome": "reclaimed",
                "editor": editor,
                "capture": capture,
                "reclaimed_bytes": report["stored_bytes"],
            }


def run_command(args: argparse.Namespace) -> dict:
    """Route native actions through the existing offline maintenance CLI."""
    if args.action == "native-list":
        if args.apply:
            raise ValueError("Native inventory is read-only")
        return inventory(args.directory, after=args.after, limit=args.limit)
    if not args.editor or not args.capture:
        raise ValueError("Native capture operations require --editor and --capture")
    if args.action == "native-inspect":
        if args.apply:
            raise ValueError("Native inspection is read-only")
        return inspect_capture(
            args.directory, args.editor, args.capture, all_editors_stopped=args.all_editors_stopped
        )
    if args.action == "native-export":
        if args.destination is None:
            raise ValueError("Native export requires --destination")
        return export_capture(
            args.directory,
            args.editor,
            args.capture,
            destination=args.destination,
            all_editors_stopped=args.all_editors_stopped,
            apply=args.apply,
        )
    return reclaim_capture(
        args.directory,
        args.editor,
        args.capture,
        fingerprint=args.fingerprint,
        inspected_at=args.inspected_at,
        apply=args.apply,
    )
