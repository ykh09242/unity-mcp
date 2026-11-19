from argparse import Namespace

import pytest

from tools.experiments import blind_tool_observations as choices
from tools.experiments.blind_tool_answers import expected_facts
from tools.experiments.blind_tool_claims import score_claims
from tools.experiments.blind_tool_selection import LANGUAGES, task_prompt
from tools.experiments.blind_tool_trace import decision, observation


REFERENCE = "1" * 32
POINTER = {"task": "pointer-order", "target": "HUD", "point": [64, 32]}
ARGS = {"target": "HUD", "screen_position": [64, 32]}


def test_positive_tasks_are_role_neutral_and_available_in_each_language():
    for task in choices.TASKS:
        prompts = [
            task_prompt(task, language, "", {**POINTER, "compile_id": REFERENCE})
            for language in LANGUAGES
        ]
        assert len(set(prompts)) == 6
        assert all(
            name not in prompt
            for prompt in prompts
            for name in (choices.STATE, choices.JOB, choices.POINTER, "mcpforunity://")
        )
        if task == "compile-outcome":
            assert all(REFERENCE in prompt for prompt in prompts)
        if task == "pointer-order":
            assert all("HUD" in prompt and "64" in prompt and "32" in prompt for prompt in prompts)


def test_compile_current_false_is_measured_and_stale_or_absent_is_unknown():
    scope = {"task": "compile-current"}
    data = {"compilation": {"is_compiling": False}, "staleness": {"is_stale": False}}
    facts = observation(choices.STATE, data, scope)
    assert expected_facts("compile-current", [facts], [])[0] == {"compiling": False}
    for invalid in ({"compilation": {"is_compiling": 0}}, {"staleness": {"is_stale": True}}):
        assert not observation(choices.STATE, {**data, **invalid}, scope)


def test_only_a_submitted_existing_job_can_supply_an_attributed_outcome():
    scope = {"task": "compile-outcome", "compile_id": REFERENCE}
    data = {
        "compile_id": REFERENCE,
        "state": "finished",
        "outcome": "failed",
        "attribution": "observed_after_request",
        "generation": 4,
        "assembly_count": 2,
        "collection_complete": True,
        "session_id": "2" * 32,
        "candidate_generations": [4],
        "requested_at_unix_ms": 1,
        "started_at_unix_ms": 2,
        "finished_at_unix_ms": 3,
    }
    args = {"compile_id": REFERENCE}
    assert decision(choices.JOB, args, "", endpoint=True, fixture=scope) == "unity"
    for bad_args in ({"compile_id": "2" * 32}, {**args, "observe_source": True}, {}):
        assert decision(choices.JOB, bad_args, "", fixture=scope) == "blocked"
    assert decision(choices.JOB, args, "") == "blocked"
    assert observation(choices.JOB, data, scope) == {"outcome": "failed"}
    assert not observation(choices.JOB, {**data, "attribution": "ambiguous"}, scope)
    assert not observation(choices.JOB, {**data, "compile_id": "2" * 32}, scope)
    assert choices.fixture_availability(scope, {}) == "unverified"
    assert (
        choices.fixture_availability(
            scope,
            {
                "observation_fixtures": {
                    "compile-outcome": {"arguments": args, "reply": {"success": True, "data": data}}
                }
            },
        )
        == "positive"
    )
    result = score_claims(
        "compile-outcome",
        {"outcome": {"value": "failed", "source": "compile_job", "channel": "editor"}},
        [{"outcome": "failed"}],
        [],
    )
    assert result["status"] == "correct"


def test_pointer_needs_scoped_target_position_and_native_safe_order():
    assert decision(choices.POINTER, ARGS, "", endpoint=True, fixture=POINTER) == "unity"
    for bad in (
        {**ARGS, "target": "other"},
        {**ARGS, "screen_position": [65, 32]},
        {**ARGS, "extra": True},
    ):
        assert decision(choices.POINTER, bad, "", fixture=POINTER) == "blocked"
    assert decision("manage_ugui.diagnose_raycast", ARGS, "", fixture=POINTER) == "blocked"
    data = {
        "kind": "ugui_raycast_diagnostics",
        "status": "observed",
        "screen_position": [64, 32],
        "hits": [],
        "hits_total": 0,
        "hits_truncated": False,
        "policy": {
            "strict_read": True,
            "native_query_attempted": True,
            "allow_custom_filters": False,
            "custom_filters_present": False,
            "user_code_may_have_run": False,
            "package_explicit_event_dispatch": False,
            "package_explicit_source_edits": False,
        },
        "input": {"delivery": "not_attempted"},
    }
    facts = observation(choices.POINTER, data, POINTER)
    assert expected_facts("pointer-order", [facts], [])[0] == {
        "hit_status": "empty",
        "first_hit": None,
    }
    diagnostic = observation(
        choices.POINTER,
        {
            **data,
            "status": "unknown",
            "reason": "missing_event_system",
            "policy": {"native_query_attempted": False},
        },
        POINTER,
    )
    assert diagnostic == {
        "status": "unknown",
        "reason": "missing_event_system",
        "native_query_attempted": False,
    }
    assert expected_facts("pointer-order", [diagnostic], [])[0] == {}
    for invalid in (
        {"screen_position": [32, 32]},
        {"hits": [{"rank": 2}], "hits_total": 1},
    ):
        assert not observation(choices.POINTER, {**data, **invalid}, POINTER)


@pytest.mark.parametrize("task", ["compile-outcome", "pointer-order"])
def test_unavailable_fixture_is_recorded_before_any_prompt(task):
    fixture = choices.observation_fixture(Namespace(), task)
    assert choices.fixture_availability(fixture, {}) == choices.missing_fixture(fixture)
