"""Replay deterministic evidence selection through an installed Server catalog without Unity."""

import argparse
import asyncio
import json
import os
from pathlib import Path
from urllib.parse import quote

from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.resources.base import ResourceContent, ResourceResult
from fastmcp.server.middleware import Middleware
from fastmcp.tools.base import ToolResult
from services.registry.observation_predicates import evaluate_observation
from services.resources import register_all_resources
from services.tools import register_all_tools

GRAPH = "Assets/Fixture.shadergraph"
JOB = "a" * 32
TASKS = {
    "saved": {
        "en": f"How many nodes and edges are saved in {GRAPH} on disk?",
        "ko": f"디스크에 저장된 {GRAPH}의 노드와 연결선 수를 확인해 주세요.",
        "source": "asset_file",
        "values": ["nodeCount", "edgeCount"],
    },
    "current": {
        "en": "What scene is currently active in the Unity Editor?",
        "ko": "현재 Unity 에디터에서 활성화된 씬이 뭔지 확인해 주세요.",
        "source": "editor_live",
        "values": ["active_scene.name"],
    },
    "runtime": {
        "en": f"Did the existing run {JOB} observe readiness after the click in Play Mode?",
        "ko": f"기존 Play Mode 실행 {JOB}에서 클릭 후 준비 상태가 관찰됐나요?",
        "source": "scenario_job",
        "values": ["scenario_job.outcome"],
    },
}
REPLIES = {
    "saved": {"nodeCount": 3, "edgeCount": 2, "data_state": "saved"},
    "current": {"name": "CurrentFixture", "path": "Assets/CurrentFixture.unity", "isLoaded": True},
    "runtime": {
        "job_id": JOB,
        "execution_environment": "editor",
        "status": "succeeded",
        "started_unix_ms": 10,
        "finished_unix_ms": 15,
        "steps": [
            {"action": "click_ui", "status": "passed", "detail": "raycast click"},
            {"action": "wait_state", "status": "passed", "detail": "fixture.ready=true"},
        ],
    },
}
READS = {
    ("inspect_shader_graph", None): "saved",
    ("manage_shader", "inspect_graph"): "saved",
    ("inspect_editor_state", None): "current",
    ("scene_inspect", "get_active"): "current",
    ("manage_scene", "get_active"): "current",
    ("manage_play_scenario", "status"): "runtime",
}
WRITES = {
    ("manage_play_scenario", "run"),
    ("manage_input", "ui_click"),
    ("manage_scene", "load"),
    ("manage_editor", "play"),
    ("manage_shader", "create"),
}
GRAPH_URI = "mcpforunity://shader/graph/" + quote(GRAPH, safe="")
EDITOR_SNAPSHOT = {
    "editor": {"active_scene": REPLIES["current"]},
    "staleness": {"is_stale": False},
}


class ObservationFixture(Middleware):
    """Return owned simulation fixtures and enforce the observed host's grant/surfaces."""

    def __init__(self, resources: bool):
        self.resources = resources
        self.calls = []

    async def on_call_tool(self, context, call_next):
        tool = context.message.name
        args = context.message.arguments
        operation = args.get("action")
        entry = (tool, operation)
        call = {
            "binding": ".".join(part for part in entry if part),
            "attempted_write": entry in WRITES,
        }
        self.calls.append(call)
        if tool == "search_unity_tools":
            return await call_next(context)
        if entry in READS and (
            (READS[entry] == "saved" and args.get("path") == GRAPH)
            or READS[entry] == "current"
            or (READS[entry] == "runtime" and args.get("job_id") == JOB)
        ):
            data = EDITOR_SNAPSHOT if tool == "inspect_editor_state" else REPLIES[READS[entry]]
            return ToolResult(structured_content={"success": True, "data": data})
        raise ToolError(f"Outside the read-only replay fixture: {call['binding']}.")

    async def on_read_resource(self, context, call_next):
        uri = str(context.message.uri)
        self.calls.append({"binding": uri, "attempted_write": False})
        if self.resources and uri == GRAPH_URI:
            return ResourceResult(
                [ResourceContent(json.dumps({"success": True, "data": REPLIES["saved"]}))]
            )
        raise ToolError("Resource not available in this host fixture.")


def binding_for(payload, resource_capable, task):
    """Prefer the matching saved resource on hosts that can read it."""
    for group in payload.get("route_groups", []):
        candidates = [group["representative"], *group["alternatives"]]
        if task == "saved" and resource_capable:
            for candidate in candidates:
                if candidate["kind"] == "resource_template":
                    return candidate
        if group["representative"]["kind"] == "tool":
            return group["representative"]
    raise LookupError("No admitted evidence binding.")


async def measured_case(client, fixture, task, language):
    selection = TASKS[task]
    request = {
        "query": selection[language],
        "route_surfaces": "both" if fixture.resources else "tools",
        "intent": "observe",
    }
    try:
        response = (
            await client.call_tool(
                "search_unity_tools",
                {
                    **request,
                    "observation_source": selection["source"],
                    "required_values": selection["values"],
                },
            )
        ).structured_content
    except ToolError:
        # The same policy recovers when an older schema has no scenario-job selector.
        if task != "runtime":
            raise
        response = (
            await client.call_tool(
                "search_unity_tools",
                {**request, "catalog_query": "manage_play_scenario status", "limit": 20},
            )
        ).structured_content
        response = {
            **response,
            "route_groups": [
                group
                for group in response["route_groups"]
                if group["representative"].get("tool") == "manage_play_scenario"
                and group["representative"].get("operation") == "status"
            ],
        }
    route = binding_for(response, fixture.resources, task)
    if route["kind"] == "resource_template":
        resources = await client.read_resource(GRAPH_URI)
        reply = json.loads(resources[0].text)
    else:
        parameters = route.get("arguments", {})
        if route.get("operation"):
            parameters["action"] = route["operation"]
        if task == "saved":
            parameters["path"] = GRAPH
        elif task == "runtime":
            parameters["job_id"] = JOB
        reply = (await client.call_tool(route["tool"], parameters)).structured_content
    declaration = route.get("observation")
    complete = (
        reply["success"] is True
        and reply["data"]
        == (EDITOR_SNAPSHOT if route.get("tool") == "inspect_editor_state" else REPLIES[task])
        and (
            not declaration
            or all(evaluate_observation(declaration, reply, selection["values"]).values())
        )
    )
    return {
        "task": task,
        "language": language,
        "host": request["route_surfaces"],
        "wording": request["query"],
        "evidence_source": selection["source"] if complete else None,
        "calls_to_first_usable": len(fixture.calls) if complete else None,
        "excess_calls_over_one_lookup_one_read": max(0, len(fixture.calls) - 2),
        "attempted_writes": sum(call["attempted_write"] for call in fixture.calls),
        "calls": fixture.calls,
    }


async def replay():
    os.environ["UNITY_MCP_TOOL_PROFILE"] = "full"
    results = []
    for resources in (False, True):
        fixture = ObservationFixture(resources)
        server = FastMCP("discovery-observation-replay")
        register_all_tools(server, project_scoped_tools=False)
        register_all_resources(server, project_scoped_tools=False)
        server.add_middleware(fixture)
        async with Client(server) as client:
            for language in ("en", "ko"):
                for task in TASKS:
                    fixture.calls = []
                    results.append(await measured_case(client, fixture, task, language))
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", required=True, help="Loaded baseline commit or candidate identity"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    trials = asyncio.run(replay())
    with args.output.open("x", encoding="utf-8") as file:
        json.dump(
            {
                "kind": "deterministic_mcp_replay",
                "source_identity": args.source,
                "read_grant": True,
                "model": None,
                "editor": None,
                "runtime_fixture": "recorded synthetic result",
                "trials": trials,
            },
            file,
            ensure_ascii=False,
            indent=2,
        )
        file.write("\n")
    return int(any(case["calls_to_first_usable"] is None for case in trials))


if __name__ == "__main__":
    raise SystemExit(main())
