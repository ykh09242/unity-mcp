"""Require native NUnit bodies and fresh, matching persisted scenario evidence."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from itertools import islice
from pathlib import Path
import re
import time
from typing import Final, Literal, assert_never
import uuid
import xml.etree.ElementTree as ET

from check_unity_test_results import check_results, escape_data

MAX_FILE_BYTES: Final = 8 * 1024 * 1024
MAX_FILES: Final = 512
ID_PATTERN: Final = re.compile(r"[a-f0-9]{32}")
METHOD_PATTERN: Final = re.compile(r"(?:[A-Za-z_][A-Za-z_0-9]*\.)+[A-Za-z_][A-Za-z_0-9]*")
EXPORT_PATTERN: Final = re.compile(r"[a-f0-9]{32}(?:\.suite)?\.json")
FINAL_STATES: Final = {"succeeded", "failed", "timed_out", "cancelled"}
Json = str | int | float | bool | None | list["Json"] | dict[str, "Json"]


def receipt_filename(method: str) -> str:
    """Keep receipt paths bounded even when exact native method names are long."""
    return hashlib.sha256(method.encode("utf-8")).hexdigest() + ".receipt.json"


@dataclass(frozen=True, slots=True)
class Document:
    """Bounded JSON object with explicit field readers at the evidence boundary."""

    value: dict[str, Json]

    @classmethod
    def parse(cls, value: Json) -> Document:
        if not isinstance(value, dict):
            raise ValueError("Expected a JSON object")
        return cls(value)

    def text(self, key: str) -> str:
        value = self.value.get(key)
        if not isinstance(value, str) or not value or len(value) > 512:
            raise ValueError(f"Invalid text field: {key}")
        return value

    def number(self, key: str) -> int:
        value = self.value.get(key)
        if type(value) is not int or value < 0:
            raise ValueError(f"Invalid integer field: {key}")
        return value

    def documents(self, key: str) -> tuple[Document, ...]:
        value = self.value.get(key)
        if not isinstance(value, list) or not 1 <= len(value) <= 64:
            raise ValueError(f"Invalid bounded array: {key}")
        return tuple(Document.parse(item) for item in value)


def unique_object(pairs: list[tuple[str, Json]]) -> dict[str, Json]:
    """Reject duplicate keys instead of accepting an ambiguous signed export."""
    result: dict[str, Json] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def read_document(path: Path) -> Document:
    """Reject linked, oversized and malformed inputs before parsing them."""
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError(f"Missing, linked or oversized evidence: {path.name}")
    raw = path.read_bytes()
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError(f"Oversized evidence: {path.name}")
    return Document.parse(json.loads(raw, object_pairs_hook=unique_object))


@dataclass(frozen=True, slots=True)
class Session:
    folder: Path
    session_id: str
    started: int

    def exported(self, name: str) -> Document:
        if not EXPORT_PATTERN.fullmatch(name):
            raise ValueError("Report paths must be owned export basenames")
        path = self.folder / name
        if path.resolve().parent != self.folder.resolve():
            raise ValueError("Report escapes evidence directory")
        return read_document(path)

    def finalized(self, report: Document) -> None:
        started = report.number("started_unix_ms")
        finished = report.number("finished_unix_ms")
        if not self.started <= started <= finished:
            raise ValueError("Stale or unfinalized report timestamps")
        if report.text("status") not in FINAL_STATES:
            raise ValueError("Report has no final outcome")
        if "report_error" not in report.value or report.value["report_error"] is not None:
            raise ValueError("Persisted report has a write error")

    def run(self, report: Document, expected_id: str) -> None:
        self.finalized(report)
        if report.text("job_id") != expected_id or not ID_PATTERN.fullmatch(expected_id):
            raise ValueError("Run ID mismatch")
        if report.value.get("runner_resources_released") is not True:
            raise ValueError("Run retained runner resources")

    def suite(self, report: Document, expected: Document) -> None:
        children = report.documents("scenarios")
        expected_states = expected.value.get("children")
        if not isinstance(expected_states, list) or len(children) != len(expected_states):
            raise ValueError("Suite child count mismatch")
        ids: set[str] = set()
        for child, status in zip(children, expected_states):
            identifier = child.text("job_id")
            allowed = status if isinstance(status, list) else [status]
            if not allowed or any(item not in FINAL_STATES | {"skipped"} for item in allowed):
                raise ValueError("Unknown expected child outcome")
            if (
                not ID_PATTERN.fullmatch(identifier)
                or identifier in ids
                or child.text("status") not in allowed
            ):
                raise ValueError("Suite child ID/status mismatch")
            ids.add(identifier)
            status = child.text("status")
            if status == "skipped":
                if child.value.get("report") is not None:
                    raise ValueError("Skipped child contains a report")
                continue
            embedded = Document.parse(child.value.get("report"))
            exported = self.exported(identifier + ".json")
            self.run(exported, identifier)
            if exported.value != embedded.value or exported.text("status") != status:
                raise ValueError("Suite child export mismatch")
            reproduction = Document.parse(exported.value.get("reproduction"))
            definition_hash = child.text("definition_hash")
            if not re.fullmatch(
                r"[a-f0-9]{64}", definition_hash
            ) or definition_hash != reproduction.text("definition_hash"):
                raise ValueError("Suite child definition hash mismatch")
            if (
                not report.number("started_unix_ms")
                <= exported.number("started_unix_ms")
                <= exported.number("finished_unix_ms")
                <= report.number("finished_unix_ms")
            ):
                raise ValueError("Suite child timestamps exceed suite lifetime")

    def receipt(self, required: Document) -> None:
        method = required.text("method")
        if not METHOD_PATTERN.fullmatch(method):
            raise ValueError("Invalid exact required method")
        receipt = read_document(self.folder / receipt_filename(method))
        if receipt.number("schema_version") != 1 or receipt.text("method") != method:
            raise ValueError("Receipt method/schema mismatch")
        completed = receipt.number("completed_unix_ms")
        if receipt.text("session_id") != self.session_id or completed < self.started:
            raise ValueError("Stale body completion receipt")
        references = receipt.documents("reports")
        expected_reports = required.documents("reports")
        if len(references) != len(expected_reports):
            raise ValueError("Required report count mismatch")
        seen: set[str] = set()
        for reference, expected in zip(references, expected_reports):
            identifier = reference.text("id")
            kind = expected.text("kind")
            if (
                kind not in {"run", "suite"}
                or not ID_PATTERN.fullmatch(identifier)
                or identifier in seen
            ):
                raise ValueError("Unknown report kind or duplicate/invalid ID")
            seen.add(identifier)
            name = identifier + (".suite.json" if kind == "suite" else ".json")
            if reference.text("file") != name:
                raise ValueError("Report reference path/ID mismatch")
            report = self.exported(name)
            digest = hashlib.sha256((self.folder / name).read_bytes()).hexdigest()
            if reference.text("sha256") != digest:
                raise ValueError("Report content changed after body completion")
            self.finalized(report)
            if (
                report.text("status") != expected.text("status")
                or report.number("finished_unix_ms") > completed
            ):
                raise ValueError("Report expected outcome/body completion mismatch")
            report_kind: Literal["run", "suite"] = "run" if kind == "run" else "suite"
            match report_kind:
                case "run":
                    self.run(report, identifier)
                case "suite":
                    if report.text("suite_id") != identifier:
                        raise ValueError("Suite ID mismatch")
                    self.suite(report, expected)
                case unreachable:
                    assert_never(unreachable)


def validate_xml(path: Path) -> None:
    """Fail closed on truncated aggregate buckets and unknown case results."""
    if path.is_symlink() or path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("Linked or oversized NUnit XML")
    root = ET.parse(path).getroot()
    cases = list(root.iter("test-case"))
    if len(cases) > 100000:
        raise ValueError("NUnit test count exceeds limit")
    buckets = Counter(case.get("result", "") for case in cases)
    states = {
        "Passed": "passed",
        "Failed": "failed",
        "Skipped": "skipped",
        "Inconclusive": "inconclusive",
    }
    if set(buckets) - states.keys() or len(cases) != int(root.attrib["total"]):
        raise ValueError("Unknown or truncated NUnit cases")
    if any(buckets[state] != int(root.attrib[bucket]) for state, bucket in states.items()):
        raise ValueError("NUnit aggregate/case counts disagree")


def check_evidence(folder: Path, manifest: Path, results: Path, runner_outcome: str) -> int:
    """Verify all required methods against this execution's report receipts."""
    try:
        if (
            folder.is_symlink()
            or not folder.is_dir()
            or len(list(islice(folder.iterdir(), MAX_FILES + 1))) > MAX_FILES
        ):
            raise ValueError("Missing, linked or excessive evidence directory")
        session_data = read_document(folder / ".session.json")
        session_id = session_data.text("session_id")
        if not ID_PATTERN.fullmatch(session_id):
            raise ValueError("Invalid evidence session ID")
        session = Session(folder, session_id, session_data.number("started_unix_ms"))
        definition = read_document(manifest)
        if definition.number("schema_version") != 1:
            raise ValueError("Unsupported evidence manifest")
        requirements = definition.documents("tests")
        methods = [item.text("method") for item in requirements]
        if len(set(methods)) != len(methods):
            raise ValueError("Duplicate required methods")
        validate_xml(results)
        if check_results(results, runner_outcome, methods):
            return 1
        for required in requirements:
            session.receipt(required)
    except (OSError, ValueError, KeyError, TypeError, RecursionError, ET.ParseError) as exc:
        print(f"::error::Native scenario evidence failed: {escape_data(str(exc))}")
        return 1
    print(f"Required native scenario evidence passed: {len(requirements)} bodies")
    return 0


def initialize(folder: Path) -> str:
    """Clear only owned evidence files after cache restoration and start a new session."""
    if folder.name != "PlayScenarioIntegrationEvidence" or folder.is_symlink():
        raise ValueError("Initializer requires the owned integration evidence directory")
    folder.mkdir(parents=True, exist_ok=True)
    entries = list(islice(folder.iterdir(), MAX_FILES + 1))
    if len(entries) > MAX_FILES:
        raise ValueError("Evidence cache exceeds cleanup limit")
    for path in entries:
        owned = (
            path.name == ".session.json"
            or EXPORT_PATTERN.fullmatch(path.name)
            or re.fullmatch(r"[a-f0-9]{64}\.receipt\.json", path.name)
            or (
                path.name.endswith(".receipt.json")
                and METHOD_PATTERN.fullmatch(path.name.removesuffix(".receipt.json"))
            )
        )
        if owned:
            if path.is_symlink() or not path.is_file():
                raise ValueError("Linked or nonregular evidence cache entry")
            path.unlink()
    session_id = uuid.uuid4().hex
    (folder / ".session.json").write_text(
        json.dumps({"session_id": session_id, "started_unix_ms": int(time.time() * 1000)}),
        encoding="utf-8",
    )
    return session_id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--initialize", action="store_true")
    parser.add_argument(
        "--manifest", type=Path, default=Path(__file__).with_name("unity-native-e2e-tests.json")
    )
    parser.add_argument(
        "--session-id", help="Fresh session ID returned by this execution's initializer."
    )
    parser.add_argument("--results", type=Path)
    parser.add_argument("--runner-outcome", default="skipped")
    args = parser.parse_args()
    if args.initialize:
        try:
            print(initialize(args.evidence))
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        return 0
    if args.results is None:
        parser.error("--results is required when validating evidence")
    if args.session_id is None:
        parser.error("--session-id from this execution's initializer is required")
    try:
        session = read_document(args.evidence / ".session.json")
        if session.text("session_id") != args.session_id:
            raise ValueError("Evidence session differs from this execution's initializer")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    return check_evidence(args.evidence, args.manifest, args.results, args.runner_outcome)


if __name__ == "__main__":
    raise SystemExit(main())
