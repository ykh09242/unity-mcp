"""Regression profiles preserve native outcomes and make every selected gate explicit."""

import json
from xml.etree import ElementTree

import pytest
from pydantic import ValidationError

from cli.utils.play_scenario_regression_profile import RegressionProfile, load_profile


def profile_value(**execution):
    return {
        "schema_version": 1,
        "name": "Smoke",
        "execution": {"build_directory": "build", **execution},
    }


@pytest.mark.parametrize(
    "execution",
    [
        {"timeout_seconds": True},
        {"graphical": "false"},
        {"unknown": 1},
        {"kind": "run", "iterations": 2},
        {"kind": "soak"},
        {"kind": "session", "repeat_count": 1},
        {"soak": {"iterations": 2}},
    ],
)
def test_profile_rejects_ignored_or_invalid_execution_options(execution):
    # Given / When / Then: malformed options cannot reach a launcher.
    with pytest.raises(ValidationError):
        RegressionProfile.model_validate(profile_value(**execution))


def test_profile_rejects_baseline_for_ordinary_run():
    # Given
    value = profile_value()
    value["baseline"] = {"registry": "registry.json", "name": "main", "policy": "policy.json"}
    # When / Then
    with pytest.raises(ValidationError):
        RegressionProfile.model_validate(value)


def test_profile_rejects_duplicate_json_keys(tmp_path):
    # Given
    source = tmp_path / "profile.json"
    source.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    # When / Then
    with pytest.raises(ValueError):
        load_profile(source)


@pytest.mark.parametrize("version", [True, 1.0, "1", 2])
def test_profile_version_requires_exact_supported_integer(version):
    # Given
    value = {**profile_value(), "schema_version": version}
    # When / Then
    with pytest.raises(ValidationError):
        RegressionProfile.model_validate(value)


def test_unavailable_launch_keeps_machine_and_human_reports(tmp_path, monkeypatch):
    # Given
    from cli.utils import play_scenario_regression as regression

    source = tmp_path / "profile.json"
    source.write_text(json.dumps(profile_value()), encoding="utf-8")
    (tmp_path / "build").mkdir()

    def unavailable(_options):
        raise regression.PlayerLaunchError("No verified executable")

    monkeypatch.setattr(regression, "run_player", unavailable)
    # When
    result = regression.run_regression(source, tmp_path / "results")
    # Then
    assert result.exit_code == 2
    assert result.gates[0].status == "unavailable"
    assert "No verified executable" in (tmp_path / "results/report.html").read_text()
    assert (tmp_path / "results/junit.xml").is_file()
    assert json.loads((tmp_path / "results/regression.json").read_text())["exit_code"] == 2
    assert load_profile(tmp_path / "results/profile.json") == load_profile(source)


def test_existing_output_is_never_overwritten(tmp_path):
    # Given
    from cli.utils.play_scenario_regression import run_regression

    source = tmp_path / "profile.json"
    source.write_text(json.dumps(profile_value()), encoding="utf-8")
    output = tmp_path / "existing"
    output.mkdir()
    marker = output / "marker"
    marker.write_text("preserved")
    # When / Then
    with pytest.raises((FileExistsError, ValueError)):
        run_regression(source, output)
    assert marker.read_text() == "preserved"


@pytest.mark.parametrize("native_exit,expected", [(0, 0), (1, 1), (130, 130)])
def test_native_outcome_survives_successful_analysis(tmp_path, monkeypatch, native_exit, expected):
    # Given: replace only the external Player boundary; use real artifact writers and analysis.
    from cli.utils import play_scenario_regression as regression
    from cli.utils.play_scenario_player import PlayerOutcome

    source = tmp_path / "profile.json"
    source.write_text(json.dumps(profile_value()), encoding="utf-8")
    captured = []

    def player(options):
        captured.append(options)
        directory = options.output_directory / "child"
        directory.mkdir(parents=True)
        state = "passed" if native_exit == 0 else "cancelled" if native_exit == 130 else "failed"
        value = {
            "job_id": "run-a",
            "scenario": {"name": "Smoke"},
            "repeat_count": 1,
            "started_unix_ms": 100,
            "finished_unix_ms": 120,
            "status": "succeeded"
            if native_exit == 0
            else "cancelled"
            if native_exit == 130
            else "failed",
            "execution_environment": "player",
            "steps": [],
            "reproduction": {
                "definition_hash": "a" * 64,
                "unity_version": "6000.0",
                "package_version": "1.0",
                "environment": {
                    "schema_version": 1,
                    "operating_system": "Windows 11",
                    "platform": "WindowsPlayer",
                    "architecture": "x64",
                    "scripting_backend": "mono",
                    "build_mode": "development",
                    "runtime_version": "6.0",
                },
            },
            "iteration_results_version": 1,
            "iteration_results": [
                {"iteration": 1, "status": state, "started_unix_ms": 100, "finished_unix_ms": 120}
            ],
        }
        return PlayerOutcome(value, directory, native_exit)

    monkeypatch.setattr(regression, "run_player", player)
    # When
    result = regression.run_regression(source, tmp_path / "results")
    # Then
    assert result.exit_code == expected, result
    assert result.execution_exit_code == native_exit
    assert captured[0].build_directory == tmp_path / "build"
    assert captured[0].require_verified_payload is True
    assert (tmp_path / "results/execution.html").is_file()
    xml = ElementTree.parse(tmp_path / "results/junit.xml")
    assert int(xml.getroot().get("failures")) == (1 if native_exit else 0)


def test_missing_pin_prevents_any_player_launch(tmp_path, monkeypatch):
    # Given
    from cli.utils import play_scenario_regression as regression

    source = tmp_path / "profile.json"
    value = profile_value(kind="soak", soak={"iterations": 2})
    value["baseline"] = {"registry": "missing.json", "name": "main", "policy": "policy.json"}
    source.write_text(json.dumps(value), encoding="utf-8")

    def forbidden(_options):
        pytest.fail("Invalid pin must be rejected before launch")

    monkeypatch.setattr(regression, "run_player_soak", forbidden)
    # When
    result = regression.run_regression(source, tmp_path / "results")
    # Then
    assert result.exit_code == 2
    assert result.execution_exit_code is None
    assert [gate.name for gate in result.gates] == ["execution", "soak-budget", "analysis"]
    assert all(gate.status == "unavailable" for gate in result.gates)


def test_profile_output_cannot_modify_build(tmp_path):
    # Given
    from cli.utils.play_scenario_regression import run_regression

    source = tmp_path / "profile.json"
    source.write_text(json.dumps(profile_value()), encoding="utf-8")
    # When / Then
    with pytest.raises(ValueError, match="immutable"):
        run_regression(source, tmp_path / "build/results")
    assert not (tmp_path / "build").exists()


def test_regression_cli_reports_invalid_profile_as_unavailable(tmp_path):
    # Given
    from click.testing import CliRunner
    from cli.commands.play_scenario_regression import regression_run

    source = tmp_path / "profile.json"
    source.write_text(json.dumps(profile_value(unknown=True)), encoding="utf-8")
    # When
    result = CliRunner().invoke(
        regression_run, [str(source), "--output-dir", str(tmp_path / "out")]
    )
    # Then
    assert result.exit_code == 2
    assert not (tmp_path / "out").exists()
