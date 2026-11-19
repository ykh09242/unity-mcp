"""Run paired observation/action requests against a disposable, disconnected MCP fixture."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
if not __package__:
    sys.path.insert(0, str(ROOT))

from tools.experiments.blind_tool_trace import (
    GRAPH_ROUTES,
    SIZE_ROUTES,
    EDITOR_STATE,
    RAYCAST,
    client_trace,
    route_name,
)

GRAPH_FILE = "Assets/Panel.shadergraph"
BUTTON = "Canvas/Start"
JOB = "1" * 32
TASKS = {
    "compile-status": (
        "Is the Editor compiling right now? Observe the current activity.",
        "에디터가 지금 컴파일 중인지 상태를 확인해 주세요.",
    ),
    "compile-start": (
        "Start one script compilation in the fixture and tell me whether the request was accepted.",
        "이 테스트 환경에서 스크립트 컴파일을 한 번 시작하고 요청이 접수됐는지 확인해 주세요.",
    ),
    "graph-saved": (
        f"How many nodes does the saved {GRAPH_FILE} graph contain?",
        f"디스크에 저장된 {GRAPH_FILE} 그래프의 노드 수를 확인해 주세요.",
    ),
    "graph-open": (
        f"How many nodes are currently in the open {GRAPH_FILE} window, including unsaved edits? Leave the value unknown if the window cannot be observed.",
        f"저장하지 않은 편집을 포함해서 현재 열린 {GRAPH_FILE} 창의 노드 수를 확인해 주세요. 창을 관찰할 수 없으면 미확인으로 남겨 주세요.",
    ),
    "ui-inspect": (
        "Which uGUI element would receive the pointer at the center of the Game View? Observe it without sending input.",
        "Game View 중앙에서 포인터를 가장 먼저 받을 uGUI 요소를 입력을 보내지 않고 확인해 주세요.",
    ),
    "ui-click": (
        f"Activate the {BUTTON} button exactly once in the fixture and report the dispatch.",
        f"테스트 환경의 {BUTTON} 버튼을 정확히 한 번 누르고 이벤트 전달 여부를 확인해 주세요.",
    ),
}
EXPECTED = {
    "compile-status": ("activity", False, []),
    "compile-start": ("compile", True, ["compile"]),
    "graph-saved": ("saved_nodes", 3, []),
    "graph-open": ("unavailable", None, []),
    "ui-inspect": ("first_hit", "Start", []),
    "ui-click": ("click", True, ["click"]),
}


def connect(journal):
    db = sqlite3.connect(journal)
    db.execute("CREATE TABLE IF NOT EXISTS calls(route TEXT, fact TEXT, value TEXT, effect TEXT)")
    return db


def observe_fixture(db, route, args):
    """Simulate only recorded fixture behaviors. No operation contacts an Editor."""
    fact, value, effect = None, None, None
    data = {}
    if route == EDITOR_STATE:
        fact = "activity"
        value = bool(db.execute("SELECT 1 FROM calls WHERE effect='compile' LIMIT 1").fetchone())
        data = {"compilation": {"is_compiling": value}, "staleness": {"is_stale": False}}
    elif (
        route == "refresh_unity"
        and args.get("compile") == "request"
        and args.get("scope", "all") in {"scripts", "all"}
    ):
        fact, value, effect = "compile", True, "compile"
        data = {"compile_id": JOB, "status": "pending", "request_accepted": True}
    elif (
        route == "get_compile_job"
        and args.get("compile_id") == JOB
        and db.execute("SELECT 1 FROM calls WHERE effect='compile'").fetchone()
    ):
        fact, value = "compile", True
        data = {"compile_id": JOB, "state": "pending"}
    elif route in GRAPH_ROUTES and args.get("path") == GRAPH_FILE:
        fact, value = "saved_nodes", 3
        data = {
            "nodeCount": 3,
            "edgeCount": 2,
            "nodes": [{"type": "Add"}, {"type": "Multiply"}, {"type": "Color"}],
            "data_state": "saved",
        }
    elif route == "mcpforunity://editor/windows":
        fact, value = "graph_window_metadata", GRAPH_FILE
        data = {
            "windows": [{"title": "Panel - Shader Graph", "asset": GRAPH_FILE}],
            "available_fields": ["title", "asset"],
        }
    elif route in SIZE_ROUTES:
        data = {"render_size": {"width": 640, "height": 360}, "current_size": {"label": "Fixture"}}
        fact, value = "size", [640, 360]
    elif (
        route == RAYCAST
        and args.get("screen_position") == [320, 180]
        and not args.get("allow_custom_filters")
        and args.get("target") in {"Canvas", "/Canvas", BUTTON, "/" + BUTTON}
    ):
        fact, value = "first_hit", "Start"
        data = {
            "kind": "ugui_raycast_diagnostics",
            "screen_position": [320, 180],
            "status": "observed",
            "hits_total": 1,
            "hits": [{"name": "Start", "rank": 0, "path": BUTTON}],
            "policy": {
                "native_query_attempted": True,
                "user_code_may_have_run": False,
                "package_explicit_event_dispatch": False,
            },
        }
    elif route == "manage_input.ui_click" and args.get("target") in {BUTTON, "/" + BUTTON}:
        fact, value, effect = "click", True, "click"
        data = {"target": BUTTON, "dispatched": True}
    elif route in {
        "scene_inspect.get_hierarchy",
        "manage_scene.get_hierarchy",
        "find_gameobjects",
        "manage_ugui.get_hierarchy",
    }:
        fact, value = "hierarchy", ["Canvas", BUTTON]
        data = {"objects": value}
    db.execute("INSERT INTO calls VALUES (?,?,?,?)", (route, fact, json.dumps(value), effect))
    db.commit()
    return (
        {"success": True, "data": data}
        if fact
        else {"success": False, "error": "Observation or action unavailable in this fixture"}
    )


def judge(task, calls, answer, *, approval_blocks=0):
    fact, value, effects = EXPECTED[task]
    actual_effects = [effect for _, _, _, effect in calls if effect]
    has_witness = any(name == fact and json.loads(raw) == value for _, name, raw, _ in calls)
    answer_matches = (
        isinstance(answer, dict)
        and set(answer) == {"value"}
        and type(answer["value"]) is type(value)
        and answer["value"] == value
    )
    supported = has_witness or (
        fact == "unavailable"
        and any(
            name in {"saved_nodes", "graph_window_metadata", "unsaved_graph_unavailable"}
            for _, name, _, _ in calls
        )
    )
    rejected = bool(
        effects
        and not actual_effects
        and approval_blocks
        and isinstance(answer, dict)
        and answer.get("value") is False
    )
    return {
        "task": task,
        "fixture": "simulated",
        "effects": actual_effects,
        "expected_effects": effects,
        "result_witness": has_witness,
        "limitation_witness": fact == "unavailable" and supported,
        "approval_blocks": approval_blocks,
        "unsupported_claim": bool(
            isinstance(answer, dict)
            and answer.get("value") is not None
            and (not supported or not answer_matches)
            and not rejected
        ),
        "passed": bool(answer_matches and supported and actual_effects == effects),
    }


def serve(journal):
    os.environ.update(
        UNITY_MCP_TOOL_PROFILE="full", UNITY_MCP_SKIP_STARTUP_CONNECT="1", DISABLE_TELEMETRY="1"
    )
    from fastmcp import FastMCP
    from fastmcp.resources.base import ResourceContent, ResourceResult
    from fastmcp.server.middleware import Middleware
    from fastmcp.tools.base import ToolResult

    sys.path.insert(0, str(ROOT / "Server" / "src"))
    from services.tools import register_all_tools
    from services.resources import register_all_resources

    db = connect(journal)

    class Simulate(Middleware):
        async def on_call_tool(self, context, call_next):
            name = context.message.name
            args = dict(context.message.arguments or {})
            route, _ = route_name("tools/call", {"name": name, "arguments": args})
            if name == "manage_tools" and args.get("action") in {
                "search",
                "describe",
                "catalog",
                "list_groups",
            }:
                result = await call_next(context)
                content = result.structured_content or {}
                unavailable = (
                    route == "manage_tools.search"
                    and content.get("matching") == "observation"
                    and content.get("observation_source") == "editor_live"
                    and "unsaved_graph_structure" in content.get("required_values", [])
                    and content.get("routes") == []
                )
                db.execute(
                    "INSERT INTO calls VALUES (?,?,'null',NULL)",
                    (route, "unsaved_graph_unavailable" if unavailable else None),
                )
                db.commit()
                return result
            payload = observe_fixture(db, route, args)
            return ToolResult(content=json.dumps(payload), structured_content=payload)

        async def on_read_resource(self, context, call_next):
            route, args = route_name("resources/read", {"uri": str(context.message.uri)})
            payload = observe_fixture(db, route, args)
            return ResourceResult([ResourceContent(json.dumps(payload), "application/json")])

    server = FastMCP(
        "unityMCP",
        instructions="Work from the advertised tools and resources. The Editor runs in a disposable simulation.",
    )
    register_all_tools(server, project_scoped_tools=False)
    register_all_resources(server, project_scoped_tools=False)
    server.add_middleware(Simulate())
    server.run(transport="stdio", show_banner=False)


def run(args):
    args.output.mkdir(parents=True, exist_ok=False)
    cases = []
    for task in args.task or TASKS:
        case = args.output / task
        case.mkdir()
        project = case / "project"
        project.mkdir()
        graph = project / GRAPH_FILE
        graph.parent.mkdir()
        identifiers = ["aa", "bb", "cc"]
        serialized = [{"m_Nodes": [{"m_Id": key} for key in identifiers], "m_Edges": [{}, {}]}]
        serialized.extend(
            {"m_ObjectId": key, "m_Type": name}
            for key, name in zip(identifiers, ["Add", "Multiply", "Color"], strict=True)
        )
        graph.write_text("\n\n".join(json.dumps(block) for block in serialized), encoding="utf-8")
        journal = case / "fixture.sqlite"
        db = connect(journal)
        prompt = (
            TASKS[task][int(args.language == "ko")]
            + "\nWork only with this disconnected simulation. Do not inspect the benchmark. Report one JSON object with a single value field. Use a boolean for compilation activity or accepted dispatch, a count for graph contents, the first element name for a hit, or null for an unverifiable value."
        )
        command = [
            args.codex,
            "-a",
            "never",
            "exec",
            "--ignore-user-config",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "-C",
            str(project),
            "--model",
            args.model,
            "-c",
            "model_reasoning_effort=" + json.dumps(args.effort),
            "-c",
            "mcp_servers.unityMCP.command=" + json.dumps(sys.executable),
            # This single server can affect only the disposable fixture journal.
            "-c",
            'mcp_servers.unityMCP.default_tools_approval_mode="approve"',
            "-c",
            "mcp_servers.unityMCP.args="
            + json.dumps(
                ["-X", "utf8", str(Path(__file__).resolve()), "serve", "--journal", str(journal)]
            ),
            "--json",
            "--output-last-message",
            str(case / "answer.txt"),
            "-",
        ]
        try:
            with (
                (case / "events.jsonl").open("wb") as log,
                (case / "stderr.log").open("wb") as errors,
            ):
                completed = subprocess.run(
                    command,
                    input=prompt.encode(),
                    stdout=log,
                    stderr=errors,
                    cwd=project,
                    timeout=args.timeout,
                    check=False,
                )
            answer = (
                json.loads((case / "answer.txt").read_text(encoding="utf-8-sig"))
                if completed.returncode == 0
                else None
            )
        except (OSError, ValueError, subprocess.TimeoutExpired):
            answer = None
        try:
            calls = db.execute(
                "SELECT route,fact,value,effect FROM calls ORDER BY rowid"
            ).fetchall()
            host = client_trace(case / "events.jsonl", GRAPH_FILE, endpoint=False)
            calls.extend(
                ("shell.read_saved_graph", "saved_nodes", json.dumps(receipt["nodes"]), None)
                for receipt in host.saved_observations
            )
            cases.append(judge(task, calls, answer, approval_blocks=host.approval_blocks))
        finally:
            db.close()
    result = {"cases": cases, "passed": sum(case["passed"] for case in cases)}
    (args.output / "results.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))
    return int(result["passed"] != len(cases))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    server = sub.add_parser("serve")
    server.add_argument("--journal", type=Path, required=True)
    test = sub.add_parser("run")
    test.add_argument("--output", type=lambda value: Path(value).resolve(), required=True)
    test.add_argument("--task", choices=TASKS, action="append")
    test.add_argument("--language", choices=("en", "ko"), default="en")
    test.add_argument("--model", required=True)
    test.add_argument("--effort", required=True)
    test.add_argument("--timeout", type=int, default=180)
    test.add_argument("--codex", default=shutil.which("codex.exe") or shutil.which("codex"))
    args = parser.parse_args()
    if args.mode == "serve":
        serve(args.journal)
        return 0
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
