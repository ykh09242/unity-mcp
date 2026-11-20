"""Synthetic same-head controls for the prospective native archive contract."""

import sys
from pathlib import Path

import pytest
from services.verification.input_chunks import source_revision
from tests.test_verification_pr_replay import LATE, PLAN, put, receipt, replay_fixture

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import prospective_nunit as native


def test_frozen_native_plans_compare_independent_results_and_preserve_missing_full(tmp_path):
    archive = replay_fixture(tmp_path)
    row = {
        **archive["prs"][0],
        "schema": "prospective_nunit_inputs@1",
        "budget_ms": 10000,
        "manual_source": "fixture-human-receipt",
    }
    results = {r: {**row.pop(r), "started_at": LATE} for r in native.ROLES}
    inputs = put(tmp_path, "inputs.json", row)
    plan, *_ = native.prepare(inputs, PLAN)
    assert native.assess(inputs, plan)["state"] == "incomplete"
    inputs = put(tmp_path, "inputs.json", {**row, **results})
    report = native.assess(inputs, plan)
    assert report["state"] == "paired"
    assert report["additional_full_failures_caught_by_automatic"] == report["full_failures"]
    assert report["comparisons"]["manual"]["measured_seconds_saved"] == 32
    results["full"]["started_at"] = PLAN
    inputs = put(tmp_path, "inputs.json", {**row, **results})
    assert native.assess(inputs, plan)["full_failures"] is None
    changed = {**plan, "manual_source": "later human"}
    with pytest.raises(ValueError, match="Frozen"):
        native.assess(inputs, changed)


def test_three_unrelated_native_runs_never_prove_candidate_bytes(tmp_path):
    row = replay_fixture(tmp_path)["prs"][0]
    results = {r: {**row.pop(r), "started_at": LATE} for r in native.ROLES}
    draft_path = tmp_path / row["draft"]["path"]
    draft = native.read_json(draft_path)
    inputs = {"Assets/Unobserved.asset": "1" * 64}
    draft["candidate"].update(inputs=inputs, source_revision=source_revision(inputs))
    row.update(
        draft=receipt(put(tmp_path, draft_path.name, draft)),
        closures=[],
        schema="prospective_nunit_inputs@1",
        budget_ms=10000,
        manual_source="operator",
    )
    path = put(tmp_path, "inputs.json", row)
    plan = native.prepare(path, PLAN)[0]
    report = native.assess(put(tmp_path, "inputs.json", {**row, **results}), plan)
    assert report["state"] == "incomplete" and not report["executions"]
    assert all("lacks declared" in reason for reason in report["gaps"].values())
