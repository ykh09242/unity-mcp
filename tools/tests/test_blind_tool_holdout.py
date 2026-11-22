"""Exercise held-out prompt delivery, guarded reads and actor-owned scoring."""

from argparse import Namespace
from hashlib import sha256
import json
from uuid import UUID

import pytest

from tools.experiments import blind_tool_actions as actions
from tools.experiments import blind_tool_console as console
from tools.experiments import blind_tool_holdout as holdout
from tools.experiments.blind_tool_host import project_item
from tools.experiments.blind_tool_selection import LANGUAGES, delivery_prompt, main, task_prompt
from tools.experiments.blind_tool_trace import decision, recorded_route, route_name


PARENT = UUID("00000000-0000-4000-8000-000000000001")
CHILD = UUID("00000000-0000-4000-8000-000000000002")
ACTOR = sha256(CHILD.bytes).hexdigest()
MODEL = "tool-choice-model"
EFFORT = "medium"
MENU_PATH = "Window/Analysis/Frame Debugger"
CAMERAS = [{"name": "Fixture Camera 3", "depth": -2}, {"name": "Fixture Camera 17", "depth": 9.25}]
ERRORS = ["Fixture error.", "With  two spaces.", "Fixture error."]
WARNINGS = ["Fixture warning."]
STATE = {
    "project": "Fixture",
    "instance": "Fixture@01234567",
    "editor_pid": 42,
    "play_mode": False,
    "frame_debugger_windows": 0,
    "observation_fixtures": {
        actions.MENU_TASK: {"menu_path": MENU_PATH},
        actions.CAMERA_TASK: {"unityCameras": CAMERAS, "truncated": False},
        console.TASK: {"errors": ERRORS, "warnings": WARNINGS},
    },
}


def dump(path, value):
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def rows(path, values):
    path.write_text("".join(json.dumps(value) + "\n" for value in values), encoding="utf-8")


def event(kind, **item):
    return {"type": kind, **({"item": item} if item else {})}


def mcp(item_id, tool, args=None, *, facts=None, status="completed", error=None):
    item = {
        "id": item_id,
        "type": "mcp_tool_call",
        "server": "unityMCP",
        "tool": tool,
        "arguments": args or {},
    }
    result = {"status": status}
    if facts is not None:
        result["observed_result"] = {"status": "returned", "facts": facts}
    if error:
        result["error"] = {"message": error}
    return [event("item.started", **item), event("item.completed", **item, **result)]


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("task", holdout.TASKS)
def test_holdout_delivers_identical_user_request_to_native_roles(task, language):
    args = Namespace(
        graph_asset="", model=MODEL, effort=EFFORT, prompt_set="holdout", answer_format="json"
    )
    direct = delivery_prompt(args, task, language, "main")
    delegated = delivery_prompt(args, task, language, "subagent")
    request = task_prompt(task, language, "", prompt_set="holdout")
    assert (
        direct.split("\n\n", 1)[1]
        == delegated.split("<request>\n", 1)[1].split("\n</request>", 1)[0]
        == request
    )
    assert request != task_prompt(task, language, "", prompt_set="natural")
    assert not any(
        hidden in request
        for hidden in (
            MENU_PATH,
            "list_menu_declarations",
            "execute_menu_item",
            "inspect_scene_cameras",
            "read_console",
            *ERRORS,
            *(cam["name"] for cam in CAMERAS),
        )
    )


@pytest.mark.parametrize(
    "mode,backend",
    [("run", ["--endpoint"]), ("matched", ["--baseline-endpoint", "--candidate-endpoint"])],
)
def test_unsupported_task_fails_before_launch_or_output(mode, backend, tmp_path, capsys):
    arguments = [
        mode,
        "--output",
        str(tmp_path / "never-created"),
        "--project",
        str(tmp_path),
        "--model",
        MODEL,
        "--effort",
        EFFORT,
        "--prompt-set",
        "holdout",
        "--task",
        "graph",
        "--language",
        "ko",
        "--role",
        "main",
    ]
    for flag in backend:
        arguments += [flag, "http://127.0.0.1:8080/mcp"]
    with pytest.raises(SystemExit) as failure:
        main(arguments)
    assert failure.value.code == 2
    assert "Remove unsupported tasks: graph" in capsys.readouterr().err
    assert not (tmp_path / "never-created").exists()


@pytest.mark.parametrize(
    "task,route,arguments,expected",
    [
        (actions.MENU_TASK, actions.MENU_READER, {}, "unity"),
        (actions.MENU_TASK, actions.MENU_RESOURCE, {"url_query": ""}, "unity"),
        (actions.MENU_TASK, actions.MENU_RESOURCE, {"url_query": "invalid=1"}, "blocked"),
        (actions.MENU_TASK, actions.MENU_EXECUTION, {"action": "execute"}, "blocked"),
        (actions.MENU_TASK, "execute_menu_item", {}, "blocked"),
        (actions.CAMERA_TASK, actions.CAMERA_READER, {}, "unity"),
        (actions.CAMERA_TASK, actions.CAMERA_RESOURCE, {}, "unity"),
        (actions.CAMERA_TASK, actions.CAMERA_READER, {"target": "other"}, "blocked"),
        (actions.CAMERA_TASK, actions.MENU_READER, {}, "blocked"),
        (actions.CAMERA_TASK, "manage_camera.list_cameras", {"action": "list_cameras"}, "blocked"),
    ],
)
def test_http_holds_scope_and_only_forwards_canonical_read_routes(task, route, arguments, expected):
    assert decision(route, arguments, "", endpoint=True, fixture={"task": task}) == "blocked"
    assert (
        decision(
            route, arguments, "", endpoint=True, fixture={"task": task, "prompt_set": "holdout"}
        )
        == expected
    )


def make_case(root, task, role):
    path = root / f"{task}-ko-{role}-01"
    path.mkdir()
    dump(
        path / "case.json",
        {
            "task": task,
            "language": "ko",
            "role": role,
            "repetition": 1,
            "graph_asset": "",
            "prompt_set": "holdout",
            "runner_status": "finished",
            "host_mode": "configured",
            "backend": "installed-guarded",
            "model": MODEL,
            "effort": EFFORT,
            "project": STATE["project"],
            "instance": STATE["instance"],
            "editor_start_state": STATE,
            "editor_end_state": STATE,
            "observation_fixture": {"task": task, "prompt_set": "holdout"},
        },
    )
    surface = {"status": "observed", "model": MODEL, "effort": EFFORT, "project_verified": True}
    dump(path / "host.json", surface)
    if role == "subagent":
        dump(path / f"host-child-{ACTOR}.json", surface)
    return path


@pytest.mark.parametrize(
    "parent_attempt,child_relinked", [(False, True), (True, True), (False, False)]
)
def test_score_counts_only_the_linked_actor_and_each_blocked_retry(
    parent_attempt, child_relinked, tmp_path, capsys
):
    path = make_case(tmp_path, actions.MENU_TASK, "subagent")
    dump(
        tmp_path / "matrix.json",
        [[actions.MENU_TASK, "ko", "subagent", 1], [actions.MENU_TASK, "ja", "main", 1]],
    )
    parent = [{"type": "thread.started", "thread_id": str(PARENT)}]
    if parent_attempt:
        parent += mcp("p", "execute_menu_item", status="failed", error="Approval denied")
    if child_relinked:
        parent.append({"type": "host.child_linked", "child_actor": ACTOR})
    rows(path / "events.jsonl", [*parent, event("turn.completed")])
    child = [
        *mcp("1", "search_unity_tools", facts={}),
        *mcp("2", actions.MENU_READER, facts={"menu_path": MENU_PATH}),
        *mcp("3", "execute_menu_item", {"action": "inspect", "menu_path": MENU_PATH}, facts={}),
        *mcp("4", "execute_menu_item", status="failed", error="Approval denied"),
        *mcp("5", "execute_menu_item", {"action": "execute"}, status="failed"),
        event("item.completed", type="agent_message", text="Window > Analysis > Frame Debugger"),
        event("turn.completed"),
    ]
    rows(path / f"child-{ACTOR}.jsonl", [{**item, "host_actor": ACTOR} for item in child])
    rows(
        path / "mcp-test.jsonl",
        [
            {
                "event": "request",
                "sequence": 1,
                "route": actions.MENU_EXECUTION,
                "channel": "blocked",
                "actor": ACTOR,
                "actor_binding": "bound",
            },
            {"event": "response", "sequence": 1, "outcome": "error"},
        ],
    )
    assert main(["score", str(tmp_path)]) == 1
    report = json.loads(capsys.readouterr().out)
    case = report["cases"][0]
    held = case["holdout"]
    assert held["unnecessary_menu_execution_attempts"] == (2 if child_relinked else None)
    assert held["eligible"] == (child_relinked and not parent_attempt)
    assert report["holdout"]["overall"]["unrun_cases"] == 1
    assert len(report["holdout"]["by_task_language_role"]) == 2
    if child_relinked:
        assert held["first_actor_business_tool"] == actions.MENU_READER
        assert held["first_actor_business_tool_suitable"] is True
        assert held["first_observed_business_action"] == "search_unity_tools"
        assert held["first_observed_business_action_suitable"] is False
        assert held["canonical_oracle_answer"] == {"menu_path": MENU_PATH}
        assert held["actor_evidence_matches_oracle"] is True
        assert held["oracle_answer_status"] == "correct"
    else:
        assert "role:unverified" in held["exclusions"]
        assert held["actor_observed_calls"] is None


@pytest.mark.parametrize(
    "task,tool,arguments,facts,answer,expected",
    [
        (
            actions.CAMERA_TASK,
            actions.CAMERA_READER,
            {},
            {"camera": CAMERAS[1]["name"], "depth": 9.25},
            "Fixture Camera 17 — depth 9,25",
            {"camera": "Fixture Camera 17", "depth": 9.25},
        ),
        (
            console.TASK,
            "read_console",
            {"action": "get", "types": ["error"]},
            {"error_filter": True, "layout": "list", "errors": ERRORS},
            json.dumps(ERRORS),
            {"errors": ERRORS},
        ),
    ],
)
def test_score_uses_independent_oracle_and_a_suitable_actor_read(
    task, tool, arguments, facts, answer, expected, tmp_path, capsys
):
    path = make_case(tmp_path, task, "main")
    rows(
        path / "events.jsonl",
        [
            {"type": "thread.started", "thread_id": str(PARENT)},
            *mcp("1", tool, arguments, facts=facts),
            event("item.completed", type="agent_message", text=answer),
            event("turn.completed"),
        ],
    )
    assert main(["score", str(tmp_path), "--prompt-set", "holdout"]) == 0
    case = json.loads(capsys.readouterr().out)["cases"][0]["holdout"]
    assert case["canonical_oracle_answer"] == expected
    assert case["first_observed_business_action_suitable"] is True
    assert case["grounded_answer"] is True


@pytest.mark.parametrize("filtered", [False, True])
def test_console_unfiltered_request_does_not_count_as_a_suitable_first_read(
    filtered, tmp_path, capsys
):
    path = make_case(tmp_path, console.TASK, "main")
    rows(
        path / "events.jsonl",
        [
            {"type": "thread.started", "thread_id": str(PARENT)},
            *mcp("1", "read_console", {"action": "get", "types": ["error" if filtered else "all"]}),
            event("item.completed", type="agent_message", text=json.dumps(ERRORS)),
            event("turn.completed"),
        ],
    )
    assert main(["score", str(tmp_path)]) == 0
    result = json.loads(capsys.readouterr().out)["cases"][0]["holdout"]
    assert result["first_actor_business_tool_suitable"] is filtered
    assert result["oracle_answer_status"] == "correct"
    assert result["grounded_answer"] is False


@pytest.mark.parametrize("windows_after,changed", [(0, False), (1, True)])
def test_state_drift_excludes_opened_window_even_if_path_is_correct(windows_after, changed):
    result = holdout.assess(
        {
            "task": actions.MENU_TASK,
            "runner_status": "finished",
            "project": STATE["project"],
            "instance": STATE["instance"],
            "editor_start_state": {**STATE, "frame_debugger_windows": 0},
            "editor_end_state": {**STATE, "frame_debugger_windows": windows_after},
        },
        [],
        MENU_PATH,
        role="confirmed",
        actor_completed=True,
        controls=[],
    )
    assert result["oracle_answer_status"] == "correct"
    assert result["fixture_preserved"] is not changed
    assert result["eligible"] is not changed
    assert ("fixture_changed" in result["exclusions"]) is changed


@pytest.mark.parametrize(
    "answer,status",
    [
        ("It was not `Fixture Camera 17`, and the value was 9.25.", "incorrect"),
        ("Fixture Camera 17 — depth 9.25. That answer is wrong.", "incorrect"),
        ("`Fixture Camera 17` — depth 9.25 (uncertain).", "needs_review"),
        ("Possibly `Fixture Camera 17`, or another GameObject, 9.25.", "needs_review"),
        ("Fixture Camera 17 Clone — depth 9.25", "incorrect"),
        ("Fixture Camera 17 — depth 10", "incorrect"),
        ("**GameObject**: `Fixture Camera 17`\n- `Camera.depth`: **9.25**", "correct"),
        ("Fixture Camera 17 — depth 値: 9.25", "correct"),
        ("Fixture Camera 17 — 깊이: 9.25", "correct"),
        ("Fixture Camera 17 (profundidad: 9,25)", "correct"),
        ("Fixture Camera 17، العمق: ٩٫٢٥", "correct"),
        ("Fixture Camera 17 — depth değeri: 9,25", "correct"),
    ],
)
def test_camera_answer_requires_a_single_affirmative_exact_pair(answer, status):
    expected = {"camera": "Fixture Camera 17", "depth": 9.25}
    assert holdout.answer_match(actions.CAMERA_TASK, answer, expected, STATE) == status


def test_actor_initiated_catalog_precedes_a_suitable_business_read(tmp_path, capsys):
    path = make_case(tmp_path, actions.CAMERA_TASK, "main")
    rows(
        path / "events.jsonl",
        [
            {"type": "thread.started", "thread_id": str(PARENT)},
            *mcp("1", "manage_tools", {"action": "catalog"}, facts={}),
            *mcp("2", actions.CAMERA_READER, facts={"camera": CAMERAS[1]["name"], "depth": 9.25}),
            event("item.completed", type="agent_message", text="Fixture Camera 17 — depth 9.25"),
            event("turn.completed"),
        ],
    )
    rows(
        path / "mcp-init.jsonl",
        [
            {
                "event": "request",
                "sequence": 1,
                "route": "initialize",
                "channel": "discovery",
                "actor": sha256(PARENT.bytes).hexdigest(),
                "actor_binding": "bound",
            }
        ],
    )
    assert main(["score", str(tmp_path)]) == 0
    held = json.loads(capsys.readouterr().out)["cases"][0]["holdout"]
    assert held["first_observed_business_action"] == "manage_tools.catalog"
    assert held["first_actor_business_tool"] == actions.CAMERA_READER
    assert held["first_actor_business_tool_suitable"] is True


@pytest.mark.parametrize(
    "task,key,value,expected",
    [
        (actions.MENU_TASK, "frame_debugger_windows", None, "fixture_preservation_unverified"),
        (actions.CAMERA_TASK, "play_mode", True, "fixture_changed"),
        (console.TASK, "instance", "Fixture@87654321", "fixture_changed"),
        (
            actions.MENU_TASK,
            console.TASK,
            {"errors": ERRORS, "warnings": [*WARNINGS, "new"]},
            "fixture_changed",
        ),
        (
            console.TASK,
            actions.CAMERA_TASK,
            {"unityCameras": [*CAMERAS, {"name": "Extra Camera", "depth": 0}], "truncated": False},
            "fixture_changed",
        ),
    ],
)
def test_all_shared_fixture_values_are_required_and_must_be_preserved(task, key, value, expected):
    before = STATE
    if key in STATE["observation_fixtures"]:
        after = {**STATE, "observation_fixtures": {**STATE["observation_fixtures"], key: value}}
    elif value is None:
        before = after = {k: v for k, v in STATE.items() if k != key}
    else:
        after = {**STATE, key: value}
    result = holdout.assess(
        {
            "task": task,
            "project": STATE["project"],
            "instance": STATE["instance"],
            "runner_status": "finished",
            "editor_start_state": before,
            "editor_end_state": after,
        },
        [],
        "",
        role="confirmed",
        actor_completed=True,
        controls=[],
    )
    assert result["eligible"] is False
    assert expected in result["exclusions"]
    assert key in {field for fields in result["fixture_diff"].values() for field in fields}


@pytest.mark.parametrize(
    "arguments,expected,count",
    [
        ({}, actions.MENU_EXECUTION, 1),
        ({"action": None}, actions.MENU_EXECUTION, 1),
        ({"action": "execute"}, actions.MENU_EXECUTION, 1),
        ({"action": "inspect"}, "execute_menu_item.inspect", 0),
        ({"action": "exists"}, "execute_menu_item.exists", 0),
        ({"action": "evaluate_availability"}, actions.MENU_AVAILABILITY, 0),
        ({"action": "list"}, actions.MENU_INVALID_ROUTE, 0),
        ({"action": ""}, actions.MENU_INVALID_ROUTE, 0),
        ({"action": []}, actions.MENU_INVALID_ROUTE, 0),
        ([], actions.MENU_INVALID_ROUTE, 0),
    ],
)
def test_host_and_wire_keep_invalid_explicit_menu_actions_distinct_from_execution(
    arguments, expected, count, tmp_path, capsys
):
    path = make_case(tmp_path, actions.MENU_TASK, "main")
    fixture = {"task": actions.MENU_TASK, "prompt_set": "holdout"}
    raw = {"name": "execute_menu_item", "arguments": arguments}
    route, args = route_name("tools/call", raw)
    channel = decision(route, args, "", fixture=fixture)
    sanitized = recorded_route(route, channel, fixture)
    item = project_item(
        {
            "type": "mcpToolCall",
            "server": "unityMCP",
            "tool": raw["name"],
            "arguments": arguments,
            "id": "one",
            "status": "failed",
        },
        "unityMCP",
        "",
        fixture=fixture,
    )
    rows(
        path / "events.jsonl",
        [
            {"type": "thread.started", "thread_id": str(PARENT)},
            event("item.started", **item),
            event("item.completed", **item),
            event("turn.completed"),
        ],
    )
    rows(
        path / "mcp-menu.jsonl",
        [
            {
                "event": "request",
                "sequence": 1,
                "route": sanitized,
                "channel": channel,
                "actor": sha256(PARENT.bytes).hexdigest(),
                "actor_binding": "bound",
            },
            {"event": "response", "sequence": 1, "outcome": "error"},
        ],
    )
    assert main(["score", str(tmp_path)]) == 0
    held = json.loads(capsys.readouterr().out)["cases"][0]["holdout"]
    assert sanitized == held["first_actor_business_tool"] == expected
    assert held["unnecessary_menu_execution_attempts"] == count
    assert len(held["actor_observed_calls"]) == 1


@pytest.mark.parametrize(
    "answer,status,only",
    [
        ("Window > Analysis > Frame Debugger", "correct", True),
        (
            "Unity エディターでは **Window → Analysis → Frame Debugger** です。 "
            "[公式マニュアル](https://example.com/Window/Rendering/Frame-Debugger)",
            "correct",
            False,
        ),
        ("[Window → Analysis → Frame Debugger](https://example.com/docs)", "correct", True),
        ("```text\nWindow/Analysis/Frame Debugger\n```", "correct", True),
        (
            "Window/Analysis/Frame Debugger\nhttps://example.com/Window/Render/Debugger",
            "correct",
            False,
        ),
        (
            "See [the manual](https://example.com/Window/Analysis/Frame%20Debugger)",
            "needs_review",
            False,
        ),
        ("Window > Rendering > Frame Debugger https://example.com/docs", "incorrect", False),
        (
            "Either **Window > Analysis > Frame Debugger** or "
            "[Window > Rendering > Frame Debugger](https://example.com/docs).",
            "incorrect",
            False,
        ),
        (
            "Window > Analysis > Frame Debugger or Window > Rendering > Frame Debugger",
            "incorrect",
            False,
        ),
        (
            "Window > Analysis > Frame Debugger or Utilities > Diagnostics > Frame Debugger",
            "incorrect",
            False,
        ),
        ("It is not **Window > Analysis > Frame Debugger**.", "incorrect", False),
        ("**Window > Analysis > Frame Debugger**. I did not open it.", "correct", False),
        ("Maybe **Window > Analysis > Frame Debugger**.", "needs_review", False),
    ],
)
def test_menu_candidates_follow_visible_breadcrumbs_and_keep_answer_only_status(
    answer, status, only
):
    assert (
        holdout.answer_match(actions.MENU_TASK, answer, {"menu_path": MENU_PATH}, STATE) == status
    )
    assert holdout.menu_answer(answer, MENU_PATH)[1] is only


def test_documentation_answer_cannot_ground_menu_without_actor_inventory():
    result = holdout.assess(
        {
            "task": actions.MENU_TASK,
            "runner_status": "finished",
            "project": STATE["project"],
            "instance": STATE["instance"],
            "editor_start_state": STATE,
            "editor_end_state": STATE,
        },
        [],
        "Use **Window > Analysis > Frame Debugger**. [Manual](https://example.com/docs)",
        role="confirmed",
        actor_completed=True,
        controls=[],
    )
    assert result["oracle_answer_status"] == "correct"
    assert result["menu_answer_path_only"] is False
    assert result["actor_evidence_matches_oracle"] is False
    assert result["grounded_answer"] is False
