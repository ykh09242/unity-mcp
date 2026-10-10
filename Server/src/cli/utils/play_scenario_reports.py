"""Export native suite reports as bounded JUnit XML and original JSON artifacts."""

import json
import re
from pathlib import Path
from typing import Annotated, Final
from xml.etree import ElementTree

from pydantic import BaseModel, ConfigDict, Field, JsonValue

LOG_LIMIT: Final = 16384


class ReportEvidence(BaseModel):
    """Parse only fields needed for export; original JSON remains the source artifact."""

    model_config = ConfigDict(frozen=True, extra="ignore")
    job_id: str = ""
    started_unix_ms: int | None = None
    finished_unix_ms: int | None = None
    error: str | None = None
    report_error: str | None = None
    report_path: str | None = None
    logs: list[dict[str, JsonValue]] = Field(default_factory=list, max_length=128)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    reproduction: dict[str, JsonValue] = Field(default_factory=dict)
    failure_diagnostics: dict[str, JsonValue] | None = None
    failure: dict[str, JsonValue] | None = None
    query_counts: dict[str, JsonValue] = Field(default_factory=dict)
    timeline: list[dict[str, JsonValue]] = Field(default_factory=list, max_length=128)
    dropped_timeline_count: int = 0
    resource_checks: list[dict[str, JsonValue]] = Field(default_factory=list, max_length=10)
    steps: list[dict[str, JsonValue]] = Field(default_factory=list, max_length=640)


class SuiteCase(BaseModel):
    """One scenario outcome, including unstarted scenarios explicitly marked skipped."""

    model_config = ConfigDict(frozen=True, extra="ignore")
    name: str
    status: str
    definition_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None
    job_id: str | None = None
    report: ReportEvidence | None = None
    skip_reason: str | None = None


class SuiteReport(BaseModel):
    """Minimal native suite report boundary used by CI export."""

    model_config = ConfigDict(frozen=True, extra="ignore")
    suite_id: str = ""
    status: str | None = None
    error: str | None = None
    suite: dict[str, JsonValue] = Field(default_factory=dict)
    started_unix_ms: int | None = None
    finished_unix_ms: int | None = None
    report_error: str | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    scenarios: list[SuiteCase] = Field(max_length=16)
    source_revision: str | None = None
    client_error: str | None = None
    report_path: str | None = None
    repeat_count: int | None = None
    timeout_seconds: int | None = None


def _xml_text(value: str) -> str:
    """Remove only XML 1.0-invalid code points before ElementTree escapes markup."""
    return "".join(
        char
        for char in value
        if ord(char) in (9, 10, 13)
        or 0x20 <= ord(char) <= 0xD7FF
        or 0xE000 <= ord(char) <= 0xFFFD
        or 0x10000 <= ord(char) <= 0x10FFFF
    )


def _duration(start: int | None, finish: int | None) -> str:
    """Missing timestamps yield zero rather than invented execution duration."""
    seconds = max(0, finish - start) / 1000 if start is not None and finish is not None else 0
    return f"{seconds:.3f}"


def _properties(element: ElementTree.Element, values: dict[str, JsonValue]) -> None:
    """Preserve bounded scalar metadata and attachment references without reading files."""
    properties = ElementTree.SubElement(element, "properties")
    for name, value in values.items():
        if isinstance(value, (str, int, float, bool)):
            ElementTree.SubElement(
                properties,
                "property",
                name=_xml_text(name[:128]),
                value=_xml_text(str(value)[:4096]),
            )


def _safe_context_text(value: str) -> str:
    """Redact credential assignments and absolute paths in newly exported diagnostic context."""
    value = re.sub(
        r"(?i)\b(token|secret|password|api[_-]?key|authorization)\s*[:=]\s*[^\s,;]+",
        r"\1=<redacted>",
        value,
    )
    value = re.sub(r"(?<![\w])(?:[A-Za-z]:[\\/]|\\\\|/)[^\s<>\"']+", "<path>", value)
    return _xml_text(value[:512])


def _context_fields(entry: dict[str, JsonValue], fields: set[str]) -> dict[str, JsonValue]:
    """Export only known bounded scalar diagnostic fields, never arbitrary object graphs."""
    return {
        key: _safe_context_text(value) if isinstance(value, str) else value
        for key, value in entry.items()
        if key in fields and isinstance(value, (str, int, float, bool))
    }


def _failure_context(evidence: ReportEvidence) -> str:
    """Build a bounded additive summary of timeline, query budgets and resource ownership."""
    context: dict[str, JsonValue] = {}
    if evidence.failure:
        context["failure"] = _context_fields(
            evidence.failure,
            {
                "code",
                "stage",
                "iteration",
                "step_index",
                "target",
                "component",
                "property_path",
                "message",
            },
        )
    if evidence.query_counts:
        context["query_counts"] = _context_fields(
            evidence.query_counts, {"target_searches", "hierarchy_visits"}
        )
    if evidence.timeline:
        context["timeline"] = [
            _context_fields(
                event,
                {
                    "sequence",
                    "timestamp_unix_ms",
                    "stage",
                    "iteration",
                    "step_index",
                    "event",
                    "detail",
                },
            )
            for event in evidence.timeline
        ]
        context["dropped_timeline_count"] = evidence.dropped_timeline_count
    if evidence.resource_checks:
        checks: list[JsonValue] = []
        for check in evidence.resource_checks:
            bounded = _context_fields(
                check,
                {
                    "iteration",
                    "new_scriptable_objects",
                    "new_subscriptions",
                    "new_handles",
                    "passed",
                    "omitted_resource_count",
                },
            )
            resources = check.get("retained_resources")
            if isinstance(resources, list):
                bounded["retained_resources"] = [
                    _context_fields(
                        resource,
                        {
                            "id",
                            "kind",
                            "owner",
                            "type_name",
                            "resource_name",
                            "source_file",
                            "source_member",
                            "source_line",
                        },
                    )
                    for resource in resources[:32]
                    if isinstance(resource, dict)
                ]
            checks.append(bounded)
        context["resource_checks"] = checks
    counted_steps: list[JsonValue] = []
    for step in evidence.steps:
        counts = step.get("query_counts")
        if isinstance(counts, dict):
            entry = _context_fields(step, {"stage", "iteration", "step_index", "name"})
            entry["query_counts"] = _context_fields(counts, {"target_searches", "hierarchy_visits"})
            counted_steps.append(entry)
    if counted_steps:
        context["step_query_counts"] = counted_steps
    return (
        json.dumps(context, ensure_ascii=True, separators=(",", ":"))[:LOG_LIMIT] if context else ""
    )


def junit_xml(raw: dict[str, JsonValue]) -> bytes:
    """Represent each scenario as one testcase with truthful outcomes and bounded logs."""
    report = SuiteReport.model_validate(raw)
    suite_name = str(report.suite.get("name", "play-scenario-suite"))
    suite = ElementTree.Element(
        "testsuite",
        name=_xml_text(suite_name),
        tests=str(len(report.scenarios)),
        time=_duration(report.started_unix_ms, report.finished_unix_ms),
    )
    _properties(
        suite,
        {
            "suite_id": report.suite_id,
            "source_revision": report.source_revision,
            "report_path": report.report_path,
            "repeat_count": report.repeat_count,
            "timeout_seconds": report.timeout_seconds,
            **report.metadata,
        },
    )
    counts = {"failures": 0, "errors": 0, "skipped": 0}
    for entry in report.scenarios:
        evidence = entry.report or ReportEvidence()
        case = ElementTree.SubElement(
            suite,
            "testcase",
            name=_xml_text(entry.name),
            classname=_xml_text(suite_name),
            time=_duration(evidence.started_unix_ms, evidence.finished_unix_ms),
        )
        properties: dict[str, JsonValue] = {
            "job_id": entry.job_id or evidence.job_id,
            "definition_hash": entry.definition_hash,
            **evidence.metadata,
            **evidence.reproduction,
        }
        if evidence.report_path:
            properties["report_path"] = evidence.report_path
        if evidence.failure_diagnostics:
            properties["screenshot_path"] = evidence.failure_diagnostics.get("screenshot_path")
        _properties(case, properties)
        if entry.status == "skipped":
            counts["skipped"] += 1
            ElementTree.SubElement(case, "skipped").text = _xml_text(entry.skip_reason or "Skipped")
        elif entry.status == "failed":
            counts["failures"] += 1
            ElementTree.SubElement(case, "failure", type="scenario_failed").text = _xml_text(
                (evidence.error or entry.skip_reason or "Scenario failed")[:4096]
            )
        elif entry.status != "succeeded":
            counts["errors"] += 1
            ElementTree.SubElement(case, "error", type=_xml_text(entry.status)).text = _xml_text(
                (evidence.error or entry.skip_reason or entry.status)[:4096]
            )
        elif evidence.report_error:
            counts["errors"] += 1
            ElementTree.SubElement(case, "error", type="report_persist_failed").text = _xml_text(
                evidence.report_error[:4096]
            )
        if evidence.report_error and entry.status != "succeeded":
            ElementTree.SubElement(case, "system-err").text = _xml_text(
                evidence.report_error[:4096]
            )
        if evidence.logs:
            bounded_logs = [
                {
                    name: value[:4096] if isinstance(value, str) else value
                    for name, value in log.items()
                    if name in {"timestamp_unix_ms", "type", "message", "stack_trace"}
                    and isinstance(value, (str, int, float, bool))
                }
                for log in evidence.logs
            ]
            logs = json.dumps(bounded_logs, ensure_ascii=True, separators=(",", ":"))
            ElementTree.SubElement(case, "system-out").text = _xml_text(logs[:LOG_LIMIT])
        context = _failure_context(evidence)
        if context:
            output = case.find("system-out")
            if output is None:
                output = ElementTree.SubElement(case, "system-out")
            output.text = _xml_text(((output.text + "\n") if output.text else "") + context)[
                :LOG_LIMIT
            ]
    infrastructure_error = report.report_error or report.client_error
    missing_parent_outcome = report.status in {"failed", "timed_out", "cancelled"} and not (
        counts["failures"] or counts["errors"]
    )
    if infrastructure_error or missing_parent_outcome:
        case = ElementTree.SubElement(
            suite,
            "testcase",
            name="suite-finalization" if infrastructure_error else "suite-outcome",
            classname=_xml_text(suite_name),
            time="0.000",
        )
        detail = infrastructure_error or report.error or f"Suite {report.status}"
        if infrastructure_error and report.error and report.error != infrastructure_error:
            detail = report.error + "\n" + infrastructure_error
        error_type = (
            "suite_infrastructure_error" if infrastructure_error else f"suite_{report.status}"
        )
        ElementTree.SubElement(case, "error", type=error_type).text = _xml_text(detail[:4096])
        counts["errors"] += 1
        suite.set("tests", str(len(report.scenarios) + 1))
    for name, count in counts.items():
        suite.set(name, str(count))
    return ElementTree.tostring(suite, encoding="utf-8", xml_declaration=True)


def write_suite_artifacts(raw: dict[str, JsonValue], directory: Path) -> tuple[Path, Path]:
    """Persist both reports in an explicit caller-selected directory; propagate write errors."""
    xml = junit_xml(raw)
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / "suite.json"
    xml_path = directory / "junit.xml"
    json_path.write_text(json.dumps(raw, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    xml_path.write_bytes(xml)
    return json_path, xml_path


def write_player_artifacts(
    raw: dict[str, JsonValue], directory: Path, client_error: str | None = None
) -> tuple[Path, Path]:
    """Export a single actual Player report without fabricating a suite execution outcome."""
    scenario = raw.get("scenario")
    name = str(scenario.get("name", "player")) if isinstance(scenario, dict) else "player"
    reproduction = raw.get("reproduction")
    definition_hash = (
        reproduction.get("definition_hash") if isinstance(reproduction, dict) else None
    )
    adapter: dict[str, JsonValue] = {
        "suite": {"name": name},
        "started_unix_ms": raw.get("started_unix_ms"),
        "finished_unix_ms": raw.get("finished_unix_ms"),
        "repeat_count": raw.get("repeat_count"),
        "timeout_seconds": raw.get("timeout_seconds"),
        "source_revision": (
            reproduction.get("source_revision") if isinstance(reproduction, dict) else None
        ),
        "scenarios": [
            {
                "name": name,
                "status": raw.get("status"),
                "job_id": raw.get("job_id"),
                "definition_hash": definition_hash,
                "report": raw,
            }
        ],
    }
    if client_error is not None:
        adapter["client_error"] = client_error
    xml = junit_xml(adapter)
    json_path = directory / "run.json"
    xml_path = directory / "junit.xml"
    # run.json is the native artifact; preserve its exact bytes when it already exists.
    if not json_path.exists():
        json_path.write_text(json.dumps(raw, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    xml_path.write_bytes(xml)
    return json_path, xml_path
