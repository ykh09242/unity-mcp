"""Synthetic parser fixtures; actual transport evidence comes from the native runner."""

import copy

import pytest

import play_scenario_mcp_sample as runner

from play_scenario_mcp_sample import validate_ready, validate_report

JOB = "a" * 32


def test_ready_requires_the_exact_loopback_project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    ready = {
        "host": "127.0.0.1",
        "port": 50001,
        "pid": 123,
        "project": str(project),
        "instance": "project@1234abcd",
    }
    assert validate_ready(ready, project) == "project@1234abcd"


@pytest.mark.parametrize(
    "field,value",
    [
        ("host", "0.0.0.0"),
        ("port", 0),
        ("port", 6400),
        ("port", 65536),
        ("pid", 0),
        ("project", "different-project"),
        ("instance", "unbound"),
    ],
)
def test_rejects_an_unowned_or_ambiguous_endpoint(tmp_path, field, value):
    project = tmp_path / "project"
    project.mkdir()
    ready = {
        "host": "127.0.0.1",
        "port": 50001,
        "pid": 123,
        "project": str(project),
        "instance": "project@1234abcd",
    }
    ready[field] = value
    with pytest.raises(ValueError):
        validate_ready(ready, project)


@pytest.fixture
def report():
    return {
        "job_id": JOB,
        "scenario": {"name": "sample-normal"},
        "reproduction": {"source_revision": "fresh-run"},
        "status": "succeeded",
        "phase": "finished",
        "runner_resources_released": True,
        "report_error": None,
        "report_path": "Library/MCPForUnity/PlayScenarioRuns/" + JOB + ".json",
        "repeat_count": 1,
        "iteration_results_version": 1,
        "iteration_results": [{"iteration": 1, "status": "passed"}],
        "resource_checks": [
            {
                "iteration": 1,
                "passed": True,
                "new_scriptable_objects": 0,
                "new_subscriptions": 0,
                "new_handles": 0,
                "retained_resources": [],
            }
        ],
        "cleanup_failures": [],
    }


def test_accepts_complete_clean_report(report):
    validate_report(
        report, JOB, "succeeded", 1, scenario="sample-normal", source_revision="fresh-run"
    )


@pytest.mark.parametrize(
    "damage",
    [
        "wrong_job",
        "wrong_status",
        "unfinished",
        "runner_retained",
        "write_error",
        "unsafe_report_path",
        "missing_iteration",
        "missing_resource_check",
        "unreported_handle",
        "cleanup_failure",
    ],
)
def test_rejects_incomplete_or_false_clean_evidence(report, damage):
    value = copy.deepcopy(report)
    if damage == "wrong_job":
        value["job_id"] = "b" * 32
    elif damage == "wrong_status":
        value["status"] = "failed"
    elif damage == "unfinished":
        value["phase"] = "cleanup"
    elif damage == "runner_retained":
        value["runner_resources_released"] = False
    elif damage == "write_error":
        value["report_error"] = "failed"
    elif damage == "unsafe_report_path":
        value["report_path"] = "../foreign.json"
    elif damage == "missing_iteration":
        value["iteration_results"].clear()
    elif damage == "missing_resource_check":
        value["resource_checks"].clear()
    elif damage == "unreported_handle":
        value["resource_checks"][0]["new_handles"] = 1
    else:
        value["cleanup_failures"] = [{"code": "unexpected"}]
    with pytest.raises(ValueError):
        validate_report(
            value, JOB, "succeeded", 1, scenario="sample-normal", source_revision="fresh-run"
        )


def test_retained_control_requires_three_distinct_identities(report):
    report["status"] = "failed"
    report["iteration_results"][0]["status"] = "failed"
    report["failure"] = {"code": "resource_assertion_failed"}
    report["cleanup_failures"] = [{"code": "resource_assertion_failed"}]
    check = report["resource_checks"][0]
    check.update(passed=False, new_scriptable_objects=1, new_subscriptions=1, new_handles=1)
    check["retained_resources"] = [
        {"id": index, "kind": kind, "owner": "lifecycle-session:" + "b" * 32}
        for index, kind in enumerate(("scriptable_object", "subscription", "handle"), 1)
    ]
    validate_report(
        report,
        JOB,
        "failed",
        1,
        retained=True,
        scenario="sample-normal",
        source_revision="fresh-run",
    )
    check["retained_resources"].pop()
    with pytest.raises(ValueError):
        validate_report(
            report,
            JOB,
            "failed",
            1,
            retained=True,
            scenario="sample-normal",
            source_revision="fresh-run",
        )


@pytest.mark.parametrize("field,value", [("scenario", "sample-release"), ("revision", "wrong-run")])
def test_terminal_report_must_identify_the_requested_run(report, field, value):
    report["scenario"] = {"name": "sample-normal"}
    report["reproduction"] = {"source_revision": "fresh-run"}
    validate_report(
        report, JOB, "succeeded", 1, scenario="sample-normal", source_revision="fresh-run"
    )
    if field == "scenario":
        report["scenario"]["name"] = value
    else:
        report["reproduction"]["source_revision"] = value
    with pytest.raises(ValueError):
        validate_report(
            report, JOB, "succeeded", 1, scenario="sample-normal", source_revision="fresh-run"
        )


@pytest.mark.parametrize("damage", ["message_only", "id_only", "duplicate", "wrong_status"])
def test_history_requires_complete_unique_matching_reports(report, damage):
    history = {"success": True, "data": {"reports": [copy.deepcopy(report)]}}
    expected = {JOB: report}
    runner.validate_history(history, expected)
    if damage == "message_only":
        history["message"] = JOB
        history["data"]["reports"] = []
    elif damage == "id_only":
        history["data"]["reports"] = [{"job_id": JOB}]
    elif damage == "duplicate":
        history["data"]["reports"].append(copy.deepcopy(report))
    else:
        history["data"]["reports"][0]["status"] = "failed"
    with pytest.raises(ValueError):
        runner.validate_history(history, expected)


def test_history_allows_earlier_unrelated_runs(report):
    earlier = copy.deepcopy(report)
    earlier["job_id"] = "b" * 32
    runner.validate_history({"data": {"reports": [earlier, report]}}, {JOB: report})
