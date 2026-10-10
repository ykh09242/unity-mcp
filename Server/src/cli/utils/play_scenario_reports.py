"""Export native suite reports as bounded JUnit XML and original JSON artifacts."""

import json
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
