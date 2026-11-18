"""Exercise blind task framing, the read-only wire, and outcome scoring offline."""

import json
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ResourceError, ToolError
from mcp_types import CallToolResult, ReadResourceResult, TextResourceContents

EXPERIMENTS = Path(__file__).resolve().parents[1] / "experiments"
sys.path.insert(0, str(EXPERIMENTS.parents[1]))

from tools.experiments.blind_tool_http import (
    ScopedObservation,
    create_observation_proxy,
    loopback_port,
)
from tools.experiments.blind_tool_selection import LANGUAGES, TASKS, main, task_prompt
from tools.experiments.blind_tool_trace import (
    GAME_VIEW,
    GAME_VIEW_RESOURCE,
    GRAPH,
    GRAPH_PREFIX,
    GRAPH_RESOURCE,
    RAYCAST,
    call_actor,
    decision,
    has_saved_read,
    observation,
    route_name,
    saved_read_grade,
    score_case,
    score_directory,
    unpack,
)

ASSET = "Assets/Examples/Fixture With Spaces.shadergraph"


def write_rows(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def request(sequence, route, channel="unity", *, page=1):
    return {
        "event": "request",
        "sequence": sequence,
        "route": route,
        "channel": channel,
        "page": page,
    }


def response(sequence, outcome="returned", **fields):
    return {"event": "response", "sequence": sequence, "outcome": outcome, "observation": fields}


def trial(path, task, *, role="main", audit=(), host=()):
    directory = path / f"{task}-ko-{role}-01"
    directory.mkdir()
    (directory / "case.json").write_text(
        json.dumps(
            {
                "task": task,
                "role": role,
                "language": "ko",
                "repetition": 1,
                "graph_asset": ASSET,
                "runner_status": "finished",
            }
        ),
        encoding="utf-8",
    )
    write_rows(directory / "events.jsonl", host)
    write_rows(directory / "mcp-1.jsonl", audit)
    return directory


@pytest.mark.parametrize("task", TASKS)
@pytest.mark.parametrize("language", LANGUAGES)
def test_blind_prompt_translates_the_same_task_without_tool_identifiers(task, language):
    prompt = task_prompt(task, language, ASSET)
    assert "Assets/Resources/GameData" in prompt
    assert (ASSET in prompt) == task.startswith("graph")
    assert not any(
        token in prompt
        for token in (
            GRAPH,
            GRAPH_PREFIX,
            GAME_VIEW,
            GAME_VIEW_RESOURCE,
            RAYCAST,
            "manage_tools",
            "mcpforunity://",
        )
    )


def test_resources_require_exact_single_encoded_asset_and_observation_only_arguments():
    asset_uri = f"{GRAPH_PREFIX}{quote(ASSET, safe='')}"
    valid = [
        (asset_uri, GRAPH_RESOURCE),
        (f"{asset_uri}?page_size=10&page_number=2", GRAPH_RESOURCE),
        (GAME_VIEW_RESOURCE, GAME_VIEW_RESOURCE),
    ]
    for uri, expected_route in valid:
        route, arguments = route_name("resources/read", {"uri": uri})
        assert route == expected_route
        assert decision(route, arguments, ASSET) == "unity"
    for uri in (
        f"{GRAPH_PREFIX}{ASSET}",
        f"{asset_uri}?evaluate_getters=true",
        f"{asset_uri}?page_size=1&page_size=2",
        f"{asset_uri}#bad",
        f"{GRAPH_PREFIX}Assets%2FResources%2FGameData%2Fsecret.shadergraph",
        f"{GAME_VIEW_RESOURCE}?allow_custom_filters=true",
    ):
        route, arguments = route_name("resources/read", {"uri": uri})
        assert decision(route, arguments, ASSET) == "blocked"
    assert decision(RAYCAST, {"allow_custom_filters": True}, ASSET) == "blocked"
    assert decision("manage_editor.play", {}, ASSET) == "blocked"
    assert decision(GRAPH, {"path": "Assets/Wrong.shadergraph"}, ASSET) == "blocked"


def test_untranslated_task_search_can_pass_the_observation_guard():
    for arguments in ({"query": "現在のゲームビューのサイズ"}, {"intent": "현재 게임 화면 크기"}):
        route, params = route_name(
            "tools/call", {"name": "manage_tools", "arguments": {"action": "search", **arguments}}
        )
        assert decision(route, params, ASSET) == "discovery"


@pytest.mark.parametrize(
    "url",
    (
        "https://127.0.0.1:8080/mcp",
        "http://example.com/mcp",
        "http://127.0.0.1@evil.invalid/mcp",
        "http://127.0.0.1/mcp?token=sensitive",
        "http://localhost/mcp#fragment",
    ),
)
def test_local_auth_does_not_allow_outside_or_credential_bearing_endpoint(url):
    with pytest.raises(ValueError, match="loopback"):
        loopback_port(url)
    assert loopback_port("http://127.0.0.1:8080/mcp") == ("127.0.0.1", 8080)


@pytest.mark.asyncio
async def test_http_adapter_verifies_target_and_disables_find_refresh(tmp_path):
    instance = "Fixture@0123456789abcdef"
    forwarded = []

    class Backend:
        @asynccontextmanager
        async def new_stateful(self):
            yield self

        async def read_resource_mcp(self, uri, meta):
            forwarded.append((uri, meta))
            value = {"success": True, "data": {"projectRoot": str(tmp_path)}}
            return ReadResourceResult(
                contents=[TextResourceContents(uri=uri, text=json.dumps(value))]
            )

        async def call_tool_mcp(self, name, arguments):
            forwarded.append((name, arguments))
            return CallToolResult(content=[], structuredContent={"success": True})

    context = SimpleNamespace(
        message=SimpleNamespace(name="find_gameobjects", arguments={"refresh_if_dirty": True})
    )
    blocked = ScopedObservation(Backend(), instance, tmp_path / "other", ASSET)
    with pytest.raises(ResourceError, match="different project root"):
        await blocked.on_call_tool(context, None)
    assert len(forwarded) == 1

    middleware = ScopedObservation(Backend(), instance, tmp_path, ASSET)
    result = await middleware.on_call_tool(context, None)
    assert result.structured_content == {"success": True}
    assert forwarded[-1] == (
        "find_gameobjects",
        {"refresh_if_dirty": False, "unity_instance": instance},
    )

    context.message.arguments["unity_instance"] = "Other@fedcba9876543210"
    with pytest.raises(ToolError, match="Outside this project"):
        await middleware.on_call_tool(context, None)
    assert len(forwarded) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "action"),
    [
        ("manage_scene", "get_active"),
        ("scene_inspect", "get_hierarchy"),
        ("asset_inspect", "get_info"),
        ("manage_shader", "read"),
        ("gameobject_inspect", "get_components"),
        ("manage_ugui", "diagnose_raycast"),
    ],
)
async def test_bundled_adapter_blocks_refresh_capable_paths(tmp_path, name, action):
    instance = "Fixture@0123456789abcdef"
    transport = StreamableHttpTransport("http://127.0.0.1:1/mcp")
    proxy = create_observation_proxy(transport, instance, tmp_path, ASSET)
    assert isinstance(proxy.middleware[-1], ScopedObservation)
    middleware = ScopedObservation(None, instance, tmp_path, ASSET)
    context = SimpleNamespace(message=SimpleNamespace(name=name, arguments={"action": action}))
    with pytest.raises(ToolError, match="Outside this project"):
        await middleware.on_call_tool(context, None)


def test_graph_counts_pagination_and_application_failure_do_not_inflate_evidence(tmp_path):
    case = trial(
        tmp_path,
        "graph",
        audit=[
            request(1, "manage_tools.search", "discovery"),
            response(1),
            request(2, "find_gameobjects"),
            response(2),
            request(3, GRAPH),
            response(3, "error"),
            request(4, GRAPH_RESOURCE),
            response(4, nodes=6, edges=4, node_types=["FixtureNode"]),
            request(5, GRAPH_RESOURCE, page=2),
            response(5, nodes=6, edges=4, node_types=["SecondNode"]),
        ],
    )
    report = score_case(case)
    assert report["first_business_call"] == "find_gameobjects"
    assert report["first_suitable_business_call"] == GRAPH
    assert report["calls_before_first_suitable"] == 1
    assert report["suitable_attempts"] == 3
    assert report["repeat_suitable_calls"] == 1
    assert report["retry_suitable_calls"] == 1
    assert report["unity_call_errors"] == 1
    assert report["discovery_successes"] == 1
    assert report["live_evidence"] == "complete"


def test_failed_response_and_approval_block_are_independent_of_selection(tmp_path):
    failed = {"result": {"structuredContent": {"success": False, "data": {"nodeCount": 3}}}}
    assert unpack(failed) == ("error", {})
    assert unpack({"result": {"isError": True, "content": [{"type": "text", "text": "ok"}]}}) == (
        "error",
        {},
    )
    assert observation(GRAPH, {"nodeCount": 3}) == {}
    host = {
        "type": "item.completed",
        "item": {
            "id": "item-1",
            "type": "mcp_tool_call",
            "server": "unityMCP",
            "tool": "manage_shader",
            "arguments": {"action": "inspect_graph", "path": ASSET},
            "status": "failed",
            "error": {"message": "MCP tool call requires approval, but approval policy is never"},
        },
    }
    case = trial(
        tmp_path,
        "graph",
        host=[host],
        audit=[
            request(1, "manage_tools.search", "discovery"),
            response(1),
        ],
    )
    report = score_case(case)
    assert report["first_business_call_suitable"] is True
    assert report["approval_blocks"] == 1
    assert report["unity_call_errors"] == 0
    assert report["live_evidence"] == "none"


def test_guard_rejection_is_counted_once_before_suitable_call(tmp_path):
    host = {
        "type": "item.completed",
        "item": {
            "id": "host-guard",
            "type": "mcp_tool_call",
            "server": "unityMCP",
            "tool": "manage_editor",
            "arguments": {"action": "play"},
            "status": "failed",
            "error": {"message": "Outside observation scope"},
        },
    }
    case = trial(
        tmp_path,
        "graph",
        host=[host],
        audit=[
            request(1, "unapproved_route", "blocked"),
            response(1, "error"),
            request(2, GRAPH),
            response(2, nodes=2, edges=1, node_types=["FixtureNode", "OtherNode"]),
        ],
    )
    report = score_case(case)
    assert report["first_business_call"] == "unapproved_route"
    assert report["first_suitable_business_call"] == GRAPH
    assert report["calls_before_first_suitable"] == 1
    assert report["observation_guard_blocks"] == 1
    assert report["live_evidence"] == "complete"


def test_host_only_block_and_wire_only_success_are_both_counted(tmp_path):
    host = {
        "type": "item.completed",
        "item": {
            "id": "host-approval",
            "type": "mcp_tool_call",
            "server": "unityMCP",
            "tool": "manage_shader",
            "arguments": {"action": "inspect_graph", "path": ASSET},
            "status": "failed",
            "error": {"message": "MCP tool call requires approval, but approval policy is never"},
        },
    }
    case = trial(
        tmp_path,
        "graph",
        host=[host],
        audit=[
            request(1, GRAPH_RESOURCE),
            response(1, nodes=6, edges=4, node_types=["FixtureNode", "OtherNode"]),
        ],
    )
    report = score_case(case)
    assert report["first_suitable_business_call"] == GRAPH
    assert report["suitable_attempts"] == 2
    assert report["retry_suitable_calls"] == 1
    assert report["approval_blocks"] == 1
    assert report["live_evidence"] == "complete"
    resource = {
        "type": "item.completed",
        "item": {
            "id": "host-resource",
            "type": "mcp_tool_call",
            "server": "unityMCP",
            "tool": "read_mcp_resource",
            "arguments": {"uri": GRAPH_PREFIX + quote(ASSET, safe="")},
            "status": "completed",
        },
    }
    write_rows(case / "events.jsonl", [host, resource])
    assert score_case(case)["suitable_attempts"] == 2


@pytest.mark.parametrize(
    ("native", "grade"),
    [
        ({"render_size": {"width": 1280, "height": 0}}, "none"),
        ({"current_size": {"width": 1280, "height": 720}}, "partial"),
        ({"render_size": {"width": 1280, "height": 720}}, "complete"),
    ],
)
def test_game_view_uses_rendered_size_for_complete_evidence(tmp_path, native, grade):
    detail = observation(GAME_VIEW_RESOURCE, native)
    report = score_case(
        trial(
            tmp_path,
            "game-view",
            audit=[
                request(1, GAME_VIEW_RESOURCE),
                response(1, **detail),
            ],
        )
    )
    assert report["live_evidence"] == grade


@pytest.mark.parametrize(
    ("position", "status", "expected"),
    [
        ([640, 360], "observed", "complete"),
        ([640, 359], "observed", "partial"),
        ([640, 360], "unknown", "partial"),
    ],
)
def test_raycast_requires_observation_at_render_center(tmp_path, position, status, expected):
    detail = observation(
        RAYCAST,
        {
            "kind": "ugui_raycast_diagnostics",
            "status": status,
            "screen_position": position,
            "hits_total": 0,
            "hits": [],
            "reason": None,
            "policy": {
                "package_explicit_event_dispatch": False,
                "user_code_may_have_run": False,
                "native_query_attempted": True,
            },
            "input": {"delivery": "not_attempted"},
        },
    )
    case = trial(
        tmp_path,
        "raycast",
        audit=[
            request(1, GAME_VIEW_RESOURCE),
            response(1, render_size=[1280, 720]),
            request(2, RAYCAST),
            response(2, **detail),
        ],
    )
    report = score_case(case)
    assert report["live_evidence"] == expected
    assert report["live_observations"][-1]["delivery"] == "not_attempted"


def test_child_evidence_cannot_be_inferred_from_parent_answer(tmp_path):
    audit = [request(1, GAME_VIEW), response(1, render_size=[800, 600])]
    case = trial(tmp_path, "game-view", role="subagent", audit=audit)
    (case / "answer.txt").write_text("The subagent says it observed 800x600.", encoding="utf-8")
    assert score_case(case)["live_evidence"] == "unattributed"
    spawn = {
        "type": "item.completed",
        "item": {
            "type": "collab_tool_call",
            "tool": "spawn_agent",
            "status": "completed",
            "receiver_thread_ids": ["01800000-0000-4000-8100-000000000001"],
        },
    }
    write_rows(case / "events.jsonl", [spawn])
    assert score_case(case)["live_evidence"] == "complete"
    write_rows(
        case / "events.jsonl",
        [spawn, {"type": "item.completed", "item": {"type": "command_execution", "exit_code": 0}}],
    )
    assert score_case(case)["role_status"] == "mixed"
    assert score_case(case)["live_evidence"] == "unattributed"


def test_native_wait_without_spawn_receipt_leaves_actor_inference_explicit(tmp_path):
    parent = "01800000-0000-4000-8100-000000000001"
    child = "01800000-0000-4000-8100-000000000002"
    actor = call_actor({"_meta": {"x-codex-turn-metadata": json.dumps({"thread_id": child})}})
    assert actor and child not in actor
    assert call_actor({"_meta": {"threadId": child}}) == actor
    assert not call_actor(
        {"_meta": {"threadId": parent, "x-codex-turn-metadata": {"thread_id": child}}}
    )
    events = [
        {"type": "thread.started", "thread_id": parent},
        {
            "type": "item.completed",
            "item": {"type": "collab_tool_call", "tool": "wait", "status": "completed"},
        },
    ]
    audit = [request(1, GAME_VIEW) | {"actor": actor}, response(1, render_size=[800, 600])]
    case = trial(tmp_path, "game-view", role="subagent", host=events, audit=audit)
    write_rows(case / "mcp-2.jsonl", [request(1, "initialize", "discovery"), response(1)])
    report = score_case(case)
    assert report["role_status"] == "inferred"
    assert report["role_attribution"] == "distinct-mcp-actor-and-native-wait"
    assert report["live_evidence"] == "unattributed"
    assert report["wire_evidence"] == "complete"


def saved_graph_output():
    return "\n\n".join(
        map(
            json.dumps,
            (
                {"m_Nodes": [{"m_Id": "one"}, {"m_Id": "two"}], "m_Edges": []},
                {"m_ObjectId": "one", "m_Type": "FixtureNode"},
                {"m_ObjectId": "two", "m_Type": "SecondNode"},
            ),
        )
    )


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ('Get-Content -Raw -LiteralPath "{asset}"', "observed"),
        ('Get-Content -Raw -Path "{asset}"', "review"),
        ('Get-Content -Raw "{asset}"', "review"),
        ('cat "{asset}"', "review"),
    ],
)
def test_wildcard_paths_need_literal_powershell_read(command, expected):
    asset = "Assets/Examples/[AB].shadergraph"
    assert saved_read_grade(command.format(asset=asset), saved_graph_output(), asset) == expected


def test_saved_graph_evidence_does_not_count_as_live_graph(tmp_path):
    command = f'Get-Content -Raw -LiteralPath "{ASSET}"'
    output = saved_graph_output()
    assert has_saved_read(command, output, ASSET)
    assert not has_saved_read(f'Test-Path "{ASSET}"', "True", ASSET)
    assert not has_saved_read(command, "file exists", ASSET)
    assert not has_saved_read(f'Get-Content "{ASSET}.backup"', output, ASSET)
    assert not has_saved_read(f'echo "{ASSET}"', output, ASSET)
    assert not has_saved_read(f'{command}; echo "{ASSET}"', output, ASSET)
    assert not has_saved_read(command, "m_Nodes m_Edges FakeNode OtherNode", ASSET)
    host = [
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "exit_code": 0,
                "command": command,
                "aggregated_output": output,
            },
        }
    ]
    for task in ("graph-saved", "graph"):
        result = score_case(trial(tmp_path, task, host=host))
        assert result["saved_graph_evidence"] == "observed"
        assert result["live_evidence"] == "none"


def test_missing_matrix_cell_is_visible_and_scores_nonzero(tmp_path):
    trial(tmp_path, "context")
    cells = [["context", "ko", "main", 1], ["context", "ko", "subagent", 1]]
    (tmp_path / "matrix.json").write_text(json.dumps(cells), encoding="utf-8")
    report = score_directory(tmp_path)
    assert report["observed_cases"] == 1
    assert report["unrun_cases"] == [cells[-1]]
    assert main(["score", str(tmp_path)]) == 1


def test_wire_records_real_results_and_never_forwards_mutation(tmp_path):
    echo = tmp_path / "stdio.py"
    echo.write_text(
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    row = json.loads(line)\n"
        "    assert row['params']['arguments']['action'] == 'get_game_view_size'\n"
        "    value = {'success': True, 'data': {'render_size': {'width': 800, 'height': 600}}}\n"
        "    print(json.dumps({'jsonrpc':'2.0', 'id':row['id'], 'result':{'structuredContent':value}}), flush=True)\n",
        encoding="utf-8",
    )
    payload = "".join(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": number,
                "method": "tools/call",
                "params": {"name": "manage_editor", "arguments": {"action": action}},
            }
        )
        + "\n"
        for number, action in enumerate(("play", "get_game_view_size"), start=1)
    )
    process = subprocess.run(
        [
            sys.executable,
            str(EXPERIMENTS / "blind_tool_trace.py"),
            "--relay",
            str(echo),
            "--audit-dir",
            str(tmp_path),
            "--graph-asset",
            ASSET,
        ],
        input=payload,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    observed = {row["id"]: row for row in map(json.loads, process.stdout.splitlines())}
    assert "error" in observed[1]
    assert "result" in observed[2]
    traces = list(tmp_path.glob("mcp-*.jsonl"))
    assert len(traces) == 1
    audit = list(map(json.loads, traces[0].read_text(encoding="utf-8").splitlines()))
    assert audit[0]["channel"] == "blocked"
    assert audit[1] == {"event": "response", "sequence": 1, "outcome": "error"}
    assert audit[-1]["observation"] == {"render_size": [800, 600]}
