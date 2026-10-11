"""Selected regression gates use real saved evidence and preserve unavailable decisions."""

import json
import socket
import subprocess
from xml.etree import ElementTree

import pytest

from cli.utils import play_scenario_regression as regression
from cli.utils.play_scenario_player import PlayerOutcome
from cli.utils.play_scenario_soak_reports import ClientSoakSummary, SoakOutcome
from cli.utils.play_scenario_soak_baselines import register_baseline, set_baseline_pin
from cli.utils.play_scenario_soak_compare_evidence import read_saved_soak
from .play_scenario_soak_budget_fixtures import _known_evidence, _policy
from .play_scenario_soak_compare_fixtures import _change_reproduction
from .test_play_scenario_flakiness import report
from .test_play_scenario_ui import snapshot


@pytest.fixture(autouse=True)
def offline_boundaries(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Regression gate test attempted network or native execution")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)


def _ordinary_player(options):
    """Replace only the unavailable external Player; keep artifact export and gates real."""
    directory = options.output_directory / "child"
    directory.mkdir(parents=True)
    value = report(execution_environment="player", scenario={"name": "Smoke"})
    value.update(started_unix_ms=100, finished_unix_ms=120)
    return PlayerOutcome(value, directory, 0)


@pytest.mark.parametrize(
    "complete,text,expected,exit_code",
    [(True, "Ready", "failed", 1), (False, None, "unavailable", 2)],
)
def test_saved_ui_gate_preserves_changed_and_unavailable_decisions(
    tmp_path, monkeypatch, complete, text, expected, exit_code
):
    # Given compatible saved UI snapshots or one incomplete candidate and a successful Player.
    before, after = snapshot(), snapshot()
    after["complete"] = complete
    after["nodes"][0]["text"] = text
    for filename, value in (("before.json", before), ("after.json", after)):
        (tmp_path / filename).write_text(json.dumps(value), encoding="utf-8")
    source = tmp_path / "profile.json"
    source.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "name": "UI gate",
                "execution": {"build_directory": "build"},
                "analyze": False,
                "ui_checks": [{"baseline": "before.json", "candidate": "after.json"}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(regression, "run_player", _ordinary_player)
    # When the real regression runner compares the explicitly selected saved snapshots.
    result = regression.run_regression(source, tmp_path / "results")
    # Then a successful launch cannot erase either a changed UI or unavailable evidence.
    gates = {gate.name: gate for gate in result.gates}
    assert result.execution_exit_code == 0 and gates["execution"].status == "passed"
    assert result.exit_code == exit_code and gates["saved-ui-0"].status == expected
    assert gates["native-report"].status == "passed"
    stored = json.loads((tmp_path / "results/regression.json").read_text())
    assert stored["exit_code"] == exit_code
    junit = ElementTree.parse(tmp_path / "results/junit.xml").getroot()
    assert int(junit.get("failures")) == int(expected == "failed")
    assert int(junit.get("errors")) == int(expected == "unavailable")


def _pinned_soak_profile(tmp_path):
    baseline = _known_evidence(tmp_path / "baseline", [0, 100, 110, 120])
    registry = tmp_path / "registry.json"
    register_baseline(registry, "stable", baseline)
    set_baseline_pin(registry, "stable", (True, "Reviewed regression reference"))
    policy = _policy(tmp_path / "policy.json", net=0, slope=0)
    source = tmp_path / "profile.json"
    source.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "name": "Pinned soak gate",
                "execution": {
                    "kind": "soak",
                    "build_directory": "build",
                    "soak": {
                        "iterations": 4,
                        "max_runtime_seconds": 3600,
                        "failure_policy": "stop",
                        "retain_reports": 1,
                    },
                },
                "baseline": {"registry": registry.name, "name": "stable", "policy": policy.name},
            }
        ),
        encoding="utf-8",
    )
    return source, baseline, registry


@pytest.mark.parametrize(
    "values,expected,exit_code",
    [([0, 100, 110, 120], "passed", 0), ([0, 100, 150, 200], "failed", 1)],
)
def test_pinned_soak_budget_gate_uses_real_comparator_without_repinning(
    tmp_path, monkeypatch, values, expected, exit_code
):
    # Given an explicit reviewed reference and a zero-increase policy.
    source, baseline, registry = _pinned_soak_profile(tmp_path)
    original_registry = registry.read_bytes()
    original_baseline = {path.name: path.read_bytes() for path in baseline.iterdir()}

    def soak_player(options):
        options.output_directory.mkdir(parents=True)
        directory = _known_evidence(options.output_directory / "child", values)
        summary = ClientSoakSummary.model_validate(
            json.loads((directory / "client-soak.json").read_text())
        )
        return SoakOutcome(summary, directory, 0)

    monkeypatch.setattr(regression, "run_player_soak", soak_player)
    # When real admission and budget comparison run after the external Player boundary.
    result = regression.run_regression(source, tmp_path / "results")
    # Then the selected budget controls the overall result while the reference stays pinned.
    gate = next(gate for gate in result.gates if gate.name == "soak-budget")
    assert result.execution_exit_code == 0
    assert result.exit_code == exit_code and gate.status == expected, result.model_dump(mode="json")
    assert gate.detail["baseline_name"] == "stable"
    assert (tmp_path / "results/budget-policy.json").is_file()
    assert registry.read_bytes() == original_registry
    assert {path.name: path.read_bytes() for path in baseline.iterdir()} == original_baseline


def test_pinned_reference_mutation_during_execution_makes_budget_unavailable(tmp_path, monkeypatch):
    # Given an admitted pin whose otherwise valid evidence changes while the Player runs.
    source, baseline, registry = _pinned_soak_profile(tmp_path)
    original_registry = registry.read_bytes()

    def soak_player(options):
        options.output_directory.mkdir(parents=True)
        directory = _known_evidence(options.output_directory / "child", [0, 100, 110, 120])
        _change_reproduction(baseline, extra_diagnostic="changed during execution")
        # Admission remains valid; the immutable registered evidence digest must detect the edit.
        read_saved_soak(baseline)
        summary = ClientSoakSummary.model_validate(
            json.loads((directory / "client-soak.json").read_text())
        )
        return SoakOutcome(summary, directory, 0)

    monkeypatch.setattr(regression, "run_player_soak", soak_player)
    # When the post-execution gate revalidates the explicitly selected reference.
    result = regression.run_regression(source, tmp_path / "results")
    # Then a passing metric sample cannot authorize changed reference evidence or repin it.
    gate = next(gate for gate in result.gates if gate.name == "soak-budget")
    assert result.execution_exit_code == 0 and result.exit_code == 2
    assert gate.status == "unavailable" and "changed" in gate.detail["error"]
    assert registry.read_bytes() == original_registry
