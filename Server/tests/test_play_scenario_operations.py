"""Public suite selection, one-call actions and explicit wait cancellation contracts."""

import copy
import json
from xml.etree import ElementTree

import httpx
import pytest
from cli.main import cli
from cli.utils import connection, play_scenario_suite
from click.testing import CliRunner
from models.play_scenarios import PlayScenario, PlayScenarioCommand, ScenarioStep
from pydantic import ValidationError

from .test_manage_play_scenario import DEFINITION, JOB_ID, route
from services.tools.manage_play_scenario import manage_play_scenario
from types import SimpleNamespace

SUITE = {
    "name": "smoke",
    "scenarios": ["menu-start"],
    "tags": ["smoke"],
    "failure_policy": "continue",
}


@pytest.mark.parametrize(
    "action,arguments",
    [
        ("suite_save", {"suite": SUITE}),
        ("suite_get", {"name": "smoke"}),
        ("suite_list", {}),
        ("suite_delete", {"name": "smoke"}),
        ("suite_run", {"name": "smoke", "suite_id": JOB_ID, "source_revision": "fixture\\label"}),
        ("suite_status", {"suite_id": JOB_ID}),
        ("suite_cancel", {"suite_id": JOB_ID}),
        ("suite_reports", {"name": "smoke"}),
    ],
)
@pytest.mark.asyncio
async def test_suite_adapter_dispatches_once_when_action_is_valid(route, action, arguments):
    # Given: the selected native Editor owns suite execution.
    expected = PlayScenarioCommand(action=action, **arguments).wire_parameters()
    # When: one suite management request crosses the public adapter.
    result = await manage_play_scenario(SimpleNamespace(), action, **arguments)
    # Then: it preserves the response and never runs a Python status loop.
    assert result == route.send.return_value
    route.select.assert_awaited_once()
    route.send.assert_awaited_once()
    assert route.send.call_args.args[3] == expected
    assert route.send.call_args.kwargs == {"retry_on_reload": False}


@pytest.mark.parametrize(
    "arguments",
    [
        {"action": "suite_save", "suite": {"name": "smoke"}},
        {"action": "suite_save", "suite": {**SUITE, "scenarios": ["one", "one"]}},
        {"action": "suite_save", "suite": {**SUITE, "tags": ["UPPER"]}},
        {"action": "suite_save", "suite": {**SUITE, "schema_version": True}},
        {"action": "suite_save", "suite": {**SUITE, "scenarios": [f"s{i}" for i in range(17)]}},
        {"action": "suite_run", "name": "smoke", "job_id": JOB_ID},
        {"action": "suite_status", "job_id": JOB_ID},
        {"action": "suite_run", "name": "smoke", "source_revision": "\n"},
        {"action": "suite_run", "name": "smoke", "source_revision": "\U0001f987" * 65},
    ],
)
@pytest.mark.asyncio
async def test_suite_adapter_has_no_effect_when_request_is_invalid(route, arguments):
    # Given: incompatible fields, ambiguous selectors or out-of-bound definition input.
    # When: the public boundary parses the request.
    result = await manage_play_scenario(SimpleNamespace(), **arguments)
    # Then: no Editor selection or native side effect occurs.
    assert result["success"] is False
    route.select.assert_not_called()
    route.send.assert_not_called()


@pytest.mark.parametrize(
    "changes",
    [
        {"tags": ["smoke", "smoke"]},
        {"tags": ["smoke"] * 17},
        {"resources": {"enabled": True, "max_handles": True}},
        {"resources": {"max_scriptable_objects": 4097}},
        {"resources": {"max_subscriptions": -1}},
    ],
)
def test_scenario_options_reject_invalid_limits_when_definition_is_saved(changes):
    # Given: registered assertions and suite tags use strict shared bounds.
    # When/Then: malformed input cannot enter a saved native definition.
    with pytest.raises(ValidationError):
        PlayScenario.model_validate({**DEFINITION, **changes})


@pytest.mark.parametrize(
    "step",
    [
        {"action": "wait_object", "target_id": "player", "count": 2},
        {"action": "wait_object", "target_id": "player", "click_mode": "raycast"},
        {"action": "wait_object", "target_id": "player", "target": "Player"},
        {"action": "wait_object", "target_id": "bad/id"},
        {"action": "wait_object", "target_id": None, "target": "Player"},
        {"action": "click_ui", "target_id": "start", "click_mode": None},
        {"action": "load_scene", "scene": "Assets/Menu.unity", "target_id": "menu"},
    ],
)
def test_id_selector_rejects_ambiguous_or_unused_fields_when_step_is_parsed(step):
    # Given: scene/object selectors have mutually exclusive wire fields.
    # When/Then: unused input and IDs outside their grammar fail before inspection.
    with pytest.raises(ValidationError):
        ScenarioStep.model_validate({"name": "Observe", **step})


def test_expanded_definition_retains_id_false_and_zero_when_wire_is_built():
    # Given: explicit registered assertions and both pointer modes use stable target IDs.
    definition = copy.deepcopy(DEFINITION)
    definition.update(tags=["smoke"], resources={"enabled": True, "max_handles": 0})
    definition["steps"] += [
        {"name": "Raycast", "action": "click_ui", "target_id": "start", "click_mode": "raycast"},
        {"name": "Missing", "action": "wait_object", "target_id": "gone", "count": 0},
    ]
    # When: the native save payload is built at the shared boundary.
    wire = PlayScenarioCommand(action="save", scenario=definition).wire_parameters()["scenario"]
    # Then: exact IDs/options survive and defaults retain strict registered assertions.
    assert wire["tags"] == ["smoke"]
    assert wire["resources"] == {
        "enabled": True,
        "max_scriptable_objects": 0,
        "max_subscriptions": 0,
        "max_handles": 0,
    }
    assert wire["steps"][-2]["click_mode"] == "raycast"
    assert wire["steps"][-1]["count"] == 0
    assert "target" not in wire["steps"][-1]


@pytest.fixture
def suite_wire(monkeypatch):
    # Given: the actual HTTP transport is inert and every command hits one selected Editor.
    calls = []
    replies = []
    client_type = httpx.AsyncClient

    def respond(request):
        calls.append(json.loads(request.content))
        reply = replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return httpx.Response(200, json=reply)

    monkeypatch.setattr(connection, "read_local_auth_token", lambda host, port: "fixture")
    monkeypatch.setattr(
        connection.httpx,
        "AsyncClient",
        lambda: client_type(transport=httpx.MockTransport(respond), trust_env=False),
    )
    monkeypatch.setattr(play_scenario_suite, "sleep", lambda seconds: None)
    return calls, replies


def receipt(status, **fields):
    return {
        "success": True,
        "data": {
            "suite_id": JOB_ID,
            "suite": SUITE,
            "status": status,
            "scenarios": [
                {
                    "name": "menu-start",
                    "status": status,
                    "report": {
                        "job_id": "b" * 32,
                        "error": "original failure" if status == "failed" else None,
                    },
                }
            ],
            **fields,
        },
    }


@pytest.mark.parametrize(
    "status,exit_code", [("succeeded", 0), ("failed", 1), ("timed_out", 1), ("cancelled", 1)]
)
def test_cli_suite_exports_outcome_when_wait_reaches_terminal(
    suite_wire, tmp_path, status, exit_code
):
    # Given: one native child finalizes after the suite start receipt.
    calls, replies = suite_wire
    replies.extend([receipt("running"), receipt(status)])
    # When: the user explicitly runs and waits for one suite.
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            "--instance",
            "Selected@owned",
            "play-scenario",
            "suite-run",
            "smoke",
            "--suite-id",
            JOB_ID,
            "--output-dir",
            str(tmp_path),
        ],
    )
    # Then: status determines exit and persisted artifacts while all commands retain selection.
    assert result.exit_code == exit_code, result.output
    assert json.loads((tmp_path / "suite.json").read_text(encoding="utf-8"))["status"] == status
    assert ElementTree.parse(tmp_path / "junit.xml").getroot().attrib["tests"] == "1"
    assert [call["params"]["action"] for call in calls] == ["suite_run", "suite_status"]
    assert all(call["unity_instance"] == "Selected@owned" for call in calls)


def test_cli_cancels_once_when_interrupted_during_observation(suite_wire, tmp_path):
    # Given: Ctrl+C interrupts an active suite before its child finalizes.
    calls, replies = suite_wire
    replies.extend(
        [receipt("running"), KeyboardInterrupt(), receipt("running"), receipt("cancelled")]
    )
    # When: suite-run observes, cancels once, then waits for the final receipt.
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            "--instance",
            "Selected@owned",
            "play-scenario",
            "suite-run",
            "smoke",
            "--suite-id",
            JOB_ID,
            "--output-dir",
            str(tmp_path),
        ],
    )
    # Then: cancellation is singular, no second suite starts, and the failure receipt is retained.
    assert result.exit_code == 1, result.output
    assert [call["params"]["action"] for call in calls] == [
        "suite_run",
        "suite_status",
        "suite_cancel",
        "suite_status",
    ]
    artifact = json.loads((tmp_path / "suite.json").read_text(encoding="utf-8"))
    assert artifact["status"] == "cancelled"
    assert artifact["client_error"] == "Interrupted"


def test_cli_reports_incomplete_finalization_when_interrupted_twice(suite_wire, tmp_path):
    # Given: a second interrupt occurs while observing cancellation cleanup.
    calls, replies = suite_wire
    replies.extend(
        [receipt("running"), KeyboardInterrupt(), receipt("running"), KeyboardInterrupt()]
    )
    # When: the runner reaches the second interruption.
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            "--instance",
            "Selected@owned",
            "play-scenario",
            "suite-run",
            "smoke",
            "--suite-id",
            JOB_ID,
            "--output-dir",
            str(tmp_path),
        ],
    )
    # Then: no cancellation replay or new job occurs and unknown finalization is explicit.
    assert result.exit_code == 1, result.output
    assert [call["params"]["action"] for call in calls].count("suite_cancel") == 1
    artifact = json.loads((tmp_path / "suite.json").read_text(encoding="utf-8"))
    assert artifact["status"] == "running"
    assert "finalization receipt unavailable" in artifact["client_error"]


def test_cli_suite_requires_selected_editor_when_wait_is_requested(suite_wire, tmp_path):
    # Given: the caller did not pin the suite to a single Editor.
    calls, _ = suite_wire
    # When: the explicit waiter is invoked.
    result = CliRunner().invoke(
        cli, ["play-scenario", "suite-run", "smoke", "--output-dir", str(tmp_path)]
    )
    # Then: admission fails locally without starting native jobs.
    assert result.exit_code == 2
    assert calls == []


@pytest.mark.parametrize(
    "action,args",
    [
        ("suite-get", ["smoke"]),
        ("suite-list", []),
        ("suite-delete", ["smoke"]),
        ("suite-status", [JOB_ID]),
        ("suite-cancel", [JOB_ID]),
        ("suite-reports", []),
    ],
)
def test_cli_suite_action_is_one_request_when_not_explicit_wait(suite_wire, action, args):
    # Given: native suite state is immediately available.
    calls, replies = suite_wire
    replies.append(receipt("succeeded"))
    # When: a single suite management action is requested.
    result = CliRunner().invoke(cli, ["--format", "json", "play-scenario", action, *args])
    # Then: one command is sent and no implicit polling occurs.
    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    assert calls[0]["params"]["action"] == action.replace("-", "_")


@pytest.mark.parametrize(
    "final_status,clock_values", [("cancelled", [0, 2, 2, 2]), ("running", [0, 2, 2, 4])]
)
def test_cli_cancels_at_deadline_when_native_suite_has_not_finalized(
    suite_wire, tmp_path, monkeypatch, final_status, clock_values
):
    # Given: explicit execution expires; native cleanup may finish or consume its grace.
    calls, replies = suite_wire
    replies.extend([receipt("running"), receipt(final_status)])
    ticks = iter(clock_values)
    monkeypatch.setattr(play_scenario_suite, "monotonic", lambda: next(ticks))
    # When: the one-second suite budget is exhausted.
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            "--instance",
            "Selected@owned",
            "play-scenario",
            "suite-run",
            "smoke",
            "--suite-id",
            JOB_ID,
            "--timeout-seconds",
            "1",
            "--cleanup-wait-seconds",
            "1",
            "--output-dir",
            str(tmp_path),
        ],
    )
    # Then: exactly one cancellation is requested and no new suite starts after timeout.
    assert result.exit_code == 1, result.output
    assert [call["params"]["action"] for call in calls] == ["suite_run", "suite_cancel"]
    artifact = json.loads((tmp_path / "suite.json").read_text(encoding="utf-8"))
    assert artifact["status"] == final_status
    assert "deadline exceeded" in artifact["client_error"]


def test_cli_exit_is_failure_when_artifact_path_is_not_a_directory(suite_wire, tmp_path):
    # Given: native execution succeeds but the chosen output path is an existing file.
    calls, replies = suite_wire
    replies.append(receipt("succeeded"))
    output = tmp_path / "artifacts"
    output.mkdir()
    (output / "junit.xml").mkdir()
    marker = output / "junit.xml" / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    # When: the caller tries to persist report artifacts there.
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            "--instance",
            "Selected@owned",
            "play-scenario",
            "suite-run",
            "smoke",
            "--suite-id",
            JOB_ID,
            "--output-dir",
            str(output),
        ],
    )
    # Then: the error receipt is visible, exit is nonzero, and existing content is preserved.
    assert result.exit_code == 1, result.output
    assert len(calls) == 1
    assert json.loads(result.stdout)["error"] == "report_persist_failed"
    assert marker.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize(
    "action,args", [("run", ["menu-start"]), ("status", [JOB_ID]), ("suite-status", [JOB_ID])]
)
def test_cli_execution_query_fails_when_native_report_cannot_persist(suite_wire, action, args):
    # Given: successful execution has an explicit native persistence error.
    calls, replies = suite_wire
    replies.append(receipt("succeeded", report_error="disk full"))
    # When: a one-shot execution/query command observes the receipt.
    result = CliRunner().invoke(cli, ["--format", "json", "play-scenario", action, *args])
    # Then: missing retained evidence fails the command without starting a status loop.
    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout)["data"]["report_error"] == "disk full"
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_live_sdk_suite_schema_dispatches_expanded_definition_when_saved(route, mode):
    # Given: the installed FastMCP SDK derives input schema from the real public declaration.
    from fastmcp import FastMCP
    from fastmcp.client import Client

    server = FastMCP("scenario-operations-contract")
    server.tool(manage_play_scenario)
    schema = (await server.list_tools(run_middleware=False))[0].parameters
    assert schema["$defs"]["PlayScenarioSuite"]["properties"]["scenarios"]["maxItems"] == 16
    assert "suite_run" in schema["properties"]["action"]["enum"]
    assert "source_revision" in schema["properties"]
    async with Client(server, mode=mode) as client:
        # When: a suite crosses SDK argument conversion and reaches the actual adapter.
        result = await client.call_tool(
            "manage_play_scenario", {"action": "suite_save", "suite": SUITE}
        )
    # Then: nested selectors survive and the SDK does not start a polling loop.
    assert json.loads(result.content[0].text) == route.send.return_value
    route.send.assert_awaited_once()
    assert (
        route.send.call_args.args[3]
        == PlayScenarioCommand(action="suite_save", suite=SUITE).wire_parameters()
    )


@pytest.mark.parametrize(
    "malformed",
    [{"suite_id": "b" * 32, "status": "succeeded"}, {"suite_id": JOB_ID, "status": "unknown"}],
)
def test_cli_cancels_once_when_native_observation_is_malformed(suite_wire, tmp_path, malformed):
    # Given: a status response cannot establish identity or completion.
    calls, replies = suite_wire
    replies.extend([receipt("running"), {"success": True, "data": malformed}, receipt("cancelled")])
    # When: the explicit waiter receives the malformed report.
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            "--instance",
            "Selected@owned",
            "play-scenario",
            "suite-run",
            "smoke",
            "--suite-id",
            JOB_ID,
            "--output-dir",
            str(tmp_path),
        ],
    )
    # Then: the suite is cancelled once and cannot be falsely reported successful.
    assert result.exit_code == 1, result.output
    assert [call["params"]["action"] for call in calls] == [
        "suite_run",
        "suite_status",
        "suite_cancel",
    ]
    assert json.loads((tmp_path / "suite.json").read_text(encoding="utf-8"))["client_error"]


def test_cli_suite_id_is_not_replaced_when_explicit_value_is_empty(suite_wire, tmp_path):
    # Given: the user supplied an invalid empty request key.
    calls, _ = suite_wire
    # When: suite-run validates the explicit key before dispatch.
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            "--instance",
            "Selected@owned",
            "play-scenario",
            "suite-run",
            "smoke",
            "--suite-id",
            "",
            "--output-dir",
            str(tmp_path),
        ],
    )
    # Then: invalid input is rejected locally instead of silently creating another request ID.
    assert result.exit_code == 2
    assert calls == []
