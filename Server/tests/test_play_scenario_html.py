"""Portable report behavior, including untrusted saved evidence."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from cli.utils.play_scenario_html import HtmlReportError, export_html, render_html


def report() -> dict:
    return {
        "job_id": "run-1",
        "status": "failed",
        "scenario": {"name": "menu-start"},
        "started_unix_ms": 1000,
        "finished_unix_ms": 1500,
        "error": "Button unavailable",
        "reproduction": {"definition_hash": "a" * 64, "source_revision": "abc123"},
        "steps": [
            {
                "stage": "main",
                "iteration": 1,
                "step_index": 0,
                "name": "Start",
                "status": "failed",
                "detail": "Button unavailable",
                "started_unix_ms": 1100,
                "finished_unix_ms": 1400,
                "performance": {"duration_ms": 300, "passed": False, "error": "Limit"},
            }
        ],
        "logs": [{"type": "Error", "message": "Button unavailable"}],
        "metrics": [
            {"iteration": 1, "managed_bytes": 100},
            {"iteration": 2, "managed_bytes": None},
            {"iteration": 3, "managed_bytes": 200},
        ],
    }


def test_html_preserves_failure_steps_logs_and_measurement_gaps():
    # Given a failed saved execution with a missing measurement.
    raw = report()
    # When it is rendered without a running Unity instance.
    html = render_html(raw)
    # Then the actual outcome and measurements remain inspectable.
    assert "menu-start" in html and "Button unavailable" in html
    assert "abc123" in html and "Performance" in html
    assert "Missing samples break the curve" in html
    assert 'data-samples="2"' in html
    assert "<script" not in html


def test_html_escapes_source_text_and_does_not_interpret_external_attachments():
    # Given markup and an external screenshot reference in untrusted evidence.
    raw = report()
    raw["error"] = '<script>alert("bad")</script>'
    raw["steps"][0]["screenshot"] = {
        "status": "captured",
        "path": "https://example.invalid/secret.png",
    }
    # When a report is rendered.
    html = render_html(raw)
    # Then source text stays text and no network image can be loaded.
    assert "&lt;script&gt;" in html
    assert 'src="https:' not in html and 'href="https:' not in html
    assert "Image unavailable" in html


def test_export_is_exclusive_and_keeps_source_bytes(tmp_path: Path):
    # Given explicit source and output paths.
    source = tmp_path / "run.json"
    payload = json.dumps(report()).encode()
    source.write_bytes(payload)
    output = tmp_path / "report.html"
    # When the source is exported.
    result = export_html(source, output)
    # Then only the requested new file is created, and reruns cannot overwrite it.
    assert result["output"] == str(output)
    assert output.read_text(encoding="utf-8").startswith("<!doctype html>")
    assert source.read_bytes() == payload
    with pytest.raises(HtmlReportError, match="exist"):
        export_html(source, output)


@pytest.mark.parametrize(
    "payload",
    [
        '{"status":"failed","status":"succeeded"}',
        '{"value":NaN}',
        "{}",
        '{"scenario":{"name":"menu"},"status":"succeeded","unknown_extra":1e400}',
        '{"scenario":{"name":"menu"},"status":"succeeded","unknown_extra":[{"value":-1e400}]}',
    ],
)
def test_invalid_saved_input_never_creates_a_report(tmp_path: Path, payload: str):
    # Given malformed or unsupported evidence.
    source = tmp_path / "bad.json"
    source.write_text(payload, encoding="utf-8")
    output = tmp_path / "report.html"
    # When it is exported, then it is rejected before output creation.
    with pytest.raises(HtmlReportError):
        export_html(source, output)
    assert not output.exists()


def test_suite_preserves_parent_infrastructure_failure():
    # Given a passed child and a failed final export.
    raw = {
        "suite": {"name": "smoke"},
        "status": "failed",
        "client_error": "Export failed",
        "scenarios": [{"name": "menu", "status": "succeeded", "report": report()}],
    }
    # When rendering the suite.
    html = render_html(raw)
    # Then the parent failure is visible independently of its children.
    assert "Export failed" in html
    assert "smoke" in html and "menu" in html


def test_cli_exports_report_with_separately_labeled_analysis(tmp_path: Path):
    # Given a registered real CLI command and an explicit supplementary result.
    from cli.commands.play_scenario_reporting import register_reporting_commands
    import click

    group = click.Group()
    register_reporting_commands(group)
    source = tmp_path / "run.json"
    source.write_text(json.dumps(report()), encoding="utf-8")
    analysis = tmp_path / "analysis.json"
    analysis.write_text('{"status":"analyzed","intermittent":true}', encoding="utf-8")
    output = tmp_path / "report.html"
    # When invoking the user-facing command.
    result = CliRunner().invoke(
        group, ["export-html", str(source), "--output", str(output), "--analysis", str(analysis)]
    )
    # Then the supplementary verdict does not replace the execution outcome.
    assert result.exit_code == 0, result.output
    html = output.read_text(encoding="utf-8")
    assert "Attached failure analysis" in html and "analyzed" in html


def test_verified_local_png_is_embedded_and_tampering_is_visible(tmp_path: Path):
    # Given a saved checkpoint with an exact fingerprint and dimensions.
    from hashlib import sha256
    from PIL import Image

    image = tmp_path / "checkpoint.png"
    Image.new("RGBA", (8, 4), "red").save(image)
    raw = report()
    raw["steps"][0]["screenshot"] = {
        "name": "menu",
        "status": "captured",
        "path": image.name,
        "sha256": sha256(image.read_bytes()).hexdigest(),
        "width": 8,
        "height": 4,
    }
    # When rendering the verified checkpoint.
    html = render_html(raw, artifact_root=tmp_path)
    # Then it remains portable without external file links.
    assert "data:image/png;base64," in html
    assert 'width="8" height="4"' in html
    # When bytes change after capture, the old fingerprint cannot admit the image.
    Image.new("RGBA", (8, 4), "blue").save(image)
    changed = render_html(raw, artifact_root=tmp_path)
    assert "data:image/png;base64," not in changed
    assert "Image unavailable" in changed


def test_image_outside_artifact_root_is_not_embedded(tmp_path: Path):
    # Given an otherwise valid image outside the source artifact folder.
    from hashlib import sha256
    from PIL import Image

    image = tmp_path / "outside.png"
    Image.new("RGBA", (2, 2), "red").save(image)
    root = tmp_path / "evidence"
    root.mkdir()
    raw = report()
    raw["steps"][0]["screenshot"] = {
        "path": str(image),
        "sha256": sha256(image.read_bytes()).hexdigest(),
        "width": 2,
        "height": 2,
    }
    # When exporting only the explicitly selected artifact directory.
    html = render_html(raw, artifact_root=root)
    # Then arbitrary local files cannot enter the shared report.
    assert "data:image/png;base64," not in html
    assert "Image unavailable" in html


def test_main_cli_exposes_offline_export_command():
    # Given the application's actual command group.
    from cli.commands.play_scenario import play_scenario

    # When requesting the offline export help.
    result = CliRunner().invoke(play_scenario, ["export-html", "--help"])
    # Then no server connection is needed to access the command.
    assert result.exit_code == 0
    assert "--comparison" in result.output


def test_parent_soak_timeout_cannot_be_hidden_by_successful_retained_iteration():
    # Given an overall timed-out soak with a successful last ordinary iteration.
    raw = report()
    raw.update(
        status="succeeded", soak={"status": "timed_out", "passed_iterations": 2}, exit_code=1
    )
    # When exporting the native run.json directly.
    html = render_html(raw)
    # Then the parent outcome is shown before the explicitly labeled iteration outcome.
    assert "timed_out" in html
    assert html.index("timed_out") < html.index("Retained iteration outcome")
    assert "Player exit code: 1" in html


def test_persistence_failure_is_visible_despite_recorded_execution_success():
    # Given successful execution followed by failed evidence persistence.
    raw = report()
    raw.update(status="succeeded", persistence_error="Journal write failed", exit_code=2)
    # When rendering saved diagnostics.
    html = render_html(raw)
    # Then the top-level outcome cannot be a green success.
    assert '<span class="status bad">failed</span>' in html
    assert "Journal write failed" in html


def test_comparative_curve_draws_both_histories_without_joining_missing_samples():
    # Given explicit aligned baseline/candidate measurements with a gap.
    raw = report()
    baseline = [
        {"iteration": 1, "managed_bytes": 90},
        {"iteration": 2},
        {"iteration": 3, "managed_bytes": 95},
    ]
    # When plotting the admitted histories.
    html = render_html(raw, baseline_samples=baseline)
    # Then both distinguishable series exist and each side of the gap is separate.
    assert "Saved soak comparison" in html
    assert 'data-samples="4"' in html
    assert html.count('class="chart-line baseline"') == 2
    assert "Blue: candidate; brown: baseline" in html


def test_direct_native_comparison_keeps_summary_values_visible():
    # Given the tuple sequence returned by the saved-soak comparison dataclass.
    from cli.utils.play_scenario_soak_compare import (
        MetricComparison,
        MetricStatistics,
        SampleCounts,
        SoakComparison,
    )

    baseline = MetricStatistics(10, 20, 10, 20, 10, 1.0)
    candidate = MetricStatistics(10, 30, 10, 30, 20, 2.0)
    counts = SampleCounts(3, 0, 0, 0, 0)
    metric = MetricComparison(
        "managed_bytes", "compared", 3, counts, counts, baseline, candidate, 1.0, 10, 1, 0, ()
    )
    comparison = SoakComparison("compared", {}, {}, [], (metric,)).to_dict()
    # When exporting the direct in-process result rather than reloading its JSON.
    html = render_html(report(), comparison=comparison)
    # Then the comparison table is visible as well as the expandable raw evidence.
    assert "managed_bytes / net_change</td><td>10</td><td>20</td><td>10</td>" in html
    assert "managed_bytes / slope_per_iteration</td><td>1.0</td><td>2.0</td><td>1.0</td>" in html
