"""Actual offline CLI gate outcomes and process/write exclusion contracts."""

import json

from click.testing import CliRunner
from cli.main import cli
import pytest

from .play_scenario_soak_budget_fixtures import _known_evidence, _policy, ENVIRONMENT
from .play_scenario_soak_compare_fixtures import _change, _change_reproduction, _evidence


@pytest.mark.parametrize(
    "limit,net_limit,expected,status",
    [(10, 20, 0, "within_budget"), (9, 20, 1, "budget_exceeded"), (10, 19, 1, "budget_exceeded")],
)
def test_real_cli_gate_keeps_original_evidence_and_never_launches_or_queries(
    tmp_path, monkeypatch, limit, net_limit, expected, status
):
    # Given: saved successful histories and an explicit slope threshold.
    baseline = _known_evidence(tmp_path / "left", [0, 100, 110, 120])
    candidate = _known_evidence(tmp_path / "right", [0, 200, 220, 240])
    policy = _policy(tmp_path / "policy.json", slope=limit, net=net_limit)
    before = {
        str(path): path.read_bytes()
        for directory in (baseline, candidate)
        for path in directory.iterdir()
    }

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Offline gate must not launch or query")

    monkeypatch.setattr("subprocess.Popen", forbidden)
    monkeypatch.setattr("cli.commands.play_scenario.run_command", forbidden)
    # When: the public command performs its actual saved-evidence computation.
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            "play-scenario",
            "check-soak-budget",
            str(baseline),
            str(candidate),
            "--policy",
            str(policy),
        ],
    )
    # Then: gate exit/status is truthful and no original artifact is modified.
    assert result.exit_code == expected, result.output
    raw = json.loads(result.stdout)
    assert raw["status"] == status and raw["leak_verdict"] is False
    assert before == {
        str(path): path.read_bytes()
        for directory in (baseline, candidate)
        for path in directory.iterdir()
    }


@pytest.mark.parametrize(
    "damage",
    [
        "legacy",
        "unknown",
        "mismatch",
        "platform",
        "mode",
        "architecture",
        "receipt",
        "missing_metric",
        "policy",
        "decimal_exception",
    ],
)
def test_gate_unverified_or_insufficient_inputs_are_structured_not_comparable(tmp_path, damage):
    # Given: an input that cannot establish supported comparable regression evidence.
    baseline = _known_evidence(tmp_path / "left", [0, 1, 2])
    candidate = _known_evidence(tmp_path / "right", [0, 1, 2])
    policy = _policy(tmp_path / "policy.json")
    if damage == "legacy":
        candidate = _evidence(tmp_path / "legacy", [0, 1, 2])
    elif damage in {"unknown", "mismatch", "platform", "mode", "architecture"}:
        changes = {
            "unknown": {"runtime_version": None},
            "mismatch": {"runtime_version": "other"},
            "platform": {"platform": "Android"},
            "mode": {"build_mode": "editor"},
            "architecture": {"architecture": "unknown"},
        }[damage]
        for directory in (
            (baseline, candidate)
            if damage in {"platform", "mode", "architecture"}
            else (candidate,)
        ):
            _change_reproduction(directory, environment={**ENVIRONMENT, **changes})
    elif damage == "receipt":
        _change(candidate, "process.json", forced_termination=True)
    elif damage == "missing_metric":
        candidate = _known_evidence(tmp_path / "missing", [0, None, None])
    elif damage == "decimal_exception":
        policy.write_text(
            policy.read_text().replace(
                '"max_slope_increase_per_iteration": 10',
                '"max_slope_increase_per_iteration": 1e999999999999999999999999999999',
            )
        )
    else:
        policy.write_text('{"schema_version":1,"schema_version":1}')
    # When: the real CLI gate receives the evidence/policy paths.
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            "play-scenario",
            "check-soak-budget",
            str(baseline),
            str(candidate),
            "--policy",
            str(policy),
        ],
    )
    # Then: invalid/unknown/insufficient conditions never return a passing exit code.
    assert result.exit_code == 2, result.output
    assert json.loads(result.stdout)["status"] == "not_comparable"


@pytest.mark.parametrize("output_format", ["text", "table"])
def test_human_budget_output_includes_actual_limits_and_reasons(tmp_path, output_format):
    # Given: a candidate exceeding only the selected slope budget.
    baseline = _known_evidence(tmp_path / "left", [0, 100, 110, 120])
    candidate = _known_evidence(tmp_path / "right", [0, 200, 220, 240])
    policy = _policy(tmp_path / "policy.json", slope=9)
    # When: human output is selected through the public CLI.
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            output_format,
            "play-scenario",
            "check-soak-budget",
            str(baseline),
            str(candidate),
            "--policy",
            str(policy),
        ],
    )
    # Then: it preserves the reason and exact slope limit fields without list truncation.
    assert result.exit_code == 1, result.output
    assert (
        "slope_increase_exceeded" in result.stdout
        and "max_slope_increase_per_iteration" in result.stdout
    )
