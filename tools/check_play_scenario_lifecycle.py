"""Validate native lifecycle reports together with unchanged identity sidecars."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

from check_play_scenario_evidence import (
    Document,
    EXPORT_PATTERN,
    ID_PATTERN,
    MAX_FILE_BYTES,
    METHOD_PATTERN,
    check_evidence,
    read_document,
    receipt_filename,
    unique_object,
)
from check_unity_test_results import escape_data

MANIFEST = Path(__file__).with_name("play-scenario-lifecycle-tests.json")


def read_identities(path: Path, digest: str) -> tuple[Document, ...]:
    """Bound and hash the exact bytes referenced by the completed native body."""
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("Missing, linked or oversized identity evidence")
    raw = path.read_bytes()
    if len(raw) > MAX_FILE_BYTES or hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("Identity evidence changed after body completion")
    return Document({"items": json.loads(raw, object_pairs_hook=unique_object)}).documents("items")


def validate_identities(evidence: Path, identity_folder: Path, manifest: Path) -> int:
    """Require every acquired iteration to retain its matching lifetime observations."""
    if identity_folder.is_symlink() or not identity_folder.is_dir():
        raise ValueError("Missing or linked identity evidence directory")
    owners: set[str] = set()
    count = 0
    for required in read_document(manifest).documents("tests"):
        method = required.text("method")
        if not METHOD_PATTERN.fullmatch(method):
            raise ValueError("Invalid required method")
        receipt = read_document(evidence / receipt_filename(method))
        digest = receipt.text("identity_evidence_sha256")
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("Invalid identity evidence hash")
        path = identity_folder / (method.rsplit(".", 1)[1] + ".json")
        if path.resolve().parent != identity_folder.resolve():
            raise ValueError("Identity evidence escapes its directory")
        identities = read_identities(path, digest)
        expected: dict[tuple[str, int], Document] = {}
        for reference in receipt.documents("reports"):
            name = reference.text("file")
            if not EXPORT_PATTERN.fullmatch(name):
                raise ValueError("Invalid report basename")
            report = read_document(evidence / name)
            if Document.parse(report.value.get("scenario")).text("name") == "sample-release":
                continue
            for iteration in report.documents("iteration_results"):
                expected[(report.text("job_id"), iteration.number("iteration"))] = report
        observed: set[tuple[str, int]] = set()
        resource_ids: set[int] = set()
        for identity in identities:
            key = (identity.text("job_id"), identity.number("iteration"))
            if key not in expected or key in observed:
                raise ValueError("Missing, duplicate or mismatched acquisition identity")
            observed.add(key)
            owner = identity.text("owner_id")
            if not ID_PATTERN.fullmatch(owner) or owner in owners:
                raise ValueError("Invalid or reused session owner")
            owners.add(owner)
            native_id = identity.value.get("native_clone_id")
            ids = identity.value.get("resource_ids")
            if (
                type(native_id) is not int
                or native_id == 0
                or not isinstance(ids, list)
                or len(ids) != 3
                or any(type(value) is not int or value <= 0 for value in ids)
                or len(set(ids)) != 3
                or resource_ids.intersection(ids)
            ):
                raise ValueError("Invalid or reused native/registered resource identity")
            resource_ids.update(ids)
            report = expected[key]
            scenario = Document.parse(report.value.get("scenario")).text("name")
            retained = scenario in {"sample-retained", "sample-retained-teardown"}
            for field in ("native_clone_retained", "stream_open", "event_delivered_after_return"):
                if identity.value.get(field) is not retained:
                    raise ValueError("Actual resource lifetime disagrees with expected cleanup")
            if identity.value.get("source_unchanged") is not True:
                raise ValueError("The persistent source asset changed")
            if retained:
                resources = report.documents("resource_checks")[0].documents("retained_resources")
                if (
                    identity.value.get("explicit_release_verified") is not True
                    or {item.number("id") for item in resources} != set(ids)
                    or any(item.text("owner") != "lifecycle-session:" + owner for item in resources)
                ):
                    raise ValueError("Retained identities or explicit release evidence mismatch")
        if observed != set(expected):
            raise ValueError("Not every acquired iteration has identity evidence")
        count += len(identities)
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--identities", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--runner-outcome", default="skipped")
    args = parser.parse_args()
    try:
        session = read_document(args.evidence / ".session.json")
        if session.text("session_id") != args.session_id:
            raise ValueError("Evidence session differs from this execution's initializer")
        if check_evidence(args.evidence, MANIFEST, args.results, args.runner_outcome):
            return 1
        count = validate_identities(args.evidence, args.identities, MANIFEST)
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as exc:
        print(f"::error::Lifecycle sample evidence failed: {escape_data(str(exc))}")
        return 1
    print(f"Lifecycle identity evidence passed: {count} acquisitions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
