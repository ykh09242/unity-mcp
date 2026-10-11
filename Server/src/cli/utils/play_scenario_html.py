"""Render saved execution evidence as a standalone, script-free HTML report."""

from dataclasses import dataclass
from html import escape
import json
import math
from pathlib import Path
from typing import Final

from pydantic import Field, JsonValue, ValidationError

from cli.utils.play_scenario_html_assets import TOTAL_IMAGE_LIMIT, screenshot_html, unlinked
from cli.utils.play_scenario_reports import ReportEvidence, SuiteReport
from cli.utils.play_scenario_soak import read_soak_json
from cli.utils.play_scenario_soak_compare import compare_soak_metrics
from cli.utils.play_scenario_soak_compare_evidence import read_saved_soak

INPUT_LIMIT: Final = 16 * 1024 * 1024
OUTPUT_LIMIT: Final = 24 * 1024 * 1024
METRICS: Final = ("managed_bytes", "allocated_bytes", "object_count")

STYLE: Final = """
:root { color-scheme: light; --ink: #20252b; --muted: #58636f; --line: #dce1e6;
  --paper: #fff; --canvas: #f5f6f8; --blue: #235da4; --red: #a02832; --green: #236744; }
* { box-sizing: border-box; }
body { margin: 0; background: var(--canvas); color: var(--ink);
  font: 15px/1.6 system-ui, -apple-system, sans-serif; }
a { color: var(--blue); text-underline-offset: 3px; }
a:focus-visible, summary:focus-visible { outline: 2px solid var(--blue); outline-offset: 4px; }
header { background: #252b33; color: #fff; padding: 32px max(24px, calc((100% - 1120px)/2)); }
header p { color: #d0d7df; margin: 6px 0; }
h1 { font-size: clamp(26px, 4vw, 38px); line-height: 1.2; margin: 8px 0 16px; overflow-wrap: anywhere; }
h2 { font-size: 22px; margin: 0 0 16px; } h3 { font-size: 17px; margin: 22px 0 8px; }
nav { display: flex; flex-wrap: wrap; gap: 12px 24px; margin: 20px 0; }
main, footer { max-width: 1168px; padding: 0 24px; margin: 0 auto; }
section, article { background: var(--paper); padding: 24px; margin: 20px 0; border: 1px solid var(--line); border-radius: 6px; }
.eyebrow { font-size: 12px; letter-spacing: .1em; text-transform: uppercase; }
.status { display: inline-block; padding: 3px 10px; font-size: 13px; font-weight: 650; white-space: nowrap;
  border-radius: 4px; background: #e8ecf0; color: #34404c; }
.good { background: #e3f2e9; color: var(--green); } .bad { background: #fbe8e9; color: var(--red); }
.muted, figcaption { color: var(--muted); } .error { color: var(--red); white-space: pre-wrap; }
.table-wrap { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 14px; }
th, td { text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { color: var(--muted); font-weight: 600; background: #f7f8fa; }
td { overflow-wrap: anywhere; } td:first-child { min-width: 140px; }
pre, code { font: 12px/1.6 ui-monospace, Consolas, monospace; }
pre { margin: 8px 0; padding: 14px; background: #f5f7f9; white-space: pre-wrap; overflow-wrap: anywhere; }
summary { cursor: pointer; font-weight: 600; padding: 8px 0; }
details { margin: 12px 0; } figure { margin: 16px 0; max-width: 560px; }
img { max-width: 100%; width: 100%; height: auto; border: 1px solid var(--line); }
svg { width: 100%; max-height: 220px; background: #f8fafc; }
.chart-line { fill: none; stroke: var(--blue); stroke-width: 2; }
.chart-point { fill: var(--blue); } footer { color: var(--muted); padding-bottom: 32px; }
.baseline { stroke: #8a5919; } .baseline-point { fill: #8a5919; }
@media (max-width: 600px) { main, footer { padding-left: 12px; padding-right: 12px; }
  section, article { padding: 16px; } th, td { padding: 8px; } }
"""


class HtmlReportError(ValueError):
    """The explicit saved inputs cannot be exported safely or completely."""


class _RunReport(ReportEvidence):
    scenario: dict[str, JsonValue] = Field(default_factory=dict)
    metrics: list[dict[str, JsonValue]] = Field(default_factory=list, max_length=1000)


@dataclass
class _Attachments:
    """Mutable byte/count budget shared by every case of one report."""

    root: Path | None
    remaining: int = TOTAL_IMAGE_LIMIT
    count: int = 0

    def image(self, value: dict[str, JsonValue]) -> str:
        if self.count >= 16:
            return '<p class="muted">Image omitted: report attachment limit reached.</p>'
        self.count += 1
        result, used = screenshot_html(value, self.root, self.remaining)
        self.remaining -= used
        return result


def _text(value: JsonValue, limit: int = 4096) -> str:
    if value is None:
        return "Unavailable"
    text = str(value)
    return escape(text[:limit] + (" [truncated]" if len(text) > limit else ""), quote=True)


def _status(value: JsonValue) -> str:
    kind = "good" if value in ("succeeded", "passed", "within_budget") else ""
    if value in ("failed", "timed_out", "budget_exceeded", "exceeded"):
        kind = "bad"
    return f'<span class="status {kind}">{_text(value, 64)}</span>'


def _json(value: JsonValue, limit: int = 65536) -> str:
    encoded = json.dumps(value, ensure_ascii=True, indent=2, allow_nan=False)
    suffix = "\n[Display truncated; consult the source JSON.]" if len(encoded) > limit else ""
    return "<pre>" + escape(encoded[:limit] + suffix) + "</pre>"


def _details(title: str, value: JsonValue) -> str:
    return f"<details><summary>{escape(title)}</summary>{_json(value)}</details>" if value else ""


def _duration(start: JsonValue, finish: JsonValue) -> str:
    if type(start) is int and type(finish) is int and finish >= start:
        return f"{finish - start:,} ms"
    return "Unavailable"


def _chart(
    samples: list[dict[str, JsonValue]],
    metric: str,
    baseline: list[dict[str, JsonValue]] | None = None,
) -> str:
    series = (("candidate", samples), ("baseline", baseline or []))
    valid = [
        sample[metric]
        for _, rows in series
        for sample in rows
        if type(sample.get(metric)) in (int, float) and math.isfinite(sample[metric])
    ]
    if not valid:
        return ""
    low, high = min(valid), max(valid)
    span = max(high - low, 1)
    shapes: list[str] = []
    count = max(len(rows) for _, rows in series)
    for name, rows in series:
        segment: list[str] = []
        for index, sample in enumerate(rows):
            value = sample.get(metric)
            if type(value) not in (int, float) or not math.isfinite(value):
                if segment:
                    shapes.append(
                        f'<polyline class="chart-line {name}" points="{" ".join(segment)}"/>'
                    )
                    segment = []
                continue
            x, y = 24 + index * 752 / max(count - 1, 1), 164 - (value - low) * 136 / span
            segment.append(f"{x:.2f},{y:.2f}")
            shapes.append(
                f'<circle class="chart-point {name}-point" cx="{x:.2f}" cy="{y:.2f}" r="2.5"/>'
            )
        if segment:
            shapes.append(f'<polyline class="chart-line {name}" points="{" ".join(segment)}"/>')
    legend = " Blue: candidate; brown: baseline." if baseline is not None else ""
    return (
        f'<h3>{escape(metric)}</h3><svg viewBox="0 0 800 190" role="img" '
        f'aria-label="{escape(metric)} by recorded sample order" data-samples="{len(valid)}">'
        f"<title>{escape(metric)}: {low:,} to {high:,}</title>{''.join(shapes)}</svg>"
        f'<p class="muted">Range {low:,} to {high:,}. {len(valid)} observed samples. '
        f"Missing samples break the curve.{legend} All recorded samples, including warmup, "
        "are shown. Budget decisions retain their own sample eligibility rules.</p>"
    )


def _run(raw: dict[str, JsonValue], name: str, attachments: _Attachments) -> str:
    run = _RunReport.model_validate(raw)
    soak = raw.get("soak")
    outcome = soak.get("status") if isinstance(soak, dict) else run.status
    infrastructure = [
        raw.get(key)
        for key in ("persistence_error", "progress_error", "report_error", "client_error")
    ]
    if outcome == "succeeded" and (any(infrastructure) or raw.get("exit_code", 0) not in (0, None)):
        outcome = "failed"
    result = f"<h2>{escape(name)}</h2>{_status(outcome)}"
    if isinstance(soak, dict):
        result += (
            f"<p>Soak session outcome above. Retained iteration outcome: {_status(run.status)}</p>"
        )
        result += _details("Soak session counts and failure", soak)
    if "exit_code" in raw:
        result += f"<p>Player exit code: {_text(raw.get('exit_code'))}</p>"
    result += _details("Finalization state", raw.get("finalization_state"))
    result += f'<p class="muted">Job {_text(run.job_id)} &middot; {_duration(run.started_unix_ms, run.finished_unix_ms)}</p>'
    for error in (run.error, *infrastructure):
        if error:
            result += f'<p class="error">{_text(error)}</p>'
    result += _details("Execution provenance", run.reproduction)
    result += _details("Failure details", run.failure)
    result += _details("Cleanup failures", raw.get("cleanup_failures"))
    if run.steps:
        result += '<h3>Steps</h3><div class="table-wrap"><table><thead><tr><th scope="col">Step</th><th scope="col">Position</th><th scope="col">Status</th><th scope="col">Duration</th><th scope="col">Evidence</th></tr></thead><tbody>'
        for step in run.steps:
            details = _text(step.get("detail", ""))
            details += _details("Performance", step.get("performance"))
            shot = step.get("screenshot")
            if isinstance(shot, dict):
                details += _details("Screenshot evidence", shot) + attachments.image(shot)
            result += (
                f"<tr><td>{_text(step.get('name'))}</td><td>{_text(step.get('stage'))} / "
                f"{_text(step.get('iteration'))} / {_text(step.get('step_index'))}</td>"
                f"<td>{_status(step.get('status'))}</td><td>{_duration(step.get('started_unix_ms'), step.get('finished_unix_ms'))}</td><td>{details}</td></tr>"
            )
        result += "</tbody></table></div>"
    for metric in METRICS:
        result += _chart(run.metrics, metric)
    result += _details("Metric samples", run.metrics)
    result += _details("Iteration outcomes", run.iteration_results)
    result += _details("Logs", run.logs)
    result += _details("Timeline", run.timeline)
    result += _details("Resource checks", run.resource_checks)
    result += _details("Failure screenshot reference", run.failure_diagnostics)
    return result


def _comparison_table(value: dict[str, JsonValue]) -> str:
    """Expose key comparison values while retaining the full source result below."""
    metrics = value.get("metrics")
    if not isinstance(metrics, (list, tuple)):
        return ""
    rows = []
    for item in metrics[:16]:
        if not isinstance(item, dict):
            continue
        baseline, candidate = item.get("baseline"), item.get("candidate")
        if not isinstance(baseline, dict) or not isinstance(candidate, dict):
            continue
        for key, delta in (
            ("net_change", "net_change_delta"),
            ("slope_per_iteration", "slope_delta"),
        ):
            rows.append(
                f"<tr><td>{_text(item.get('metric'))} / {key}</td>"
                f"<td>{_text(baseline.get(key))}</td><td>{_text(candidate.get(key))}</td>"
                f"<td>{_text(item.get(delta))}</td></tr>"
            )
    if not rows:
        return ""
    return (
        '<div class="table-wrap"><table><thead><tr><th scope="col">Metric</th><th scope="col">Baseline</th><th scope="col">Candidate</th><th scope="col">Delta</th></tr></thead><tbody>'
        + "".join(rows)
        + "</tbody></table></div>"
    )


def _analysis_table(value: dict[str, JsonValue]) -> str:
    groups = value.get("groups")
    if not isinstance(groups, list):
        return ""
    rows = []
    for index, group in enumerate(groups[:64], 1):
        if not isinstance(group, dict):
            continue
        counts = group.get("counts")
        if not isinstance(counts, dict):
            continue
        rows.append(
            f"<tr><td>Group {index}</td><td>{_text(counts.get('passed'))}</td>"
            f"<td>{_text(counts.get('failed'))}</td><td>{_text(counts.get('timed_out'))}</td>"
            f"<td>{_text(counts.get('unknown'))}</td><td>{_text(group.get('failure_rate'))}</td>"
            f"<td>{_text(group.get('intermittent'))}</td></tr>"
        )
    return (
        '<div class="table-wrap"><table><thead><tr><th scope="col">Compatible group</th><th scope="col">Passed</th><th scope="col">Failed</th><th scope="col">Timed out</th><th scope="col">Unknown</th><th scope="col">Failure rate</th><th scope="col">Intermittent</th></tr></thead><tbody>'
        + "".join(rows)
        + "</tbody></table></div>"
    )


def render_html(
    raw: dict[str, JsonValue],
    *,
    artifact_root: Path | None = None,
    comparison: dict[str, JsonValue] | None = None,
    analysis: dict[str, JsonValue] | None = None,
    visual: dict[str, JsonValue] | None = None,
    visual_root: Path | None = None,
    baseline_samples: list[dict[str, JsonValue]] | None = None,
) -> str:
    """Render bounded saved values without scripts, network requests or arbitrary file links."""
    attachments = _Attachments(artifact_root)
    try:
        if isinstance(raw.get("scenarios"), list):
            suite = SuiteReport.model_validate(raw)
            title = str(suite.suite.get("name", "Scenario suite"))[:256]
            content = f"<section><h2>Suite outcome</h2>{_status(suite.status)}"
            for error in (suite.error, suite.report_error, suite.client_error):
                if error:
                    content += f'<p class="error">{_text(error)}</p>'
            content += "</section>"
            for case, source in zip(suite.scenarios, raw["scenarios"], strict=True):
                detail = source.get("report") if isinstance(source, dict) else None
                body = f"<h2>{_text(case.name)}</h2>{_status(case.status)}"
                if isinstance(detail, dict):
                    body += _run(detail, "Execution details", attachments)
                else:
                    body += f"<p>{_text(case.skip_reason)}</p>"
                content += f"<article>{body}</article>"
        elif isinstance(raw.get("scenario"), dict) and isinstance(raw.get("status"), str):
            title = str(raw["scenario"].get("name", "Scenario run"))[:256]
            content = "<section>" + _run(raw, title, attachments) + "</section>"
        else:
            raise HtmlReportError("Expected a saved scenario run or suite report")
        supplemental = ""
        if baseline_samples is not None:
            candidate_samples = _RunReport.model_validate(raw).metrics
            if len(baseline_samples) > 1000:
                raise HtmlReportError("Baseline sample count exceeds 1000")
            supplemental += "<section><h2>Saved soak comparison</h2>"
            for metric in METRICS:
                supplemental += _chart(candidate_samples, metric, baseline_samples)
            supplemental += _details("Baseline source samples", baseline_samples) + "</section>"
        for label, value in (
            ("Attached comparison", comparison),
            ("Attached failure analysis", analysis),
            ("Attached visual comparison", visual),
        ):
            if value is not None:
                supplemental += f"<section><h2>{label}</h2>{_status(value.get('status'))}"
                supplemental += '<p class="muted">Separately supplied result; its own provenance and compatibility rules apply.</p>'
                supplemental += _comparison_table(value) + _analysis_table(value)
                if value is visual and isinstance(value.get("artifacts"), list):
                    attachments.root = visual_root
                    for item in value["artifacts"][:2]:
                        if isinstance(item, dict):
                            supplemental += attachments.image(item)
                supplemental += _details("Full saved result and provenance", value) + "</section>"
        html = (
            '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
            "img-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'\">"
            f"<title>{escape(title)} | Play Scenario report</title><style>{STYLE}</style></head>"
            '<body><header><p class="eyebrow">Unity MCP / Saved execution evidence</p>'
            f"<h1>{escape(title)}</h1><p>Play Scenario report</p></header><main>"
            '<nav aria-label="Report sections"><a href="#execution">Execution</a>'
            '<a href="#attachments">Attached results</a></nav>'
            f'<div id="execution">{content}</div><div id="attachments">{supplemental or "<p>No supplementary results supplied.</p>"}</div>'
            "</main><footer>Generated from saved evidence. Exporting does not rerun the scenario "
            "or change any recorded outcome. Missing evidence remains unavailable.</footer></body></html>\n"
        )
        if len(html.encode("utf-8")) > OUTPUT_LIMIT:
            raise HtmlReportError("Rendered report exceeds the 24 MiB output limit")
        return html
    except (ValidationError, TypeError, OverflowError, RecursionError) as exc:
        raise HtmlReportError(f"Invalid saved report: {exc}") from exc


def _read(path: Path) -> dict[str, JsonValue]:
    unlinked(path.absolute())
    raw = read_soak_json(path, INPUT_LIMIT)
    pending = [raw]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, float) and not math.isfinite(value):
            raise HtmlReportError("Saved input contains a non-finite JSON number")
    return raw


def _soak_samples(directory: Path) -> list[dict[str, JsonValue]]:
    """Use the admitted full journal rather than the final one-iteration run report."""
    saved = read_saved_soak(directory)
    samples: list[dict[str, JsonValue]] = []
    for row in saved.journal.rows:
        if row.metrics:
            samples.extend(sample.model_dump(mode="json") for sample in row.metrics)
        else:
            samples.append({"iteration": row.iteration})
    if len(samples) > 1000:
        raise HtmlReportError("Saved soak has more than 1000 chart samples")
    return samples


def export_html(
    source: Path,
    output: Path,
    *,
    comparison: Path | None = None,
    analysis: Path | None = None,
    visual: Path | None = None,
    baseline: Path | None = None,
) -> dict[str, JsonValue]:
    """Write one new self-contained report from explicit inputs, preserving source artifacts."""
    try:
        unlinked(source.absolute())
        source_is_directory = source.is_dir()
        if baseline is not None and (not source_is_directory or comparison is not None):
            raise HtmlReportError(
                "--baseline requires a saved soak directory and cannot combine with --comparison"
            )
        raw = _read(source / "run.json" if source_is_directory else source)
        baseline_samples = None
        comparison_value = _read(comparison) if comparison is not None else None
        if source_is_directory:
            raw = {**raw, "metrics": _soak_samples(source)}
        if baseline is not None:
            unlinked(baseline.absolute())
            baseline_samples = _soak_samples(baseline)
            comparison_value = compare_soak_metrics(baseline, source).to_dict()
            if comparison_value.get("status") != "compared":
                raise HtmlReportError("Saved soak histories are not comparable")
        html = render_html(
            raw,
            artifact_root=source.absolute() if source_is_directory else source.absolute().parent,
            comparison=comparison_value,
            analysis=_read(analysis) if analysis is not None else None,
            visual=_read(visual) if visual is not None else None,
            visual_root=visual.absolute().parent if visual is not None else None,
            baseline_samples=baseline_samples,
        )
        unlinked(output.absolute().parent)
        with output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(html)
        return {"status": "exported", "output": str(output), "bytes": len(html.encode("utf-8"))}
    except (OSError, ValueError, RuntimeError, RecursionError) as exc:
        if isinstance(exc, HtmlReportError):
            raise
        raise HtmlReportError(f"Cannot export HTML report: {exc}") from exc
