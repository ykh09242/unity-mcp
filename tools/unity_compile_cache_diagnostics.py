"""Compare a restored public compiler cache with its receipt without changing it."""

from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import re
import sys

import unity_compile_cache as cache


MAX_DETAILS = 20


def _relative_file(value: str, directories: list[str]) -> bool:
    path = PurePosixPath(value)
    return (value == path.as_posix() and not path.is_absolute() and ".." not in path.parts
            and "\\" not in value and ":" not in value
            and any(value == root if cache._ui_reference(root) else value.startswith(root + "/")
                    for root in directories))


def diagnose(manifest: cache.unity_ci.Manifest, version: str, directory: Path) -> dict:
    details = cache.identity(manifest, version)
    directory = cache._destination(directory, version)
    data, receipt_path = directory / "Data", directory / cache.RECEIPT
    if not data.is_dir() or cache._linked(data) or cache._linked(receipt_path):
        raise ValueError("Cache Data and receipt must be unlinked ordinary inputs")
    if receipt_path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("Cache receipt exceeds the diagnostic size bound")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt["cache_key"] != details["cache_key"] or receipt["provenance"] != details["provenance"]:
        raise ValueError("Cache source identity does not match the selected manifest version")
    directories = receipt["directories"]
    expected = receipt["files"]
    if (not isinstance(directories, list) or not directories
            or any(not cache._allowed_input(value) for value in directories)
            or not isinstance(expected, dict) or not expected):
        raise ValueError("Malformed compiler-cache receipt")
    for relative, record in expected.items():
        if (not _relative_file(relative, directories) or not isinstance(record, dict)
                or set(record) != {"size", "mode", "sha256"}
                or type(record["size"]) is not int or record["size"] < 0
                or type(record["mode"]) is not int or not 0 <= record["mode"] <= 0o7777
                or not isinstance(record["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", record["sha256"])):
            raise ValueError("Invalid public compiler file record")
    for path in data.rglob("*"):
        relative = path.relative_to(data).as_posix()
        if cache._linked(path):
            raise ValueError("Linked compiler input cannot be inspected")
        if not (_relative_file(relative, directories) or path.is_dir()
                and any(root.startswith(relative + "/") or root == relative and not cache._ui_reference(root)
                        for root in directories)):
            raise ValueError("Compiler input outside the public cache scope")
    actual = cache._records(data)
    mismatches = []
    total = 0
    for relative in sorted(expected.keys() | actual.keys()):
        if expected.get(relative) != actual.get(relative):
            total += 1
            if len(mismatches) < MAX_DETAILS:
                mismatches.append({"path": relative, "expected": expected.get(relative),
                                   "actual": actual.get(relative)})
    return {"version": version, "cache_key": details["cache_key"], "total_mismatches": total,
            "mismatches": mismatches, "truncated": total > len(mismatches)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version")
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=Path(__file__).with_name("unity-versions.json"))
    args = parser.parse_args(argv)
    try:
        manifest = cache.unity_ci.load_manifest(args.manifest)
        result = diagnose(manifest, args.version, args.cache)
        print(json.dumps(result, sort_keys=True))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Unity compiler cache diagnostic failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
