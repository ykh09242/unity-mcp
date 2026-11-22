"""Exercise Console actor attribution, wire formats, filtering, and independent preservation."""

import json
from types import SimpleNamespace

import pytest

from tools.experiments import blind_tool_console as console
from tools.experiments.blind_tool_host import project_item
from tools.experiments.blind_tool_observations import fixture_availability
from tools.experiments.blind_tool_selection import LANGUAGES, delivery_prompt, main
from tools.experiments.blind_tool_trace import (
    client_trace,
    decision,
    observation,
    repeat_counts,
    score_case,
    thread_digest,
    unpack,
)


FIXTURE = {"task": console.TASK}
ROWS = {
    "errors": ["Atlas index 8 is invalid", "Clip was missing"],
    "warnings": ["Using the fallback atlas"],
}
STATE = {"observation_fixtures": {console.TASK: ROWS}}
ARGS = {"types": ["error"]}
REPLY = {"success": True, "data": [{"type": "Error", "message": m} for m in ROWS["errors"]]}


def test_six_natural_requests_are_identical_for_main_and_child_without_oracle_or_route():
    args = SimpleNamespace(
        graph_asset="", prompt_set="natural", answer_format="json", model="abc", effort="high"
    )
    for language in LANGUAGES:
        direct = delivery_prompt(args, console.TASK, language, "main")
        delegated = delivery_prompt(args, console.TASK, language, "subagent")
        assert direct == delegated.partition("<request>\n")[2].partition("\n</request>")[0]
        assert "\n" not in direct
        assert not any(
            token in direct
            for token in ["read_console", "editor_live", "JSON", *ROWS["errors"], *ROWS["warnings"]]
        )


def test_console_rejects_isolated_host_before_launch(capsys):
    with pytest.raises(SystemExit) as error:
        main(
            [
                "run",
                "--project",
                ".",
                "--output",
                "never-created",
                "--task",
                console.TASK,
                "--language",
                "en",
                "--role",
                "main",
                "--model",
                "test",
                "--effort",
                "low",
                "--state-probe",
                "probe.py",
                "--relay",
                "relay.py",
                "--host-mode",
                "isolated",
            ]
        )
    assert error.value.code == 2
    assert "requires --host-mode configured" in capsys.readouterr().err


@pytest.mark.parametrize("endpoint", [True, False])
def test_get_is_scoped_to_console_fixture_and_clear_is_blocked(endpoint):
    for route, args in (("read_console", ARGS), ("read_console.get", {"action": "get", **ARGS})):
        assert decision(route, args, "", fixture=FIXTURE, endpoint=endpoint) == "unity"
        assert decision(route, args, "", fixture={"task": "graph"}, endpoint=endpoint) == "blocked"
    assert (
        decision("read_console.clear", {"action": "clear"}, "", fixture=FIXTURE, endpoint=endpoint)
        == "blocked"
    )
    assert (
        decision("read_console", {"action": ["clear"]}, "", fixture=FIXTURE, endpoint=endpoint)
        == "blocked"
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"structuredContent": REPLY},
        {"content": [{"type": "text", "text": json.dumps(REPLY)}]},
        {"structured_content": {"result": REPLY}},
        {"structuredContent": {"success": True, "data": ROWS["errors"]}},
    ],
)
def test_actual_request_and_projected_reply_support_real_console_formats(payload):
    status, data = unpack({"result": payload}, "read_console")
    assert status == "returned"
    for args in (ARGS, {"types": '[" ERROR "]'}):
        facts = observation("read_console", data, FIXTURE, arguments=args)
        assert console.witnessed([facts], ROWS["errors"])
    for args in (
        {},
        {"types": ["error", "warning"]},
        {"types": "error"},
        {**ARGS, "filter_text": "Atlas"},
    ):
        assert not console.witnessed(
            [observation("read_console", data, FIXTURE, arguments=args)], ROWS["errors"]
        )


def test_incomplete_mixed_and_malformed_reads_cannot_establish_the_errors():
    for row in ({"type": "Warning", "message": "warning"}, {"type": [], "message": "bad"}):
        facts = console.read_facts(ARGS, {"items": [*REPLY["data"], row]})
        assert not console.witnessed([facts], ROWS["errors"])
    first = console.read_facts(
        {**ARGS, "page_size": 1},
        {
            "items": REPLY["data"][:1],
            "cursor": 0,
            "pageSize": 1,
            "total": 2,
            "truncated": True,
            "nextCursor": "1",
        },
    )
    last = console.read_facts(
        {**ARGS, "page_size": 1, "cursor": "1"},
        {
            "items": REPLY["data"][1:],
            "cursor": 1,
            "pageSize": 1,
            "total": 2,
            "truncated": False,
            "nextCursor": None,
        },
    )
    assert not console.witnessed([first], ROWS["errors"])
    assert not console.witnessed([first, {**last, "offset": 2}], ROWS["errors"])
    assert console.witnessed([first, last], ROWS["errors"])
    assert not console.witnessed([first, {**last, "more": True}], ROWS["errors"])


def write_rows(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_console_pages_retain_requested_offsets_through_configured_host(tmp_path):
    events = []
    for index, cursor in enumerate((0, "2", 2)):
        item = project_item(
            {
                "id": str(index),
                "type": "mcpToolCall",
                "server": "unityMCP",
                "tool": "read_console",
                "arguments": {
                    **ARGS,
                    "cursor": cursor,
                    "page_size": 2,
                    **({"action": "get"} if index == 2 else {}),
                },
                "status": "completed",
                "result": {"structuredContent": REPLY},
            },
            "unityMCP",
            "",
            fixture=FIXTURE,
        )
        events.append({"type": "item.completed", "item": item})
    path = tmp_path / "events.jsonl"
    write_rows(path, events)
    calls = list(client_trace(path, "", endpoint=True, fixture=FIXTURE).calls.values())
    assert [c.page for c in calls] == [0, 2, 2]
    assert repeat_counts(calls) == (1, 0)


@pytest.mark.parametrize("role", ["main", "subagent"])
def test_score_case_requires_completed_answer_from_role_verified_actor(tmp_path, role):
    parent = "01800000-0000-4000-8100-000000000001"
    child = "01800000-0000-4000-8100-000000000002"
    actor = thread_digest(parent if role == "main" else child)
    case = {
        "task": console.TASK,
        "role": role,
        "language": "ko",
        "repetition": 1,
        "graph_asset": "",
        "runner_status": "finished",
        "host_mode": "configured",
        "model": "trial-model",
        "effort": "medium",
        "backend": "unity-mcp-loopback",
        "observation_fixture": FIXTURE,
        "editor_start_state": STATE,
        "editor_end_state": STATE,
    }
    (tmp_path / "case.json").write_text(json.dumps(case))
    started = {"type": "thread.started", "thread_id": parent}
    done = {"type": "turn.completed", "host_actor": actor}
    item = project_item(
        {
            "id": "read",
            "type": "mcpToolCall",
            "server": "unityMCP",
            "tool": "read_console",
            "arguments": ARGS,
            "result": {"structuredContent": REPLY},
            "status": "completed",
        },
        "unityMCP",
        "",
        fixture=FIXTURE,
    )
    facts = item["observed_result"]["facts"]
    answer = {
        "type": "item.completed",
        "host_actor": actor,
        "item": {
            "type": "agent_message",
            "id": "reply",
            "phase": "final_answer",
            "text": "\n".join("- " + m for m in ROWS["errors"]),
        },
    }
    actor_events = [{"type": "item.completed", "host_actor": actor, "item": item}, answer, done]
    if role == "subagent":
        write_rows(
            tmp_path / "events.jsonl",
            [started, {"type": "host.child_linked", "child_actor": actor}, done],
        )
        write_rows(tmp_path / f"child-{actor}.jsonl", actor_events)
        surface = f"host-child-{actor}.json"
    else:
        write_rows(tmp_path / "events.jsonl", [started, *actor_events])
        surface = "host.json"
    controls = {
        "project_verified": True,
        "status": "observed",
        "model": "trial-model",
        "effort": "medium",
    }
    (tmp_path / "host.json").write_text(json.dumps(controls))
    (tmp_path / surface).write_text(json.dumps(controls))
    write_rows(
        tmp_path / "mcp-1.jsonl",
        [
            {
                "event": "request",
                "sequence": 1,
                "route": "read_console",
                "actor": actor,
                "channel": "unity",
                "page": 0,
            },
            {"event": "response", "sequence": 1, "outcome": "returned", "observation": facts},
        ],
    )
    report = score_case(tmp_path)
    assert report["fixture_availability"] == "positive"
    assert report["final_answer"]["status"] == "correct" and report["live_evidence"] == "complete"
    assert report["first_successful_suitable_call"] == "read_console"
    for field, value, issue in (
        ("effort", "max", "effort"),
        ("model", None, "model"),
        ("status", "incomplete", "catalog"),
        ("project_verified", False, "project"),
    ):
        (tmp_path / surface).write_text(json.dumps({**controls, field: value}))
        incomplete = score_case(tmp_path)["final_answer"]
        assert (
            incomplete["status"] == "needs_review"
            and issue in incomplete["unverified_controls"][role]
        )
    (tmp_path / surface).write_text(json.dumps(controls))
    if role == "subagent":
        (tmp_path / "host.json").write_text(json.dumps({**controls, "status": "incomplete"}))
        assert score_case(tmp_path)["final_answer"]["unverified_controls"] == {
            "parent": ["catalog"]
        }
        (tmp_path / "host.json").write_text(json.dumps(controls))
    if role == "main":
        # Keep the same actor, oracle, text and matching messages. An incomplete
        # or contradictory page cannot establish those messages were the full set.
        page = {"cursor": 0, "pageSize": 2, "items": REPLY["data"], "total": 3, "nextCursor": "2"}
        args = {**ARGS, "cursor": 0, "page_size": 2}
        path = tmp_path / "mcp-1.jsonl"
        request, reply = [json.loads(line) for line in path.read_text().splitlines()]
        for invalid in (
            page,
            {**page, "truncated": False},
            {**page, "truncated": False, "total": 2},
            {**page, "truncated": False, "nextCursor": None},
            {**page, "truncated": True, "nextCursor": "1"},
            {**page, "truncated": False, "nextCursor": None, "total": 2, "cursor": 1},
            {"items": REPLY["data"]},
            REPLY["data"],
        ):
            status, data = unpack(
                {"result": {"structuredContent": {"success": True, "data": invalid}}},
                "read_console",
            )
            failed = observation("read_console", data, FIXTURE, arguments=args)
            write_rows(path, [request, {**reply, "outcome": status, "observation": failed}])
            assert score_case(tmp_path)["final_answer"]["status"] != "correct"
        terminal = {**page, "total": 2, "truncated": False, "nextCursor": None}
        status, data = unpack(
            {"result": {"structuredContent": {"success": True, "data": terminal}}}, "read_console"
        )
        good_page = observation("read_console", data, FIXTURE, arguments=args)
        write_rows(path, [request, {**reply, "outcome": status, "observation": good_page}])
        assert score_case(tmp_path)["final_answer"]["status"] == "correct"
        write_rows(path, [request, reply])
    if role == "subagent":
        write_rows(
            tmp_path / "events.jsonl",
            [started, {"type": "host.child_linked", "child_actor": actor}, answer, done],
        )
        write_rows(tmp_path / f"child-{actor}.jsonl", [actor_events[0], done])
        assert score_case(tmp_path)["final_answer"]["status"] == "missing"
    else:
        (tmp_path / "case.json").write_text(
            json.dumps(
                {
                    **case,
                    "editor_end_state": {
                        "observation_fixtures": {console.TASK: {**ROWS, "warnings": []}}
                    },
                }
            )
        )
        result = score_case(tmp_path)["final_answer"]
        assert result["status"] == "incorrect" and result["retained"] is False


def test_oracle_and_manual_answer_review_cannot_replace_actor_receipt(tmp_path):
    assert fixture_availability(FIXTURE, {}) == "unverified"
    assert (
        fixture_availability(
            FIXTURE, {"observation_fixtures": {console.TASK: {**ROWS, "warnings": []}}}
        )
        == "unavailable"
    )
    args = (
        tmp_path,
        {"editor_start_state": STATE, "editor_end_state": STATE},
        [],
        "Here are the messages:\n" + "\n".join(ROWS["errors"]),
    )
    flags = dict(actor_verified=True, project_verified=True, scoped=True)
    report = console.outcome(*args, **flags)
    (tmp_path / "answer-review.json").write_text(
        json.dumps(
            {
                "review_digest": report["review_digest"],
                "status": "correct",
                "reason": "All and only error messages were reported.",
            }
        )
    )
    result = console.outcome(*args, **flags)
    assert result["answer_match"] == "correct" and result["status"] == "needs_review"
    assert console.answer_match("\n".join(ROWS["errors"] + ROWS["warnings"]), ROWS) == "incorrect"
