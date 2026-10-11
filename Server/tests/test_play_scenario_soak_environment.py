"""Native/Python UTF-16 metadata admission agrees before any budget verdict."""

import json

from click.testing import CliRunner
from cli.main import cli
from pydantic import ValidationError
import pytest

from cli.utils.play_scenario_soak_compare import SoakComparisonError
from cli.utils.play_scenario_soak_environment import RecordedEnvironment
from cli.utils.play_scenario_soak_budget import check_soak_budget
from .play_scenario_soak_budget_fixtures import ENVIRONMENT, _known_evidence, _policy
from .play_scenario_soak_compare_fixtures import _change_reproduction


@pytest.mark.parametrize(
    "field,limit",
    [("operating_system", 256), ("platform", 64), ("architecture", 32), ("runtime_version", 64)],
)
def test_metadata_limit_counts_native_utf16_units_with_non_bmp_scalars(field, limit):
    # Given: the final scalar occupies two native UTF-16 code units.
    valid = {**ENVIRONMENT, field: "x" * (limit - 2) + "\U0001f680"}
    invalid = {**ENVIRONMENT, field: "x" * (limit - 1) + "\U0001f680"}
    # When/Then: the exact native boundary is accepted and one extra unit rejected.
    assert getattr(RecordedEnvironment.model_validate(valid), field) == valid[field]
    with pytest.raises(ValidationError):
        RecordedEnvironment.model_validate(invalid)


@pytest.mark.parametrize("value", ["Test\u0085OS", "Test\u009fOS", "Test\ud800OS", "Test\udfffOS"])
def test_metadata_rejects_native_controls_and_malformed_surrogates(value):
    # Given: a native-control character or malformed UTF-16 sequence in a claimed fact.
    # When/Then: typed admission does not accept it as known environment evidence.
    with pytest.raises(ValidationError):
        RecordedEnvironment.model_validate({**ENVIRONMENT, "operating_system": value})


@pytest.mark.parametrize("value", ["x" * 255 + "\U0001f680", "Test\u0085OS", "Test\ud800OS"])
def test_invalid_native_metadata_never_passes_budget_api(tmp_path, value):
    # Given: matching claimed snapshots that native capture would have converted to null.
    baseline = _known_evidence(tmp_path / "left", [0, 1, 2])
    candidate = _known_evidence(tmp_path / "right", [0, 1, 2])
    for directory in (baseline, candidate):
        _change_reproduction(directory, environment={**ENVIRONMENT, "operating_system": value})
    policy = _policy(tmp_path / "policy.json")
    # When/Then: matching malformed facts cannot prove a supported budget environment.
    with pytest.raises(SoakComparisonError):
        check_soak_budget(baseline, candidate, policy)


@pytest.mark.parametrize("value", ["x" * 255 + "\U0001f680", "Test\u0085OS", "Test\ud800OS"])
def test_invalid_native_metadata_returns_structured_cli_exit_two(tmp_path, value):
    # Given: otherwise complete matching successful evidence with invalid metadata.
    baseline = _known_evidence(tmp_path / "left", [0, 1, 2])
    candidate = _known_evidence(tmp_path / "right", [0, 1, 2])
    for directory in (baseline, candidate):
        _change_reproduction(directory, environment={**ENVIRONMENT, "operating_system": value})
    policy = _policy(tmp_path / "policy.json")
    # When: the actual CLI admits both artifact directories.
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
    # Then: there is no passing budget verdict or unhandled traceback.
    assert result.exit_code == 2, result.output
    assert json.loads(result.stdout)["status"] == "not_comparable"
