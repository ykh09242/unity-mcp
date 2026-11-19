"""Record allowed Unity MCP observations and adjudicate blind-trial traces."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from threading import Lock, Thread
from typing import IO, Any
from urllib.parse import parse_qs, unquote, urlsplit
from uuid import UUID

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.experiments.blind_tool_answers import (
    GAME_VIEW,
    GAME_VIEW_RESOURCE,
    RAYCAST,
    SIZE_ROUTES,
    current_view,
    final_answer,
    observed_center_hit,
)
from tools.experiments.blind_tool_diagnostics import SEARCH_ROUTES
from tools.experiments.blind_tool_shell import shell_purpose, shell_receipt

EDITOR_STATE = "mcpforunity://editor/state"
PROJECT_INFO = "mcpforunity://project/info"
GRAPH = "manage_shader.inspect_graph"
GRAPH_PREFIX = "mcpforunity://shader/graph/"
GRAPH_RESOURCE = GRAPH_PREFIX + "{encoded_path}"
GRAPH_ROUTES = {GRAPH, GRAPH_RESOURCE, "inspect_shader_graph"}
SAVED_FILE = "shell.read_saved_graph"
ACTIVE_SCENE = {"scene_inspect.get_active", "manage_scene.get_active"}
ROUTES = {
    "editor": {EDITOR_STATE, *ACTIVE_SCENE},
    "context": {PROJECT_INFO},
    "game-view": SIZE_ROUTES,
    "raycast": {*SIZE_ROUTES, RAYCAST},
    "graph": GRAPH_ROUTES,
    "graph-saved": GRAPH_ROUTES,
    "graph-open": GRAPH_ROUTES,
}
DISCOVERY = {
    "initialize",
    "tools/list",
    "resources/list",
    "resources/templates/list",
    "manage_tools.catalog",
    "manage_tools.describe",
    "manage_tools.list_groups",
    *SEARCH_ROUTES,
    "mcpforunity://capabilities",
    "mcpforunity://capabilities/scoped",
    "mcpforunity://instances",
    "mcpforunity://trial/source",
    "mcpforunity://doctor",
    "mcpforunity://scene/gameobject-api",
}
OTHER_READS = {
    "find_gameobjects",
    "gameobject_inspect.find",
    "gameobject_inspect.get_info",
    "gameobject_inspect.get_components",
    "manage_input.status",
    "manage_input.player_status",
    "manage_ugui.get_hierarchy",
    "manage_ugui.inspect_layout",
    "manage_ugui.inspect_canvas",
    "manage_ugui.diagnose_projection",
    "manage_ugui.ping",
    "scene_inspect.get_hierarchy",
    "scene_inspect.get_loaded_scenes",
    "mcpforunity://editor/windows",
    "mcpforunity://scene/cameras",
}
# These callbacks avoid refresh-capable generic preflight. The HTTP adapter
# explicitly disables refresh for the one allowlisted exception.
SAFE_HTTP_READS = {EDITOR_STATE, PROJECT_INFO, *SIZE_ROUTES, *GRAPH_ROUTES, "find_gameobjects"}
APPROVAL = re.compile(
    r"approval[^\n]{0,80}(?:denied|rejected|blocked|never|declined|required)|"
    r"(?:denied|rejected|blocked|declined)[^\n]{0,80}approval",
    re.IGNORECASE,
)


def mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def string(value: Any) -> str:
    return value if isinstance(value, str) else ""


def json_object(value: str | bytes) -> dict[str, Any]:
    try:
        return mapping(json.loads(value))
    except (ValueError, UnicodeError):
        return {}


def thread_digest(value: Any) -> str:
    """Correlate opaque host thread IDs without retaining their original values."""
    try:
        key = UUID(string(value))
    except ValueError:
        return ""
    return sha256(key.bytes).hexdigest()


def actor_binding(params: dict[str, Any]) -> tuple[str, str]:
    """Use only the documented Codex-owned MCP request metadata fields."""
    meta = mapping(params.get("_meta"))
    direct = thread_digest(meta.get("threadId"))
    raw = meta.get("x-codex-turn-metadata")
    turn = json_object(raw) if isinstance(raw, str) else mapping(raw)
    nested = thread_digest(turn.get("thread_id"))
    if direct and nested and direct != nested:
        return "", "conflict"
    return direct or nested, "bound" if direct or nested else "missing"


def call_actor(params: dict[str, Any]) -> str:
    return actor_binding(params)[0]


def route_name(method: str, params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Identify the action as well as the tool without retaining user-supplied text."""
    if method == "tools/call":
        name = string(params.get("name"))
        args = mapping(params.get("arguments"))
        action = string(args.get("action"))
        return (f"{name}.{action}" if action else name), args
    if method == "resources/read":
        uri = string(params.get("uri"))
        address, _, query = uri.partition("?")
        if address.startswith(GRAPH_PREFIX):
            decoded = unquote(address.removeprefix(GRAPH_PREFIX))
            return GRAPH_RESOURCE, {"uri": uri, "path": decoded}
        return address, {"uri": uri, "url_query": query}
    return method, {}


def decision(route: str, args: dict[str, Any], graph_asset: str, *, endpoint: bool = False) -> str:
    """Keep the bridge limited to observation and connection-local discovery."""
    serialized = unquote(json.dumps(args, ensure_ascii=False)).replace("\\", "/").casefold()
    if any(
        part in serialized
        for part in ("assets/resources/gamedata", "assets//resources", "../", "/..")
    ):
        return "blocked"
    if any(args.get(key) for key in ("allow_custom_filters", "evaluate_getters", "include_values")):
        return "blocked"
    if route in GRAPH_ROUTES:
        if not graph_asset or args.get("path") != graph_asset:
            return "blocked"
        if route == GRAPH_RESOURCE:
            uri = urlsplit(args["uri"])
            segment = uri.path.removeprefix("/graph/")
            query = parse_qs(uri.query, keep_blank_values=True)
            if (
                "/" in segment
                or uri.fragment
                or set(query) - {"page_number", "page_size"}
                or any(
                    len(values) != 1 or not values[0].isascii() or not values[0].isdecimal()
                    for values in query.values()
                )
            ):
                return "blocked"
    elif "uri" in args and args.get("url_query"):
        return "blocked"
    if route in DISCOVERY or route.startswith("skill://unity-mcp-orchestrator/"):
        return "discovery"
    if endpoint:
        return "unity" if route in SAFE_HTTP_READS else "blocked"
    if route in OTHER_READS or any(route in routes for routes in ROUTES.values()):
        return "unity"
    return "blocked"


def unpack(response: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Read MCP success separately from the domain payload nested in content."""
    result = mapping(response.get("result"))
    if response.get("error"):
        return "error", {}
    value = mapping(result.get("structuredContent") or result.get("structured_content"))
    if not value:
        blocks = result.get("contents") or result.get("content") or []
        if isinstance(blocks, list) and blocks:
            value = json_object(string(mapping(blocks[0]).get("text")))
    if (
        value.get("success") is False
        or value.get("error")
        or result.get("isError")
        or result.get("is_error")
    ):
        data = mapping(value.get("data"))
        if data.get("kind") == "ugui_raycast_diagnostics" and data.get("status") == "failed":
            return "error", data
        return "error", {}
    if value.get("success") is True:
        return "returned", mapping(value.get("data"))
    if isinstance(value.get("routes"), list) and value.get("scope") in {
        "mcp_context",
        "local_catalog",
    }:
        return "returned", value
    if result:
        return "returned", {}
    return "missing", {}


def positive_size(value: Any) -> list[int] | None:
    size = mapping(value)
    width, height = size.get("width"), size.get("height")
    if type(width) is int and type(height) is int and min(width, height) > 0:
        return [width, height]
    return None


def observation(route: str, data: dict[str, Any]) -> dict[str, Any]:
    """Retain the documented evidence fields for the scored read operations."""
    if route in SEARCH_ROUTES:
        from tools.experiments.blind_tool_diagnostics import search_receipt

        return search_receipt(data)
    if route in SIZE_ROUTES:
        size = positive_size(data.get("render_size"))
        selected = mapping(data.get("current_size"))
        preset = bool(string(selected.get("label"))) or bool(positive_size(selected))
        observation_fields = {"render_size": size} if size else {}
        if preset:
            observation_fields["preset_reported"] = True
        if label := string(selected.get("label")):
            observation_fields["preset_label"] = label
        return observation_fields
    if route == RAYCAST:
        if data.get("kind") != "ugui_raycast_diagnostics":
            return {}
        if data.get("status") == "failed":
            reason = string(data.get("reason"))
            return {
                "status": "failed",
                "reason": reason if re.fullmatch("[a-z_]{1,64}", reason) else None,
            }
        point = data.get("screen_position")
        status = string(data.get("status"))
        if not (
            isinstance(point, list)
            and len(point) == 2
            and all(type(value) in (int, float) for value in point)
            and status in {"observed", "unknown"}
        ):
            return {}
        hit_count = data.get("hits_total")
        hits = data.get("hits")
        first_hit = mapping(hits[0]) if isinstance(hits, list) and hits else {}
        return {
            "point": point,
            "hits_total": hit_count if type(hit_count) is int else None,
            "first_hit": string(first_hit.get("name")) or None,
            "status": status,
            "reason": string(data.get("reason")) or None,
            "ordered_hits": (
                type(hit_count) is int
                and hit_count >= 0
                and isinstance(hits, list)
                and (
                    (hit_count == 0 and not hits)
                    or (
                        hit_count > 0
                        and first_hit.get("rank") == 0
                        and bool(string(first_hit.get("name")))
                    )
                )
            ),
            "policy_safe": (
                mapping(data.get("policy")).get("package_explicit_event_dispatch") is False
                and mapping(data.get("policy")).get("user_code_may_have_run") is False
            ),
            "native_query_attempted": mapping(data.get("policy")).get("native_query_attempted")
            is True,
            "delivery": string(mapping(data.get("input")).get("delivery")) or None,
        }
    if route in GRAPH_ROUTES:
        nodes = data.get("nodes")
        count, edges = data.get("nodeCount"), data.get("edgeCount")
        if type(count) is not int or type(edges) is not int or min(count, edges) < 0:
            return {}
        types = (
            {string(mapping(node).get("type")) for node in nodes}
            if isinstance(nodes, list)
            else set()
        )
        return {
            "nodes": count,
            "edges": edges,
            "node_types": sorted(types - {""}),
            "data_state": "saved",
        }
    if route == EDITOR_STATE:
        editor = mapping(data.get("editor"))
        play = mapping(editor.get("play_mode")).get("is_playing")
        compile_state = mapping(data.get("compilation")).get("is_compiling")
        scene = mapping(editor.get("active_scene"))
        if type(play) is not bool or type(compile_state) is not bool:
            return {}
        return {
            "playing": play,
            "compiling": compile_state,
            "active_scene": bool(string(scene.get("name")) or string(scene.get("path"))),
            "scene_name": string(scene.get("name")) or None,
            "fresh": mapping(data.get("staleness")).get("is_stale") is False,
        }
    if route in ACTIVE_SCENE:
        scene = mapping(data.get("scene")) or data
        return (
            {"active_scene": True, "scene_name": string(scene.get("name")) or None}
            if string(scene.get("name") or scene.get("path"))
            else {}
        )
    if route == PROJECT_INFO:
        fields = ("projectRoot", "unityVersion", "renderPipeline")
        return (
            {
                "project_fields": True,
                "project_root": data["projectRoot"],
                "unity_version": data["unityVersion"],
                "render_pipeline": data["renderPipeline"],
            }
            if all(string(data.get(key)) for key in fields)
            else {}
        )
    return {}


def page_number(route: str, args: dict[str, Any]) -> int:
    """Graph pages are distinct work. A second request for a page is a repeat."""
    page = args.get("page_number", 1)
    if route == GRAPH_RESOURCE:
        page = parse_qs(urlsplit(string(args.get("uri"))).query).get("page_number", ["1"])[0]
    if route in GRAPH_ROUTES and (type(page) is int or re.fullmatch(r"[0-9]{1,10}", string(page))):
        return int(page)
    return 1


def relay_stdio(relay: list[str], audit_dir: Path, graph_asset: str, *, endpoint: bool) -> None:
    """Audit actual JSON-RPC requests while forwarding bytes through the vetted relay."""
    pending: dict[str | int, tuple[int, str, str]] = {}
    lock = Lock()
    audit_path = audit_dir / f"mcp-{os.getpid()}.jsonl"
    with (
        audit_path.open("x", encoding="utf-8") as audit,
        subprocess.Popen(
            [sys.executable, "-X", "utf8", *relay],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        ) as bridge,
    ):
        assert bridge.stdin is not None and bridge.stdout is not None

        def record(**fields: Any) -> None:
            audit.write(json.dumps(fields, ensure_ascii=True) + "\n")
            audit.flush()

        def send_to_host(payload: bytes) -> None:
            sys.stdout.buffer.write(payload)
            sys.stdout.buffer.flush()

        def send_requests() -> None:
            try:
                sequence = 0
                for payload in sys.stdin.buffer:
                    request = json_object(payload)
                    method = string(request.get("method"))
                    request_id = request.get("id")
                    if method and type(request_id) in (str, int):
                        if method == "ping":
                            bridge.stdin.write(payload)
                            bridge.stdin.flush()
                            continue
                        params = mapping(request.get("params"))
                        route, args = route_name(method, params)
                        channel = decision(route, args, graph_asset, endpoint=endpoint)
                        label = route if channel != "blocked" else "unapproved_route"
                        sequence += 1
                        with lock:
                            actor, binding = actor_binding(params)
                            record(
                                event="request",
                                sequence=sequence,
                                route=label,
                                channel=channel,
                                page=page_number(route, args),
                                actor=actor,
                                actor_binding=binding,
                            )
                            if channel == "blocked":
                                response = {
                                    "jsonrpc": "2.0",
                                    "id": request_id,
                                    "error": {
                                        "code": -32602,
                                        "message": "Outside observation scope",
                                    },
                                }
                                record(event="response", sequence=sequence, outcome="error")
                                send_to_host((json.dumps(response) + "\n").encode())
                                continue
                            pending[request_id] = (sequence, route, channel)
                    elif method not in {"notifications/initialized", "notifications/cancelled"}:
                        continue
                    bridge.stdin.write(payload)
                    bridge.stdin.flush()
            except BrokenPipeError:
                pass
            finally:
                bridge.stdin.close()

        thread = Thread(target=send_requests, daemon=True)
        thread.start()
        for payload in bridge.stdout:
            response = json_object(payload)
            request_id = response.get("id")
            with lock:
                request = pending.pop(request_id, None) if type(request_id) in (str, int) else None
                if request is not None:
                    sequence, route, channel = request
                    outcome, data = unpack(response)
                    if route == RAYCAST and data.get("status") == "failed":
                        outcome = "error"
                    detail = (
                        observation(route, data)
                        if channel == "unity" or route in SEARCH_ROUTES
                        else {}
                    )
                    record(event="response", sequence=sequence, outcome=outcome, observation=detail)
                send_to_host(payload)


def load_lines(stream: IO[str]) -> Iterator[dict[str, Any]]:
    """An interrupted process may leave the final JSON line incomplete."""
    for line in stream:
        if row := json_object(line):
            yield row


@dataclass(slots=True)
class Call:
    route: str
    channel: str
    order: int
    status: str = "missing"
    evidence: dict[str, Any] = field(default_factory=dict)
    page: int = 1
    actor: str = ""
    actor_binding: str = ""
    timeline: str = ""
    request_at: int = 0
    response_at: int = 0
    shell_purpose: str = ""


def wire_trace(path: Path, *, after: int = 0, through: int | None = None) -> list[Call]:
    """Join request and response records by connection-local sequence number."""
    calls: dict[int, Call] = {}
    with path.open(encoding="utf-8") as stream:
        for ordinal, row in enumerate(load_lines(stream), 1):
            number = row.get("sequence")
            if (
                type(number) is not int
                or number <= after
                or (through is not None and number > through)
            ):
                continue
            match row.get("event"):
                case "request":
                    page = row.get("page")
                    calls[number] = Call(
                        string(row.get("route")),
                        string(row.get("channel")),
                        number,
                        page=page if type(page) is int else 1,
                        actor=string(row.get("actor")),
                        actor_binding=string(row.get("actor_binding")),
                        timeline=path.name,
                        request_at=ordinal,
                    )
                case "response" if number in calls:
                    call = calls[number]
                    call.status = string(row.get("outcome"))
                    call.evidence = mapping(row.get("observation"))
                    call.response_at = ordinal
                case _:
                    continue
    return list(calls.values())


def wire_checkpoint(directory: Path) -> dict[str, int]:
    """Record per-connection request bounds before or after a host-owned model turn."""
    return {
        path.name: max((call.order for call in wire_trace(path)), default=0)
        for path in directory.glob("mcp-*.jsonl")
    }


def event_route(item: dict[str, Any]) -> str:
    name = string(item.get("tool"))
    args = mapping(item.get("arguments"))
    if name == "read_mcp_resource":
        return route_name("resources/read", args)[0]
    if name in {"list_mcp_resources", "list_mcp_resource_templates"}:
        return "resources/list"
    return route_name("tools/call", {"name": name, "arguments": args})[0]


def saved_read_grade(command: str, output: str, asset: str) -> str:
    """Credit a direct exact-path read with serialized graph structure in the output."""
    if not asset or asset.casefold() not in command.casefold().replace("\\", "/"):
        return "none"
    if shell_purpose(command, asset) != "named_asset_read":
        return "review"
    return "observed" if saved_graph_facts(output[:2_000_000]) else "review"


def saved_graph_facts(output: str, *, allow_partial: bool = False) -> dict[str, Any]:
    """Use types of objects referenced by the root graph's actual node list."""
    decoder = json.JSONDecoder()
    try:
        content = output.lstrip("\ufeff \t\r\n")
        root, boundary = decoder.raw_decode(content)
    except ValueError:
        return {}
    nodes, edges = mapping(root).get("m_Nodes"), mapping(root).get("m_Edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        return {}
    node_ids = {string(mapping(node).get("m_Id")) for node in nodes} - {""}
    node_types: set[str] = set()
    while True:
        content = content[boundary:].lstrip()
        if not content:
            break
        try:
            value, boundary = decoder.raw_decode(content)
        except ValueError:
            break
        node = mapping(value)
        kind = string(node.get("m_Type"))
        if kind and string(node.get("m_ObjectId")) in node_ids:
            node_types.add(kind)
    return (
        {
            "nodes": len(nodes),
            "edges": len(edges),
            "node_types": sorted(node_types),
            "data_state": "saved",
        }
        if allow_partial or len(node_types) >= 2
        else {}
    )


def has_saved_read(command: str, output: str, asset: str) -> bool:
    return saved_read_grade(command, output, asset) == "observed"


@dataclass(slots=True)
class ClientTrace:
    calls: dict[str, Call] = field(default_factory=dict)
    approval_blocks: int = 0
    shell_calls: int = 0
    saved_reads: int = 0
    saved_reads_to_review: int = 0
    saved_observations: list[dict] = field(default_factory=list)
    child_actors: set[str] = field(default_factory=set)
    parent_actor: str = ""
    waits: int = 0
    completed: bool = False
    final_message: str = ""


def client_trace(
    path: Path,
    asset: str,
    *,
    endpoint: bool,
    unity_server: str = "unityMCP",
    expected_actor: str = "",
) -> ClientTrace:
    """Use host accounting for calls blocked before the relay and main-role shell activity."""
    trace = ClientTrace()
    if not path.exists():
        return trace
    with path.open(encoding="utf-8") as stream:
        for event in load_lines(stream):
            if expected_actor and event.get("host_actor") != expected_actor:
                continue
            item = mapping(event.get("item"))
            item_id = string(item.get("id"))
            if event.get("type") in {"item.started", "turn.started"}:
                trace.final_message = ""
            match event.get("type"), item.get("type"):
                case "thread.started", _:
                    trace.parent_actor = thread_digest(event.get("thread_id"))
                case "host.child_linked", _:
                    digest = string(event.get("child_actor"))
                    if re.fullmatch("[a-f0-9]{64}", digest):
                        trace.child_actors.add(digest)
                case "item.started" | "item.completed", "mcp_tool_call" if (
                    item.get("server") == unity_server
                ):
                    route = event_route(item)
                    args = mapping(item.get("arguments"))
                    if item.get("tool") == "read_mcp_resource":
                        _, args = route_name("resources/read", args)
                    channel = decision(route, args, asset, endpoint=endpoint)
                    call = trace.calls.setdefault(
                        item_id,
                        Call(
                            route if channel != "blocked" else "unapproved_route",
                            channel,
                            len(trace.calls) + 1,
                            page=page_number(route, args),
                        ),
                    )
                    if event.get("type") == "item.completed":
                        message = string(mapping(item.get("error")).get("message"))
                        if item.get("status") == "failed" and APPROVAL.search(message):
                            call.status = "approval_blocked"
                            trace.approval_blocks += 1
                        else:
                            call.status = string(item.get("status"))
                            result = mapping(item.get("result"))
                            receipt = mapping(item.get("observed_result"))
                            if receipt and call.status == "completed":
                                call.status = string(receipt.get("status"))
                                call.evidence = mapping(receipt.get("facts"))
                            elif result and call.status == "completed":
                                call.status, data = unpack({"result": result})
                                call.evidence = observation(route, data)
                                if route == RAYCAST and data.get("status") == "failed":
                                    call.status = "error"
                case "item.started" | "item.completed", "mcp_tool_call" | "other_tool":
                    service = string(item.get("server")) or "host"
                    tool = string(item.get("tool"))
                    channel = (
                        "discovery"
                        if tool
                        in {"list_mcp_resources", "list_mcp_resource_templates", "tool_search"}
                        else "competitor"
                    )
                    call = trace.calls.setdefault(
                        item_id, Call(f"{service}.{tool}", channel, len(trace.calls) + 1)
                    )
                    if event.get("type") == "item.completed":
                        call.status = string(item.get("status")) or "completed"
                        if APPROVAL.search(string(mapping(item.get("error")).get("message"))):
                            call.status = "approval_blocked"
                            trace.approval_blocks += 1
                case "item.started" | "item.completed", "command_execution":
                    # Reserve the start position even when a later command completes first.
                    command = string(item.get("command"))
                    exact_path = bool(
                        asset and asset.casefold() in command.casefold().replace("\\", "/")
                    )
                    call = trace.calls.setdefault(
                        item_id, Call("shell.command", "shell", len(trace.calls) + 1)
                    )
                    receipt = mapping(item.get("shell_receipt")) or shell_receipt(
                        command, string(item.get("aggregated_output")), item.get("exit_code"), asset
                    )
                    call.shell_purpose = string(receipt.get("purpose")) or "unknown"
                    if event.get("type") != "item.completed":
                        continue
                    trace.shell_calls += 1
                    call.status = "returned" if item.get("exit_code") == 0 else "error"
                    output = string(item.get("aggregated_output"))
                    if item.get("exit_code") == 0:
                        recorded = mapping(item.get("saved_read"))
                        grade = recorded.get("grade") or (
                            saved_read_grade(command, output, asset) if exact_path else "none"
                        )
                        if (
                            item.get("shell_receipt")
                            and grade == "observed"
                            and (
                                not asset
                                or receipt.get("asset_digest") != sha256(asset.encode()).hexdigest()
                            )
                        ):
                            grade = "review"
                        trace.saved_reads += int(grade == "observed")
                        trace.saved_reads_to_review += int(grade == "review")
                        if grade == "observed":
                            call.route = SAVED_FILE
                            trace.saved_observations.append(
                                mapping(recorded.get("facts")) or saved_graph_facts(output)
                            )
                        elif (
                            asset
                            and call.shell_purpose == "named_asset_read"
                            and receipt.get("asset_digest") == sha256(asset.encode()).hexdigest()
                            and (facts := mapping(receipt.get("facts")))
                        ):
                            call.evidence = facts
                            trace.saved_observations.append(facts)
                    elif APPROVAL.search(output):
                        trace.approval_blocks += 1
                case (
                    "item.completed",
                    "collab_tool_call" | "collaboration_tool_call" | "subagent_tool_call",
                ):
                    if (
                        item.get("tool") in {"spawn_agent", "spawn"}
                        and item.get("status") == "completed"
                    ):
                        peers = item.get("receiver_thread_ids")
                        if isinstance(peers, list):
                            trace.child_actors.update(filter(None, map(thread_digest, peers)))
                    if item.get("tool") == "wait" and item.get("status") == "completed":
                        trace.waits += 1
                case "turn.completed", _:
                    trace.completed = True
                case "item.completed", "agent_message":
                    if item.get("phase") in {None, "final_answer"}:
                        trace.final_message = string(item.get("text"))
                case _:
                    continue
    return trace


def role_status(
    role: str,
    trace: ClientTrace,
    connections: list[list[Call]],
    child: ClientTrace | None = None,
    *,
    configured=False,
) -> tuple[str, str]:
    """Join native role events with distinct observed business connections."""
    observing = [
        calls for calls in connections if any(call.channel != "discovery" for call in calls)
    ]
    actors = {
        call.actor
        for calls in observing
        for call in calls
        if call.actor and call.channel != "discovery"
    }
    host_business = any(call.channel != "discovery" for call in trace.calls.values())
    reads = [call for calls in observing for call in calls if call.channel != "discovery"]
    if any(call.actor_binding == "conflict" for call in reads):
        return "mixed", "conflicting-request-actor-metadata"

    def matches(call, request):
        status = {"completed": "returned", "failed": "error"}.get(call.status, call.status)
        return (
            status in {"returned", "error"}
            and (call.route, call.page, status) == (request.route, request.page, request.status)
            and not any(
                key in call.evidence and call.evidence[key] != value
                for key, value in request.evidence.items()
            )
        )

    def matched_items(host, unbound):
        remaining = list(host.calls.values())
        for request in unbound:
            index = next(
                (i for i, candidate in enumerate(remaining) if matches(candidate, request)), None
            )
            if index is None:
                return False
            remaining.pop(index)
        return True

    if role == "main":
        if trace.waits:
            return "mixed", "native-wait-in-direct-main-trial"
        if (
            trace.child_actors
            or len(observing) > 1
            or (actors and trace.parent_actor and actors != {trace.parent_actor})
        ):
            return "mixed", "competing-actors"
        if configured and (
            not trace.parent_actor or not matched_items(trace, [c for c in reads if not c.actor])
        ):
            return "unverified", "missing-main-request-receipt"
        return "confirmed", "host-main"
    if host_business or trace.shell_calls or trace.parent_actor in actors:
        return "mixed", "parent-business-activity"
    if len(trace.child_actors) == 1 and child and (child.calls or child.completed):
        if actors - trace.child_actors or len(observing) > 1:
            return "mixed", "competing-actors"
        # An unattributed wire call must also be independently visible on this
        # child's host timeline before it can contribute evidence to that actor.
        if matched_items(child, [c for c in reads if not c.actor]):
            return "confirmed", "host-linked-child-items"
    if len(trace.child_actors) == 1 and len(observing) == 1:
        if actors - trace.child_actors:
            return "mixed", "unrelated-request-actor"
        if all(call.actor in trace.child_actors for call in reads):
            return "confirmed", "native-spawn-and-linked-requests"
        return "inferred", "spawn-with-unbound-business-requests"
    if trace.waits and len(observing) == 1 and len(connections) == 2:
        if len(actors) == 1 and trace.parent_actor and trace.parent_actor not in actors:
            return "inferred", "distinct-mcp-actor-and-native-wait"
        if not actors:
            return "inferred", "isolated-mcp-connection-and-native-wait"
    return "unverified", "missing-actor-evidence"


def evidence_grade(task: str, calls: list[Call]) -> str:
    """Require task-specific live facts and separate limitations from complete answers."""
    evidence = [call for call in calls if call.evidence and call.status == "returned"]
    if not evidence:
        return "none"
    if task == "graph-open":
        return "partial"
    if task in {"graph", "graph-saved"}:
        totals: dict[tuple[int, int], set[str]] = {}
        for call in evidence:
            entry = call.evidence
            key = (entry["nodes"], entry["edges"])
            totals.setdefault(key, set()).update(entry["node_types"])
        return (
            "complete" if len(totals) == 1 and len(next(iter(totals.values()))) >= 2 else "partial"
        )
    if task == "editor":
        fresh = scene = False
        for call in calls:
            if call.status != "returned":
                continue
            if call.route == EDITOR_STATE:
                fresh = bool(call.evidence.get("fresh"))
                scene = fresh and bool(call.evidence.get("active_scene"))
            elif call.route in ACTIVE_SCENE:
                scene = bool(call.evidence.get("active_scene"))
        return "complete" if fresh and scene else "partial"
    if task in {"raycast", "game-view"}:
        size, ray = current_view(
            [{"route": c.route, **c.evidence} for c in calls if c.status == "returned"]
        )
        if size.get("render_size") and (task == "game-view" or observed_center_hit(ray)):
            return "complete"
        return "partial"
    return "complete"


def repeat_counts(calls: list[Call]) -> tuple[int, int]:
    """Count repeats and the subset that follow a recorded error on the same operation/page."""
    prior: dict[tuple[str, int], str] = {}
    repeated = retries = 0
    for call in calls:
        family = (
            GAME_VIEW
            if call.route in SIZE_ROUTES
            else GRAPH
            if call.route in GRAPH_ROUTES
            else call.route
        )
        key = family, call.page
        if key in prior:
            repeated += 1
            retries += int(prior[key] in {"error", "failed", "approval_blocked"})
        prior[key] = call.status
    return repeated, retries


def timing_metrics(task: str, calls: list[Call]) -> tuple[int | None, int | None]:
    """Only earlier responses on a common timeline can justify a later attempt."""
    if (
        any(not c.request_at or not c.timeline for c in calls)
        or len({c.timeline for c in calls}) > 1
    ):
        return None, None
    seen = set()
    count = early_rays = 0
    for call in calls:
        family = (
            GAME_VIEW
            if call.route in SIZE_ROUTES
            else GRAPH
            if call.route in GRAPH_ROUTES
            else call.route
        )
        key = (family, call.page)
        prior = sorted(
            (c for c in calls if 0 < c.response_at < call.request_at), key=lambda c: c.response_at
        )
        if key in seen:
            complete = any(c.route == SAVED_FILE and c.status == "returned" for c in prior)
            count += int(complete or evidence_grade(task, prior) == "complete")
        if call.route == RAYCAST:
            size, _ = current_view(
                [{"route": c.route, **c.evidence} for c in prior if c.status == "returned"]
            )
            early_rays += int(not size.get("render_size"))
        seen.add(key)
    return count, early_rays


def merge_business_calls(host: list[Call], wire: list[Call]) -> list[Call]:
    """Join overlapping attempts while retaining host-only blocks and wire-only work."""
    calls = []
    offset = 0
    for visible in host:
        if visible.status == "approval_blocked":
            calls.append(visible)
            continue
        # The same request can be visible twice. Merge matching completions in
        # request order without swallowing a later success for the same route.
        status = {"completed": "returned", "failed": "error"}.get(visible.status, visible.status)
        match = next(
            (
                index
                for index in range(offset, len(wire))
                if (wire[index].route, wire[index].page) == (visible.route, visible.page)
                and (status == wire[index].status or status == "missing")
            ),
            None,
        )
        if match is None:
            calls.append(visible)
        else:
            calls.extend(wire[offset : match + 1])
            offset = match + 1
    calls.extend(wire[offset:])
    return calls


def score_case(case_dir: Path) -> dict[str, Any]:
    """Score transport evidence separately from attempts, approval, and role integrity."""
    row = json_object((case_dir / "case.json").read_bytes())
    task = string(row["task"])
    routes = ROUTES[task]
    from tools.experiments.blind_tool_diagnostics import (
        actor_discovery,
        actor_searches,
        stage_diagnosis,
    )

    options = dict(
        endpoint=row.get("backend") == "unity-mcp-loopback",
        unity_server=string(row.get("unity_server")) or "unityMCP",
    )
    asset = string(row["graph_asset"])
    parent = client_trace(case_dir / "events.jsonl", asset, **options)
    child = None
    if (
        row.get("host_mode") == "configured"
        and row["role"] == "subagent"
        and len(parent.child_actors) == 1
    ):
        actor = next(iter(parent.child_actors))
        child = client_trace(
            case_dir / f"child-{actor}.jsonl", asset, expected_actor=actor, **options
        )
    starts = row.get("wire_before_turn") or {}
    ends = row.get("wire_at_completion")
    connections = [
        wire_trace(
            path,
            after=starts.get(path.name, 0),
            through=ends.get(path.name, 0) if isinstance(ends, dict) else None,
        )
        for path in sorted(case_dir.glob("mcp-*.jsonl"))
    ]
    role, attribution = role_status(
        string(row["role"]),
        parent,
        connections,
        child,
        configured=row.get("host_mode") == "configured",
    )
    host = child if child and attribution == "host-linked-child-items" else parent
    wire = [call for calls in connections for call in calls]
    unity = [call for call in wire if call.channel == "unity"]
    relevant = [call for call in unity if call.route in routes]
    # Wire response order is established only within a connection.
    relevant = sorted(relevant, key=lambda c: (c.timeline, c.response_at))
    wire_grade = evidence_grade(task, relevant)
    host_business = [call for call in host.calls.values() if call.channel != "discovery"]
    host_only = row.get("backend") == "installed-host" and row.get("host_mode") == "configured"
    if host_only:
        relevant = [
            call for call in host_business if call.channel == "unity" and call.route in routes
        ]
    grade = evidence_grade(task, relevant)
    attributed = grade if role == "confirmed" or grade == "none" else "unattributed"
    task_evidence = attributed
    if task == "graph-saved" and host.saved_reads:
        task_evidence = "complete" if role == "confirmed" else "unattributed"
    elif task == "graph-saved" and host.saved_observations and task_evidence == "none":
        task_evidence = "partial" if role == "confirmed" else "unattributed"
    discovery_sources = [
        call for call in (host.calls.values() if host_only else wire) if call.channel == "discovery"
    ]
    # Start order comes from the client when present, including pre-wire approvals. For
    # an isolated child, the single wire connection is the only source of call order.
    business = merge_business_calls(
        host_business, [call for call in wire if call.channel != "discovery"]
    )
    first = business[0] if business else None
    observation_attempts = [
        call
        for call in business
        if call.shell_purpose not in {"instruction_read", "working_directory"}
    ]
    first_observation = next(iter(observation_attempts), None)
    # The registered graph readers cannot inspect an unsaved window. Their responses
    # can document that limitation but are not a suitable business read of its contents.
    sequence = [
        call
        for call in business
        if (task != "graph-open" and call.channel == "unity" and call.route in routes)
        or (task == "graph-saved" and call.route == SAVED_FILE)
    ]
    first_suitable = next(iter(sequence), None)
    first_success = next(
        (
            call
            for call in sequence
            if call.status == "returned" and (call.evidence or call.route == SAVED_FILE)
        ),
        None,
    )
    repeated, retries = repeat_counts(sequence)
    live_observations = []
    for call in relevant:
        if call.status != "returned":
            continue
        detail = dict(call.evidence)
        # Older relay summaries omit some values. Only recover them when one
        # successful host result and one wire response identify the same call.
        matches = [
            c
            for c in host_business
            if c.route == call.route
            and c.page == call.page
            and c.status == "returned"
            and c.evidence
        ]
        if (
            len(matches) == 1
            and sum((c.route, c.page) == (call.route, call.page) for c in relevant) == 1
            and all(matches[0].evidence.get(k) == v for k, v in detail.items())
        ):
            detail.update(matches[0].evidence)
        if detail or call.route in SIZE_ROUTES or call.route == RAYCAST:
            live_observations.append({"route": call.route, **detail})
    redundant, early_rays = timing_metrics(task, sequence)
    surface = case_dir / "host.json"
    if row["role"] == "subagent" and len(parent.child_actors) == 1:
        surface = case_dir / f"host-child-{next(iter(parent.child_actors))}.json"
    inventory = (
        json_object(surface.read_bytes())
        if surface.is_file() and (row["role"] == "main" or len(parent.child_actors) == 1)
        else {}
    )
    selected_actor = (
        parent.parent_actor if row["role"] == "main" else next(iter(parent.child_actors), "")
    )
    actor_host = parent if row["role"] == "main" else child
    actor_conditions = {
        "host_binary_digest": row.get("host_binary_digest"),
        "model": inventory.get("model"),
        "effort": inventory.get("effort"),
        "project_verified": inventory.get("project_verified"),
        "namespace": inventory.get("namespace"),
        "permissions": inventory.get("permissions"),
        "server_commit": mapping(inventory.get("source")).get("server_commit"),
        "tool_schema_digest": inventory.get("tool_schema_digest"),
        "competitor_digest": inventory.get("competitor_digest"),
        "inventory_status": inventory.get("status"),
    }
    searches = actor_searches(actor_host, wire, selected_actor, role == "confirmed")
    diagnosis = stage_diagnosis(
        set() if task == "graph-open" else routes,
        inventory,
        business,
        searches,
        role_confirmed=role == "confirmed",
        grade=task_evidence,
    )
    if searches:
        diagnosis["search_capture"] = (
            "ranked" if diagnosis["first_search"] else "attempt_without_rank"
        )
    elif (
        role == "confirmed"
        and selected_actor
        and any(call.route in SEARCH_ROUTES and call.actor == selected_actor for call in wire)
    ):
        diagnosis["search_capture"] = "attempt_without_rank"
    elif (
        role == "confirmed"
        and actor_host
        and actor_host.completed
        and selected_actor
        and not any(call.route in SEARCH_ROUTES and not call.actor for call in wire)
    ):
        diagnosis["search_capture"] = "no_search"
    else:
        diagnosis["search_capture"] = "unknown"
    diagnosis["actor_discovery"] = actor_discovery(
        actor_host, wire, selected_actor, role == "confirmed"
    )
    return {
        **row,
        **(
            {
                "competitor_digest": inventory.get("competitor_digest"),
                "competition_observed": inventory.get("competition_observed"),
            }
            if row.get("host_mode") == "configured" and row["role"] == "subagent"
            else {}
        ),
        "answer_protocol": string(row.get("answer_protocol")) or "v1",
        "actor_conditions": actor_conditions,
        "role_status": role,
        "role_attribution": attribution,
        "turn_completed": parent.completed,
        "selection_diagnosis": diagnosis,
        "actor_receipt": {
            "parent": parent.parent_actor or None,
            "children": sorted(parent.child_actors),
        },
        "first_business_call": first.route if first else None,
        "first_shell_purpose": next(
            (call.shell_purpose for call in business if call.channel == "shell"), None
        )
        if role == "confirmed"
        else None,
        "first_observation_attempt": (
            first_observation.route
            if first_observation
            and role == "confirmed"
            and first_observation.shell_purpose != "unknown"
            else None
        ),
        "preparatory_calls_before_observation": (
            business.index(first_observation)
            if first_observation
            and role == "confirmed"
            and first_observation.shell_purpose != "unknown"
            else None
        ),
        "first_suitable_business_call": first_suitable.route if first_suitable else None,
        "first_successful_suitable_call": first_success.route
        if first_success and role == "confirmed"
        else None,
        "calls_before_first_success": business.index(first_success)
        if first_success and role == "confirmed"
        else None,
        "calls_before_first_suitable": business.index(first_suitable) if first_suitable else None,
        "first_business_call_suitable": first is not None and first == first_suitable,
        "suitable_attempts": len(sequence),
        "repeat_suitable_calls": repeated,
        "retry_suitable_calls": retries,
        "repeats_after_complete_evidence": redundant,
        "raycast_before_size": early_rays if task == "raycast" else None,
        "discovery_calls": len(discovery_sources),
        "discovery_scope": "trial_host" if host_only else "all_trial_wire_connections",
        "discovery_successes": sum(call.status == "returned" for call in discovery_sources),
        "unity_call_errors": sum(call.status == "error" for call in unity),
        "observation_guard_blocks": sum(call.channel == "blocked" for call in wire),
        "approval_blocks": host.approval_blocks,
        "approval_visibility": "child-host-events"
        if host is child
        else "parent-host-events"
        if row["role"] == "subagent"
        else "host-events",
        "shell_calls_visible_to_host": host.shell_calls,
        "saved_graph_file_reads_visible_to_host": host.saved_reads,
        "saved_graph_evidence": (
            "observed"
            if host.saved_reads and role == "confirmed"
            else "unattributed"
            if host.saved_reads
            else "partial"
            if host.saved_observations and role == "confirmed"
            else "manual-review"
            if host.saved_reads_to_review
            else "none"
        ),
        "live_evidence": attributed,
        "wire_evidence": wire_grade,
        "evidence_transport": "configured-host" if host_only else "relay",
        "task_evidence": task_evidence,
        "live_observations": live_observations,
        "graph_data_state": "saved"
        if task.startswith("graph") and (live_observations or host.saved_reads)
        else None,
        "unsaved_graph_observed": False if task.startswith("graph") else None,
        "final_answer": final_answer(
            case_dir,
            task,
            live_observations if role == "confirmed" or row.get("answer_protocol") != "v3" else [],
            host.saved_observations
            if role == "confirmed" or row.get("answer_protocol") != "v3"
            else [],
            parent.final_message if parent.completed else "",
            protocol=string(row.get("answer_protocol")) or "v1",
            actor_answer=(
                child.final_message if child and child.completed and role == "confirmed" else ""
            )
            if row.get("answer_protocol") == "v3" and row["role"] == "subagent"
            else None,
            explicit_limit=task == "graph-open"
            and any(
                call.status in {"returned", "completed"}
                and call.evidence.get("no_match_reason") == "explicitly_unavailable"
                and call.evidence.get("observation_source") == "editor_live"
                and "unsaved_graph_structure" in call.evidence.get("excluded_values", [])
                for call in searches
            ),
        ),
    }


def score_directory(output: Path) -> dict[str, Any]:
    """Summarize the captured trial matrix while keeping missing cells visible."""
    if not output.is_dir():
        raise FileNotFoundError(f"Trial directory does not exist: {output}")
    cases = [score_case(path.parent) for path in sorted(output.glob("*/case.json"))]
    matrix = output / "matrix.json"
    planned = json.loads(matrix.read_text(encoding="utf-8")) if matrix.exists() else []
    observed = {
        (case["task"], case["language"], case["role"], case["repetition"]) for case in cases
    }
    return {
        "schema": "unity-mcp/blind-tool-selection@1",
        "planned_cases": len(planned),
        "observed_cases": len(cases),
        "unrun_cases": [case for case in planned if tuple(case) not in observed],
        "complete_live_evidence": sum(case["live_evidence"] == "complete" for case in cases),
        "cases_with_approval_blocks": sum(bool(case["approval_blocks"]) for case in cases),
        "runner_failures": sum(case.get("runner_status") != "finished" for case in cases),
        "cases": cases,
    }


def proxy_main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    backend = parser.add_mutually_exclusive_group(required=True)
    backend.add_argument("--relay", type=Path)
    backend.add_argument("--endpoint")
    parser.add_argument("--instance")
    parser.add_argument("--project", type=Path)
    parser.add_argument("--audit-dir", type=Path, required=True)
    parser.add_argument("--graph-asset", required=True)
    args = parser.parse_args()
    if args.relay:
        command = [str(args.relay)]
    else:
        command = [
            str(Path(__file__).with_name("blind_tool_http.py")),
            args.endpoint,
            args.instance,
            str(args.project),
            args.graph_asset,
        ]
    relay_stdio(command, args.audit_dir, args.graph_asset, endpoint=bool(args.endpoint))


if __name__ == "__main__":
    proxy_main()
