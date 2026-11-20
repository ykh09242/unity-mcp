"""Evidence for child-owned observations and first-search/availability diagnoses."""

import asyncio
import json
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.experiments.blind_tool_diagnostics import search_receipt, stage_diagnosis
from tools.experiments.blind_tool_host import collect_children, direct_child
from tools.experiments.blind_tool_trace import Call, ClientTrace, role_status, thread_digest


def test_first_search_logs_only_returned_ranked_bindings():
    saved = {"kind": "tool", "tool": "inspect_shader_graph", "purpose": "private content"}
    receipt = search_receipt(
        {
            "routes": [saved],
            "matching": "observation",
            "total_route_groups": 2,
            "route_groups": [{"representative": saved, "alternatives": []}],
            "query": "private request",
        }
    )
    assert receipt == {
        "matching": "observation",
        "grouped": True,
        "top3": [["inspect_shader_graph"]],
        "flat_top3": ["inspect_shader_graph"],
        "distinct_groups": 2,
    }
    assert "private" not in json.dumps(receipt)


def test_failure_stage_requires_known_advertisement_and_observed_actor():
    routes = {"query.hit"}
    surface = {"status": "observed", "unity": {"tools": ["query"], "actions": {"query": ["hit"]}}}
    fallback = [Call("shell.command", "shell", 1)]

    def stage(catalog, calls, confirmed=True):
        return stage_diagnosis(routes, catalog, calls, [], role_confirmed=confirmed, grade="none")[
            "stage"
        ]

    assert stage(surface, fallback) == "advertised_different_path"
    assert stage({"status": "incomplete"}, fallback) == "unknown"
    assert stage({"status": "observed", "unity": {"tools": []}}, fallback) == "unadvertised"
    assert (
        stage(surface, [Call("query.hit", "unity", 1, status="approval_blocked")])
        == "permission_blocked"
    )
    assert (
        stage(surface, [Call("query.hit", "unity", 1, evidence={"status": "unknown"})])
        == "native_unavailable"
    )
    assert stage(surface, fallback, False) == "actor_unknown"


def test_pure_search_is_discovery_and_its_rank_belongs_to_the_calling_actor():
    from tools.experiments.blind_tool_diagnostics import actor_searches
    from tools.experiments.blind_tool_trace import decision, observation

    route = "search_unity_tools"
    assert decision(route, {}, "", endpoint=True) == "discovery"
    assert decision(route + ".activate", {}, "", endpoint=True) == "blocked"
    call = Call(
        route,
        "discovery",
        1,
        actor="child",
        evidence=observation(route, {"routes": [{"kind": "tool", "tool": "get_game_view_size"}]}),
    )
    assert not actor_searches(None, [call], "parent", True)
    result = stage_diagnosis(
        {"get_game_view_size"},
        {},
        [],
        actor_searches(None, [call], "child", True),
        role_confirmed=True,
        grade="none",
    )
    assert result["first_search_top3_suitable"] is True


def test_only_parent_bound_children_can_supply_completed_host_items(tmp_path):
    parent, child, unrelated = [str(uuid4()) for _ in range(3)]
    assert direct_child({"id": child, "parentThreadId": parent}, parent) == child
    assert direct_child({"id": child, "parentThreadId": unrelated}, parent) is None

    class Client:
        async def request(self, method, params):
            assert method == "thread/turns/list"
            assert params["threadId"] == child
            return {
                "data": [
                    {
                        "status": "completed",
                        "itemsView": "full",
                        "items": [{"id": "1", "type": "commandExecution", "exitCode": 0}],
                    }
                ]
            }

    events = []
    asyncio.run(collect_children(Client(), {child}, {}, events.append, tmp_path, "unityMCP", ""))
    assert events == [{"type": "host.child_linked", "child_actor": thread_digest(child)}]
    assert len(list(tmp_path.glob("child-*.jsonl"))) == 1
    trace = ClientTrace(child_actors={thread_digest(child)})
    observed = ClientTrace(calls={"1": Call("get_game_view_size", "unity", 1, status="returned")})
    assert (
        role_status(
            "subagent",
            trace,
            [[Call("get_game_view_size", "unity", 1, status="returned")]],
            observed,
        )[0]
        == "confirmed"
    )
    assert role_status("subagent", trace, [[Call("other", "unity", 1)]], observed)[0] != "confirmed"
    observed.calls["1"].status = "approval_blocked"
    assert (
        role_status(
            "subagent",
            trace,
            [[Call("get_game_view_size", "unity", 1, status="returned")]],
            observed,
        )[0]
        != "confirmed"
    )


def test_resource_template_scope_includes_optional_pagination():
    from tools.experiments.blind_tool_diagnostics import advertised
    from tools.experiments.blind_tool_trace import GRAPH_RESOURCE

    uri = GRAPH_RESOURCE + "{?page_size,page_number}"
    surface = {"status": "observed", "unity": {"resources": [], "templates": [uri]}}
    assert advertised(surface, GRAPH_RESOURCE) is True
    assert search_receipt({"routes": [{"kind": "resource_template", "uri_template": uri}]})[
        "top3"
    ] == [[GRAPH_RESOURCE]]


def test_parent_rank_does_not_credit_a_confirmed_child():
    from tools.experiments.blind_tool_diagnostics import actor_discovery, actor_searches

    search = Call(
        "manage_tools.search", "discovery", 1, actor="parent", evidence={"top3": [["saved"]]}
    )
    assert not actor_searches(ClientTrace(), [search], "child", True)
    assert actor_discovery(None, [search], "child", True) is None
    assert (
        actor_discovery(ClientTrace(completed=True), [search], "child", True)["observed_calls"] == 0
    )
    result = stage_diagnosis(set(), {}, [], [search], role_confirmed=True, grade="partial")
    assert result["advertised"] is None and result["first_search_top3_suitable"] is False


def test_native_raycast_failure_keeps_reason_without_observation_credit():
    from tools.experiments.blind_tool_trace import RAYCAST, evidence_grade, observation, unpack

    status, data = unpack(
        {
            "result": {
                "structuredContent": {
                    "success": False,
                    "data": {
                        "kind": "ugui_raycast_diagnostics",
                        "status": "failed",
                        "reason": "native_query_threw",
                    },
                }
            }
        }
    )
    failed = Call(RAYCAST, "unity", 1, status=status, evidence=observation(RAYCAST, data))
    assert failed.evidence == {"status": "failed", "reason": "native_query_threw"}
    assert evidence_grade("raycast", [failed]) == "none"
    diagnosis = stage_diagnosis({RAYCAST}, {}, [failed], [], role_confirmed=True, grade="none")
    assert diagnosis["stage"] == "native_failed"
    assert diagnosis["native_results"] == [
        {"route": RAYCAST, "status": "failed", "reason": "native_query_threw"}
    ]
