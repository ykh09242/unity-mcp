"""Public offline comparison exit, output and no-launch contracts."""

import json
from click.testing import CliRunner
from cli.main import cli
import pytest

from .play_scenario_soak_compare_fixtures import _evidence


@pytest.mark.parametrize(
    "values,exit_code,status",
    [([0, 1, 2], 0, "compared"), ([0, None, None], 2, "insufficient_evidence")],
)
def test_registered_cli_exit_and_json_never_launch_or_modify_evidence(
    tmp_path, monkeypatch, values, exit_code, status
):
    baseline = _evidence(tmp_path / "left", values)
    candidate = _evidence(tmp_path / "right", values)
    before = {
        str(path): path.read_bytes()
        for directory in (baseline, candidate)
        for path in directory.iterdir()
    }

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Offline comparison must not launch, refresh or poll")

    monkeypatch.setattr("subprocess.Popen", forbidden)
    monkeypatch.setattr("cli.utils.connection.run_command", forbidden)
    monkeypatch.setattr("cli.commands.play_scenario.run_command", forbidden)
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            "play-scenario",
            "compare-soak-metrics",
            str(baseline),
            str(candidate),
        ],
    )
    assert result.exit_code == exit_code, result.output
    assert json.loads(result.stdout)["status"] == status
    assert before == {
        str(path): path.read_bytes()
        for directory in (baseline, candidate)
        for path in directory.iterdir()
    }


def test_registered_cli_missing_process_is_not_comparable_exit_two(tmp_path):
    baseline = _evidence(tmp_path / "left", [0, 1, 2])
    candidate = _evidence(tmp_path / "right", [0, 1, 2])
    (candidate / "process.json").unlink()
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            "play-scenario",
            "compare-soak-metrics",
            str(baseline),
            str(candidate),
        ],
    )
    assert result.exit_code == 2 and json.loads(result.stdout)["status"] == "not_comparable"


@pytest.mark.parametrize("output_format", ["text", "table"])
def test_human_output_preserves_actual_trends_without_list_truncation(tmp_path, output_format):
    # Given: two saved histories whose slopes and net changes differ.
    baseline = _evidence(tmp_path / "left", [0, 10, 20])
    candidate = _evidence(tmp_path / "right", [0, 20, 40])
    # When: the actual registered command formats human output.
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            output_format,
            "play-scenario",
            "compare-soak-metrics",
            str(baseline),
            str(candidate),
        ],
    )
    # Then: slope and observed net-change columns survive formatting for every metric.
    assert result.exit_code == 0, result.output
    assert all(
        metric in result.stdout for metric in ("managed_bytes", "allocated_bytes", "object_count")
    )
    assert all(
        field in result.stdout
        for field in ("baseline_slope", "candidate_slope", "net_change_delta")
    )
    assert "10.0" in result.stdout and "20.0" in result.stdout


def test_bounded_deep_json_produces_structured_not_comparable_exit_two(tmp_path):
    # Given: valid JSON exceeds parser recursion depth while staying below 64 KiB.
    baseline = _evidence(tmp_path / "left", [0, 1, 2])
    candidate = _evidence(tmp_path / "right", [0, 1, 2])
    (candidate / "request.json").write_bytes(
        b'{"nested":' + b"[" * 30000 + b"0" + b"]" * 30000 + b"}"
    )
    # When: the real registered command admits the candidate evidence.
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            "play-scenario",
            "compare-soak-metrics",
            str(baseline),
            str(candidate),
        ],
    )
    # Then: deep JSON remains a bounded structured admission error.
    assert result.exit_code == 2, result.output
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "not_comparable" and parsed["diagnostic_only"] is True
    assert len(parsed["error"]) <= 540
