"""Strict opt-in policy parsing prevents ambiguous or implicit budget success."""

import json

import pytest
from pydantic import ValidationError

from cli.utils.play_scenario_soak import SoakContractError
from cli.utils.play_scenario_soak_budget_policy import read_budget_policy
from .play_scenario_soak_budget_fixtures import _policy


@pytest.mark.parametrize(
    "field",
    [
        "max_net_change_increase",
        "max_slope_increase_per_iteration",
        "min_paired_samples",
        "min_consecutive_samples",
    ],
)
def test_each_selected_metric_requires_every_explicit_limit_and_minimum(tmp_path, field):
    # Given: one selected metric whose policy omits a required field.
    path = _policy(tmp_path / "policy.json")
    raw = json.loads(path.read_text())
    del raw["metrics"]["managed_bytes"][field]
    path.write_text(json.dumps(raw))
    # When/Then: no policy default can invent a passing limit.
    with pytest.raises(ValidationError):
        read_budget_policy(path)


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": True},
        {"schema_version": 2},
        {"extra": True},
        {"metrics": {}},
        {"metrics": {"rss_bytes": {}}},
    ],
)
def test_unknown_schema_fields_or_metric_names_are_rejected(tmp_path, changes):
    # Given: policy metadata outside the explicit schema or channel subset.
    path = _policy(tmp_path / "policy.json")
    raw = json.loads(path.read_text())
    raw.update(changes)
    path.write_text(json.dumps(raw))
    # When/Then: unsupported policy interpretation fails admission.
    with pytest.raises(ValidationError):
        read_budget_policy(path)


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_net_change_increase", True),
        ("max_net_change_increase", -1),
        ("max_net_change_increase", 2**63),
        ("min_paired_samples", 1),
        ("min_paired_samples", 1001),
        ("min_paired_samples", 2.0),
        ("min_consecutive_samples", 1),
        ("min_consecutive_samples", 1001),
        ("max_slope_increase_per_iteration", True),
        ("max_slope_increase_per_iteration", "0"),
        ("max_slope_increase_per_iteration", -1),
        ("max_slope_increase_per_iteration", 2**63),
    ],
)
def test_policy_numbers_preserve_types_and_bounds(tmp_path, field, value):
    # Given: numeric values outside the selected field's strict domain.
    path = _policy(tmp_path / "policy.json")
    raw = json.loads(path.read_text())
    raw["metrics"]["managed_bytes"][field] = value
    path.write_text(json.dumps(raw))
    # When/Then: coercion cannot weaken numeric admission.
    with pytest.raises((ValidationError, SoakContractError)):
        read_budget_policy(path)


@pytest.mark.parametrize(
    "token",
    [
        "NaN",
        "Infinity",
        "-Infinity",
        "1e309",
        "0e999999",
        "1e-19",
        "0." + "1" * 65,
        "1e999999999999999999999999999999",
    ],
)
def test_nonfinite_overflow_and_expansion_tokens_are_bounded(tmp_path, token):
    # Given: a numeric token that could produce unavailable or oversized arithmetic.
    path = _policy(tmp_path / "policy.json")
    path.write_text(
        path.read_text().replace(
            '"max_slope_increase_per_iteration": 10', f'"max_slope_increase_per_iteration": {token}'
        )
    )
    # When/Then: exact arithmetic is never constructed from this token.
    with pytest.raises((ValidationError, SoakContractError)):
        read_budget_policy(path)


def test_duplicate_policy_keys_are_never_last_value_wins(tmp_path):
    # Given: two competing explicit limits under the same JSON key.
    path = _policy(tmp_path / "policy.json")
    path.write_text(
        path.read_text().replace(
            '"max_net_change_increase": 20',
            '"max_net_change_increase": 20, "max_net_change_increase": 10',
        )
    )
    # When/Then: ambiguous limits are rejected.
    with pytest.raises(SoakContractError):
        read_budget_policy(path)


@pytest.mark.parametrize(
    "encoded", [b" " * 8193, b'{"nested":' + b"[" * 1000 + b"0" + b"]" * 1000 + b"}"]
)
def test_policy_byte_and_depth_limits_are_enforced_before_validation(tmp_path, encoded):
    # Given: oversized or deeply nested input rather than a bounded policy.
    path = tmp_path / "policy.json"
    path.write_bytes(encoded)
    # When/Then: parser size/depth cannot leak into a gate decision.
    with pytest.raises(SoakContractError):
        read_budget_policy(path)


def test_linked_policy_is_rejected_even_when_target_has_valid_limits(tmp_path):
    # Given: a redirected policy path whose target itself is otherwise valid.
    target = _policy(tmp_path / "target.json")
    path = tmp_path / "policy.json"
    try:
        path.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"Symlink unavailable on host: {exc}")
    # When/Then: the policy path cannot silently follow the link.
    with pytest.raises(SoakContractError):
        read_budget_policy(path)
