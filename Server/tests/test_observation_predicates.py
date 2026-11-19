"""One wire receipt and one public declaration determine whether a fact was observed."""

import pytest

from services.registry.action_catalog import (
    COMPILE_JOB_OBSERVATION,
    EDITOR_STATE_OBSERVATION,
    POINTER_ORDER_OBSERVATION,
)
from services.registry.observation_predicates import evaluate_observation
from tests.test_compile_job_contracts import native_job
from tests.test_ugui_raycast_diagnostics import sample


def test_snapshot_reports_false_and_unsaved_path_only_while_fresh():
    values = EDITOR_STATE_OBSERVATION["values"]
    data = {
        "staleness": {"is_stale": False},
        "editor": {"active_scene": {"path": ""}},
        "compilation": {"is_compiling": False},
    }
    reply = {"success": True, "data": data}
    assert evaluate_observation(EDITOR_STATE_OBSERVATION, reply, values) == {
        "active_scene.path": True,
        "active_scene.name": False,
        "compilation.is_compiling": True,
    }
    for stale in (True, None, 0, "false"):
        data["staleness"]["is_stale"] = stale
        assert not any(evaluate_observation(EDITOR_STATE_OBSERVATION, reply, values).values())
    legacy = {key: val for key, val in EDITOR_STATE_OBSERVATION.items() if key != "predicates"}
    assert not any(evaluate_observation(legacy, reply, values).values())


def test_observed_empty_order_requires_attempt_policy_and_consistent_totals():
    value = "pointer_hit_order"
    reply = sample()
    data = reply["data"]
    data["policy"]["strict_read"] = True

    def observed():
        return evaluate_observation(POINTER_ORDER_OBSERVATION, reply, [value])[value]

    assert observed()
    data["hits"][0]["rank"] = False
    assert not observed()
    data.update(hits=[], hits_total=0, hits_truncated=False)
    assert observed()
    data["hits_total"] = 1
    assert not observed()
    data["hits_total"] = 0
    for key in (
        "strict_read",
        "native_query_attempted",
        "user_code_may_have_run",
        "package_explicit_event_dispatch",
        "package_explicit_source_edits",
        "allow_custom_filters",
        "custom_filters_present",
    ):
        data["policy"][key] = not data["policy"][key]
        assert not observed()
        data["policy"][key] = not data["policy"][key]
    data["status"] = "unknown"
    assert not observed()


@pytest.mark.parametrize(
    "change",
    [
        {"state": "running"},
        {"collection_complete": False},
        {"outcome": "unknown"},
        {"attribution": "ambiguous"},
        {"candidate_generations": [True]},
        {"generation": None},
        {"compile_id": "wrong"},
        {"finished_at_unix_ms": 0},
    ],
)
def test_compile_job_requires_finished_attributed_evidence(change):
    value = "compile_job.outcome"
    reply = {"success": True, "data": native_job("a" * 32, "failed")}
    assert evaluate_observation(COMPILE_JOB_OBSERVATION, reply, [value])[value]
    reply["data"].update(change)
    assert not evaluate_observation(COMPILE_JOB_OBSERVATION, reply, [value])[value]
