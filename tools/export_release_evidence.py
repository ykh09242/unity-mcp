"""Retain prepared release evidence locally and verify retained bundles offline.

Export: python tools/export_release_evidence.py export --help
Verify: python tools/export_release_evidence.py verify --help
Local Git history remains required; this helper never fetches or publishes.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import subprocess

import check_release_evidence as checker


@dataclass(frozen=True, slots=True)
class ExportRequest:
    repo: Path
    package_commit: str
    notes: Path
    evidence_index: Path
    destination: Path


def contained_path(path: Path) -> Path:
    """Reject lexical traversal and existing linked ancestors, including Windows junctions."""
    checker.require(".." not in path.parts, "Parent traversal is unsupported")
    absolute = path.absolute()
    for ancestor in (absolute, *absolute.parents):
        if ancestor.exists() or ancestor.is_symlink():
            info = ancestor.lstat()
            checker.require(
                not ancestor.is_symlink() and not getattr(info, "st_file_attributes", 0) & 0x400,
                "Linked paths and reparse points are unsupported",
            )
    return absolute


def saved_bytes(path: Path) -> bytes:
    return checker.read_bytes(contained_path(path))


def encoded(value: checker.JsonMap) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def consistent(request: ExportRequest) -> checker.Components:
    issues = checker.check_release_notes(
        request.repo,
        request.package_commit,
        saved_bytes(request.notes).decode("utf-8"),
        request.evidence_index,
    )
    checker.require(not issues, "\n".join(issues))
    return checker.components(request.repo, request.package_commit)


def write_new(path: Path, data: bytes) -> None:
    with contained_path(path).open("xb") as stream:
        stream.write(data)


def retain_entry(request: ExportRequest, entry: checker.JsonMap, retained: dict[str, str]) -> None:
    """Retain one validated job's bytes; release its buffers before the next job."""
    files = checker.mapping(entry.get("files"), "files")
    # Reuse the checker's path/digest rules, without reproducing kind/result semantics.
    saved = checker.entry_files(request.evidence_index, entry, set(files))
    for role, data in saved.items():
        checksum = hashlib.sha256(data).hexdigest()
        filename = checksum + ".bin"
        if filename not in retained:
            write_new(request.destination / filename, data)
            retained[filename] = checksum
        files[role] = {"path": filename, "sha256": checksum}


def export_bundle(request: ExportRequest) -> Path:
    """Copy only validated records to a new directory; never overwrite existing output."""
    destination = contained_path(request.destination)
    checker.require(not destination.exists(), "Destination already exists; choose a new directory")
    checker.require(destination.parent.is_dir(), "Destination parent must already exist")
    notes = saved_bytes(request.notes)
    index = checker.load_json(saved_bytes(request.evidence_index))
    evidence = checker.mapping(index.get("evidence"), "evidence")
    for raw_entry in evidence.values():
        entry = checker.mapping(raw_entry, "evidence entry")
        for role, raw_pointer in checker.mapping(entry.get("files"), "files").items():
            pointer = checker.mapping(raw_pointer, role)
            contained_path(request.evidence_index.parent / checker.text(pointer.get("path"), role))
    selected = consistent(request)
    # Exclusive mkdir and exclusive files preserve existing work, including failed exports.
    # A failure leaves an incomplete directory for inspection; bundle.json is written last.
    destination.mkdir()
    retained: dict[str, str] = {}
    for raw_entry in evidence.values():
        retain_entry(request, checker.mapping(raw_entry, "evidence entry"), retained)
    for name, data in (("notes.md", notes), ("evidence.json", encoded(index))):
        write_new(destination / name, data)
        retained[name] = hashlib.sha256(data).hexdigest()
    manifest: checker.JsonMap = {
        "schema_version": 1,
        "components": {
            "unity": selected.package,
            "server": selected.server,
            "skill": selected.skill,
        },
        "files": dict(retained),
    }
    checker.require(
        not checker.check_release_notes(
            request.repo, selected.package, notes.decode("utf-8"), destination / "evidence.json"
        ),
        "Retained evidence failed validation",
    )
    write_new(destination / "bundle.json", encoded(manifest))
    return destination


def validate_bundle(repo: Path, bundle: Path) -> None:
    """Check retained bytes, selected commits and original checker semantics offline."""
    root = contained_path(bundle)
    manifest = checker.load_json(saved_bytes(root / "bundle.json"))
    checker.require(
        set(manifest) == {"schema_version", "components", "files"}
        and type(manifest.get("schema_version")) is int
        and manifest["schema_version"] == 1,
        "Unsupported export manifest schema",
    )
    components = checker.mapping(manifest.get("components"), "components")
    checker.require(set(components) == {"unity", "server", "skill"}, "Missing selected components")
    package = checker.text(components.get("unity"), "Unity package commit")
    selected = checker.components(repo, package)
    checker.require(
        components
        == {"unity": selected.package, "server": selected.server, "skill": selected.skill},
        "Retained components differ from committed package pins",
    )
    files = checker.mapping(manifest.get("files"), "retained files")
    checker.require({"notes.md", "evidence.json"} <= set(files), "Missing prepared notes or index")
    checker.require(
        {path.name for path in root.iterdir()} == set(files) | {"bundle.json"},
        "Missing or unlisted retained files",
    )
    for name, raw_checksum in files.items():
        checker.require(
            re.fullmatch(r"notes\.md|evidence\.json|[0-9a-f]{64}\.bin", name) is not None,
            "Unsupported retained filename",
        )
        checksum = checker.text(raw_checksum, name + " digest")
        checker.require(
            checker.digest(checksum, 64)
            and hashlib.sha256(saved_bytes(root / name)).hexdigest() == checksum,
            "Retained SHA256 mismatch: " + name,
        )
    consistent(ExportRequest(repo, package, root / "notes.md", root / "evidence.json", root))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="Retain supplied saved records in a new directory")
    export.add_argument("--repo", type=Path, required=True)
    export.add_argument("--package-commit", required=True)
    export.add_argument("--notes", type=Path, required=True)
    export.add_argument("--evidence-index", type=Path, required=True)
    export.add_argument("--destination", type=Path, required=True)
    verify = commands.add_parser("verify", help="Verify a retained directory offline")
    verify.add_argument("--repo", type=Path, required=True)
    verify.add_argument("--bundle", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        match args.command:
            case "export":
                export_bundle(
                    ExportRequest(
                        args.repo,
                        args.package_commit,
                        args.notes,
                        args.evidence_index,
                        args.destination,
                    )
                )
            case "verify":
                validate_bundle(args.repo, args.bundle)
        print("Saved release evidence is consistent; no release was published.")
        return 0
    except (OSError, ValueError, KeyError, RecursionError, subprocess.TimeoutExpired) as error:
        print(error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
