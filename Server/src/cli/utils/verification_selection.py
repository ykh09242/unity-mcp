"""Compare independently executed Unity NUnit selections on the same instrumented input."""

from dataclasses import dataclass
from hashlib import sha256
import math
from pathlib import Path
import re
from xml.etree import ElementTree

from cli.utils.play_scenario_analysis_io import read_bytes


_ASSET = re.compile(r"(Assets/.+?) ([0-9a-f]{32}) ([0-9a-f]{64})")
_CONTEXT = "MCP_VERIFICATION_CONTEXT "
_TERMINAL = {"Passed", "Failed", "Skipped", "Inconclusive"}


@dataclass(frozen=True)
class Cohort:
    digest: str
    context: str
    inputs: dict[str, tuple[str, str]]
    cases: dict[str, str]
    seconds: float


def read_cohort(path: Path) -> Cohort:
    raw = read_bytes(path, 16 * 1024 * 1024)
    xml = raw.decode("utf-8-sig")
    if "<!DOCTYPE" in xml or "<!ENTITY" in xml:
        raise ValueError("NUnit evidence must not contain a document type or entities")
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise ValueError("Invalid NUnit evidence") from exc
    if root.tag != "test-run":
        raise ValueError("Expected Unity NUnit test-run")
    seconds = float(root.get("duration", "nan"))
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("Missing finite NUnit run duration")
    reference_inputs = None
    reference_context = None
    cases = {}
    for case in root.iter("test-case"):
        name, result = case.get("fullname"), case.get("result")
        if not name or name in cases or result not in _TERMINAL:
            raise ValueError("Expected unique named terminal NUnit cases")
        cases[name] = result
        inputs = {}
        contexts = set()
        for line in (case.findtext("output") or "").splitlines():
            if line.startswith(_CONTEXT):
                contexts.add(line.removeprefix(_CONTEXT).strip())
            match = _ASSET.fullmatch(line)
            if match:
                asset, guid, digest = match.groups()
                if asset in inputs:
                    raise ValueError("Duplicate fixture input observation")
                inputs[asset] = (guid, digest)
        if not inputs or len(contexts) != 1 or not all(contexts):
            raise ValueError("Each case needs fixture input digests and one execution context")
        context = contexts.pop()
        if reference_inputs is None:
            reference_inputs, reference_context = inputs, context
        if (inputs, context) != (reference_inputs, reference_context):
            raise ValueError("Input or execution context changed within the NUnit run")
    if not cases or len(cases) != int(root.get("total", "-1")):
        raise ValueError("Incomplete NUnit case inventory")
    if root.get("result") not in {"Passed", "Failed"} or (
        (root.get("result") == "Failed") != ("Failed" in cases.values())
    ):
        raise ValueError("Run outcome does not agree with individual case evidence")
    return Cohort(sha256(raw).hexdigest(), reference_context, reference_inputs, cases, seconds)


def selection_quality(selected: Cohort, full: Cohort, affected, fallback: Cohort | None = None):
    cohorts = [selected, full] if fallback is None else [selected, full, fallback]
    if len({row.digest for row in cohorts}) != len(cohorts):
        raise ValueError("Independent NUnit runs are required")
    if any((row.context, row.inputs) != (full.context, full.inputs) for row in cohorts):
        raise ValueError("Input or execution context differs across cohorts")
    truth = set(affected)
    if not truth.issubset(full.cases):
        raise ValueError("Declared affected checks must be present in the full run")
    failed = {key for key, state in full.cases.items() if state == "Failed"}

    def assess(run):
        if not run.cases.keys() <= full.cases.keys():
            raise ValueError("The selection contains cases outside the full run")
        mismatched = sorted(k for k, state in run.cases.items() if full.cases[k] != state)
        caught = {key for key in failed if run.cases.get(key) == "Failed"}
        return {
            "artifact_sha256": run.digest,
            "executed_count": len(run.cases),
            "caught_failures": sorted(caught),
            "missed_failures": sorted(failed - caught),
            "unnecessary_checks": sorted(run.cases.keys() - truth) if truth else None,
            "divergent_results": mismatched,
            "duration_seconds": run.seconds,
            "measured_seconds_saved": full.seconds - run.seconds,
        }

    narrow = assess(selected)
    broader = assess(fallback) if fallback is not None else None
    incomplete = any(value in {"Skipped", "Inconclusive"} for value in full.cases.values())
    incomplete |= bool(narrow["divergent_results"] or (broader and broader["divergent_results"]))
    return {
        "schema": "verification_selection_quality@1",
        "state": "incomplete" if incomplete else "measured",
        "context": full.context,
        "input_count": len(full.inputs),
        "full": {
            "artifact_sha256": full.digest,
            "executed_count": len(full.cases),
            "failed_checks": sorted(failed),
            "duration_seconds": full.seconds,
        },
        "selection": narrow,
        "fallback": broader,
    }
