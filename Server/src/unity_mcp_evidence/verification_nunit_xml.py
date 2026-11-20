"""Bounded incremental decoding and hashing for retained NUnit XML."""

import math
from codecs import getincrementaldecoder
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import BinaryIO
from xml.etree import ElementTree

from .play_scenario_analysis_io import AnalysisError, open_artifact
from .play_scenario_report_json import REPORT_LIMIT

_CHUNK_SIZE = 64 * 1024
_FORBIDDEN_MARKERS = ("<!DOCTYPE", "<!ENTITY")
_MARKER_OVERLAP = max(map(len, _FORBIDDEN_MARKERS)) - 1
DECISIVE_RESULTS = frozenset({"Passed", "Failed"})
_TERMINAL_RESULTS = DECISIVE_RESULTS | {"Skipped", "Inconclusive"}


@dataclass(slots=True)
class NUnitXml:
    """Track the digest while yielding XML events from one admitted file."""

    file: BinaryIO
    size: int
    digest: str = field(init=False, default="")

    def events(self) -> Iterator[tuple[str, ElementTree.Element]]:
        parser = ElementTree.XMLPullParser(events=("start", "end"))
        decoder = getincrementaldecoder("utf-8-sig")()
        digest = sha256()
        remaining = self.size
        overlap = ""
        forbidden = False
        parse_error = None
        while True:
            chunk = self.file.read(min(_CHUNK_SIZE, remaining + 1))
            remaining -= len(chunk)
            if remaining < 0 or (not chunk and remaining):
                raise AnalysisError("Artifact changed while reading")
            digest.update(chunk)
            decoded = decoder.decode(chunk, final=not chunk)
            # Check even after a parse error, matching the original UTF-8 and marker
            # checks that ran before parsing. Never feed a completed marker to Expat.
            window = overlap + decoded
            forbidden = forbidden or any(marker in window for marker in _FORBIDDEN_MARKERS)
            overlap = window[-_MARKER_OVERLAP:]
            if not forbidden and parse_error is None:
                try:
                    parser.feed(decoded)
                    yield from parser.read_events()
                    if not chunk:
                        parser.close()
                except ElementTree.ParseError as exc:
                    parse_error = exc
            if not chunk:
                break
        if forbidden:
            raise ValueError("NUnit evidence must not contain a document type or entities")
        if parse_error is not None:
            raise ValueError("Invalid NUnit evidence") from parse_error
        self.digest = digest.hexdigest()


@dataclass(frozen=True, slots=True)
class NUnitCase:
    name: str
    result: str
    duration: float | None
    failure_message: str | None
    assembly: str | None


@dataclass(frozen=True, slots=True)
class NUnitRun:
    digest: str
    result: str
    seconds: float
    cases: dict[str, NUnitCase]
    size: int


def _case_duration(case: ElementTree.Element) -> float | None:
    try:
        duration = float(case.get("duration", "nan"))
    except ValueError:
        return None
    return duration if math.isfinite(duration) and duration >= 0 else None


def _keep_case_evidence(
    element: ElementTree.Element, ancestors: list[ElementTree.Element], capture_output: bool
) -> bool:
    parent = ancestors[-1]
    if parent.tag == "test-case" and (
        element.tag == "failure" or (capture_output and element.tag == "output")
    ):
        return parent.find(element.tag) is element
    return (
        len(ancestors) >= 2
        and ancestors[-2].tag == "test-case"
        and parent.tag == "failure"
        and element.tag == "message"
        and parent.find("message") is element
    )


def read_test_run(
    path: Path,
    *,
    maximum_bytes: int = REPORT_LIMIT,
    observe_case: Callable[[ElementTree.Element, str], None] | None = None,
) -> NUnitRun:
    """Stream one complete local NUnit result, optionally visiting fixture case output."""
    with open_artifact(path, min(maximum_bytes, REPORT_LIMIT)) as (file, size):
        return _read_test_run(NUnitXml(file, size), observe_case)


def _read_test_run(
    xml: NUnitXml, observe_case: Callable[[ElementTree.Element, str], None] | None
) -> NUnitRun:
    stack: list[ElementTree.Element] = []
    cases: dict[str, NUnitCase] = {}
    names: set[str] = set()
    error: ValueError | None = None
    root: dict[str, str] = {}
    seconds = 0.0
    for event, element in xml.events():
        try:
            if event == "start" and error is None:
                if not stack:
                    if element.tag != "test-run":
                        raise ValueError("Expected Unity NUnit test-run")
                    root = dict(element.attrib)
                    seconds = float(root.get("duration", "nan"))
                    if not math.isfinite(seconds) or seconds < 0:
                        raise ValueError("Missing finite NUnit run duration")
                if element.tag == "test-case":
                    name, result = element.get("fullname", ""), element.get("result", "")
                    if not name or name in names or result not in _TERMINAL_RESULTS:
                        raise ValueError("Expected unique named terminal NUnit cases")
                    names.add(name)
            elif event == "end" and element.tag == "test-case" and error is None:
                name, result = element.attrib["fullname"], element.attrib["result"]
                if observe_case is not None:
                    observe_case(element, result)
                assembly = next(
                    (
                        ancestor.get("fullname") or ancestor.get("name")
                        for ancestor in reversed(stack[:-1])
                        if ancestor.tag == "test-suite" and ancestor.get("type") == "Assembly"
                    ),
                    None,
                )
                failure = element.find("failure")
                cases[name] = NUnitCase(
                    name,
                    result,
                    _case_duration(element),
                    (failure.findtext("message") or None) if failure is not None else None,
                    assembly,
                )
        except ValueError as exc:
            # Keep validating the whole input before reporting semantic errors.
            error = exc
        if event == "start":
            stack.append(element)
        else:
            stack.pop()
            if not stack:
                element.clear()
            elif error is not None or not _keep_case_evidence(
                element, stack, observe_case is not None
            ):
                stack[-1].remove(element)
                element.clear()
    if error is not None:
        raise error
    if not cases or len(cases) != int(root.get("total", "-1")):
        raise ValueError("Incomplete NUnit case inventory")
    result = root.get("result", "").removesuffix("(Child)")
    if result.startswith("Skipped:"):
        result = "Skipped"
    results = {case.result for case in cases.values()}
    if result not in results or (result == "Failed") != ("Failed" in results):
        raise ValueError("Run outcome does not agree with individual case evidence")
    return NUnitRun(xml.digest, result, seconds, cases, xml.size)
