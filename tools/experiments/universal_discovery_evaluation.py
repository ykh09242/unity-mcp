"""Measure action-level discovery using only original requests and the real MCP catalog."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, NotRequired, TypedDict
from unittest.mock import patch

import anyio
from fastmcp import Client, FastMCP
from fastmcp.server.middleware import Middleware, MiddlewareContext
from mcp_types import Tool

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.config import config
from services.resources import register_all_resources
from services.tools import register_all_tools
from transport.legacy import unity_connection
from transport.plugin_hub import PluginHub

from tools.experiments.discovery_evaluation_fixture import (
    FIXTURE,
    EvaluationSuite,
    Request,
    load_assisted_hints,
    load_suite,
)

PROFILES = ("core", "full")
SEARCH = "search_unity_tools"
Availability = Literal["available", "unavailable", "unknown"]
PREREGISTERED_GATE = "universal-discovery-absolute-v1"
PREREGISTERED_LIMITS = {
    "min_top1": 0.60,
    "min_top3": 0.80,
    "min_language_top1": 0.40,
    "min_language_top3": 0.55,
    "max_wrong_effect_top1": 5,
}


class Route(TypedDict, total=False):
    tool: str
    operation: str
    uri: str
    uri_template: str


class RouteGroup(TypedDict):
    representative: Route
    alternatives: list[Route]


class Results(TypedDict):
    query: str
    matching: str
    route_groups: list[RouteGroup]
    semantic_model: NotRequired[str]


SearchHints = Mapping[str, str | list[str]]


@dataclass(frozen=True, slots=True)
class Inventory:
    actions: frozenset[str]
    unknown_action_tools: frozenset[str]
    names: frozenset[str]

    def visibility(self, routes: frozenset[str]) -> Availability:
        if routes & self.actions:
            return "available"
        if any(route.partition(".")[0] in self.unknown_action_tools for route in routes):
            return "unknown"
        return "unavailable"


def action_values(schema: dict) -> Iterator[str]:
    if isinstance(value := schema.get("const"), str):
        yield value
    for value in schema.get("enum", ()):
        if isinstance(value, str):
            yield value
    for key in ("allOf", "anyOf", "oneOf"):
        for branch in schema.get(key, ()):
            yield from action_values(branch)


def advertised_inventory(tools: Sequence[Tool], resources: set[str]) -> Inventory:
    actions = set(resources)
    unknown = set()
    for tool in tools:
        action_schema = tool.input_schema.get("properties", {}).get("action")
        if action_schema is None:
            actions.add(tool.name)
        elif values := set(action_values(action_schema)):
            actions.update(f"{tool.name}.{value}" for value in values)
        else:
            unknown.add(tool.name)
    return Inventory(
        frozenset(actions), frozenset(unknown), frozenset({tool.name for tool in tools} | resources)
    )


async def read_inventory(client: Client, expected: set[str]) -> Inventory:
    tools = await client.list_tools()
    resources = {str(resource.uri) for resource in await client.list_resources()}
    resources.update(
        str(template.uri_template) for template in await client.list_resource_templates()
    )
    inventory = advertised_inventory(tools, resources)
    unresolved = {route.partition(".")[0] for route in expected} & inventory.unknown_action_tools
    actions = set(inventory.actions)
    for name in sorted(unresolved):
        # Some legacy tools keep a permissive string in the SDK input schema.
        # Check their public action cards independently of the ranked results.
        response = await client.call_tool("manage_tools", {"action": "catalog", "tool": name})
        tool = (response.structured_content or {}).get("tool", {})
        if tool.get("name") == name and tool.get("advertised") is True and tool.get("actions"):
            actions.update(f"{name}.{action}" for action in tool["actions"])
            unresolved.remove(name)
    return Inventory(frozenset(actions), frozenset(unresolved), inventory.names)


def route_name(route: Route) -> str:
    if tool := route.get("tool"):
        operation = route.get("operation")
        return f"{tool}.{operation}" if operation else tool
    return route.get("uri") or route["uri_template"]


class CaseResult(TypedDict):
    language: str
    task: str
    availability: Availability
    matching: str
    semantic_model: str | None
    rank: int | None
    group_rank: int | None
    hidden_expected_route: bool
    wrong_effect_top1: bool
    wrong_effect_top3: bool
    offered: list[str]


def score(request: Request, response: Results, inventory: Inventory) -> CaseResult:
    if response["query"] != request.query:
        raise ValueError(f"{request.language}/{request.task}: original query was not echoed")
    groups = response["route_groups"][:3]
    offered = [route_name(group["representative"]) for group in groups]
    bindings = [
        {route_name(route) for route in (group["representative"], *group["alternatives"])}
        for group in groups
    ]
    availability = inventory.visibility(request.expected)
    eligible = request.expected & inventory.actions
    hidden = any(
        inventory.visibility(frozenset({route})) == "unavailable"
        for routes in bindings
        for route in routes & request.expected - eligible
    )
    return {
        "language": request.language,
        "task": request.task,
        "availability": availability,
        "matching": response["matching"],
        "semantic_model": response.get("semantic_model", "").strip() or None,
        "rank": next((index for index, route in enumerate(offered, 1) if route in eligible), None),
        "group_rank": next(
            (index for index, routes in enumerate(bindings, 1) if routes & eligible),
            None,
        ),
        "hidden_expected_route": hidden,
        "wrong_effect_top1": bool(request.wrong_effect.intersection(offered[:1])),
        "wrong_effect_top3": bool(request.wrong_effect.intersection(offered)),
        "offered": offered,
    }


class Metrics(TypedDict):
    count: int
    available: int
    unavailable: int
    unknown: int
    top1: int
    top3: int
    available_top1: int
    available_top3: int
    any_binding_top1: int
    any_binding_top3: int
    no_result: int
    hidden_expected_route: int
    wrong_effect_top1: int
    wrong_effect_top3: int


def summarize(cases: Sequence[CaseResult]) -> Metrics:
    available = [row for row in cases if row["availability"] == "available"]
    availability = Counter(row["availability"] for row in cases)
    return {
        "count": len(cases),
        "available": len(available),
        "unavailable": availability["unavailable"],
        "unknown": availability["unknown"],
        "top1": sum(row["rank"] == 1 for row in cases),
        "top3": sum(row["rank"] is not None for row in cases),
        "available_top1": sum(row["rank"] == 1 for row in available),
        "available_top3": sum(row["rank"] is not None for row in available),
        "any_binding_top1": sum(row["group_rank"] == 1 for row in cases),
        "any_binding_top3": sum(row["group_rank"] is not None for row in cases),
        "no_result": sum(not row["offered"] for row in cases),
        "hidden_expected_route": sum(row["hidden_expected_route"] for row in cases),
        "wrong_effect_top1": sum(row["wrong_effect_top1"] for row in cases),
        "wrong_effect_top3": sum(row["wrong_effect_top3"] for row in cases),
    }


class SearchBoundary(Middleware):
    """Refuse business tool and resource invocations in the search-only evaluation."""

    def __init__(self) -> None:
        self.unexpected_calls: list[str] = []

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        name = context.message.name
        arguments = context.message.arguments or {}
        inventory_lookup = (
            name == "manage_tools"
            and set(arguments) == {"action", "tool"}
            and arguments["action"] == "catalog"
        )
        if name != SEARCH and not inventory_lookup:
            self.unexpected_calls.append(context.message.name)
            raise AssertionError("A catalog query attempted to call a business tool")
        return await call_next(context)

    async def on_read_resource(self, context: MiddlewareContext, call_next):
        self.unexpected_calls.append(str(context.message.uri))
        raise AssertionError("A catalog query attempted to read a live resource")


class ProfileResult(TypedDict):
    summary: Metrics
    languages: dict[str, Metrics]
    tasks: dict[str, Metrics]
    worst_language_top1: float
    worst_language_top3: float
    matching: dict[str, int]
    semantic_models: dict[str, int]
    by_model: dict[str, Metrics]
    inventory_unchanged: bool
    unexpected_business_calls: list[str]
    native_dispatch_attempts: int
    rows: list[CaseResult]


async def evaluate_profile(
    profile: str,
    suite: EvaluationSuite,
    *,
    hints: Mapping[str, SearchHints] | None = None,
) -> ProfileResult:
    scope = {"UNITY_MCP_TOOL_PROFILE": profile, "UNITY_MCP_DISABLE_TELEMETRY": "1"}
    with (
        patch.dict(os.environ, scope),
        patch.object(config, "http_remote_hosted", False),
        patch.object(
            PluginHub, "send_command_for_instance", side_effect=AssertionError("native HTTP send")
        ) as http,
        patch.object(
            unity_connection,
            "_async_send_command_response",
            side_effect=AssertionError("async stdio send"),
        ) as stdio_async,
        patch.object(
            unity_connection,
            "_send_command_with_retry",
            side_effect=AssertionError("sync stdio send"),
        ) as stdio_sync,
    ):
        os.environ.pop("UNITY_MCP_TOOL_GROUPS", None)
        boundary = SearchBoundary()
        server = FastMCP("universal-discovery-evaluation", middleware=[boundary])
        register_all_tools(server)
        register_all_resources(server)
        expected = {route for request in suite.requests for route in request.expected}
        rows = []
        async with Client(server, mode="legacy", cache=False) as client:
            before = await read_inventory(client, expected)
            for request in suite.requests:
                arguments = {"query": request.query, "limit": 3}
                if hints is not None:
                    arguments = {**hints[request.query], **arguments}
                reply = await client.call_tool(SEARCH, arguments, raise_on_error=hints is None)
                response = reply.structured_content or {}
                if "route_groups" not in response and hints is not None:
                    # A blinded caller's invalid input remains in the assisted denominator.
                    response = {"query": request.query, "matching": "rejected", "route_groups": []}
                rows.append(score(request, response, before))
            after = await read_inventory(client, expected)
        native_attempts = http.call_count + stdio_async.call_count + stdio_sync.call_count
    languages = {
        language: summarize([row for row in rows if row["language"] == language])
        for language in sorted({request.language for request in suite.requests})
    }
    tasks = {
        task: summarize([row for row in rows if row["task"] == task])
        for task in sorted({request.task for request in suite.requests})
    }
    model_names = sorted({row["semantic_model"] or "none" for row in rows})
    return {
        "summary": summarize(rows),
        "languages": languages,
        "tasks": tasks,
        "worst_language_top1": min(m["top1"] / m["count"] for m in languages.values()),
        "worst_language_top3": min(m["top3"] / m["count"] for m in languages.values()),
        "matching": dict(Counter(row["matching"] for row in rows)),
        "semantic_models": dict(Counter(model for row in rows if (model := row["semantic_model"]))),
        "by_model": {
            model: summarize([row for row in rows if (row["semantic_model"] or "none") == model])
            for model in model_names
        },
        "inventory_unchanged": before == after,
        "unexpected_business_calls": boundary.unexpected_calls,
        "native_dispatch_attempts": native_attempts,
        "rows": rows,
    }


def gate_failures(report: ProfileResult, limits: argparse.Namespace) -> list[str]:
    metrics = report["summary"]
    failures = []
    if (
        not report["inventory_unchanged"]
        or report["unexpected_business_calls"]
        or report["native_dispatch_attempts"]
    ):
        failures.append("search changed inventory or attempted business dispatch")
    if limits.require_matching and report["matching"] != {
        limits.require_matching: metrics["count"]
    }:
        failures.append(f"unexpected search matching modes: {report['matching']}")
    models = report["semantic_models"]
    expected_models = set(limits.expect_model or ())
    complete_receipts = sum(models.values()) == metrics["count"]
    if expected_models and (not complete_receipts or not models.keys() <= expected_models):
        failures.append(f"selected model receipt mismatch: {models}")
    elif limits.require_model and not complete_receipts:
        failures.append(f"missing semantic model receipts: {models}")
    if metrics["hidden_expected_route"]:
        failures.append("search offered an expected operation absent from the client inventory")
    for name, actual, floor in (
        ("top1", metrics["top1"] / metrics["count"], limits.min_top1),
        ("top3", metrics["top3"] / metrics["count"], limits.min_top3),
        ("worst language top1", report["worst_language_top1"], limits.min_language_top1),
        ("worst language top3", report["worst_language_top3"], limits.min_language_top3),
    ):
        if actual < floor:
            failures.append(f"{name} {actual:.2%} < {floor:.2%}")
    if limits.max_wrong_effect_top1 is not None and (
        metrics["wrong_effect_top1"] > limits.max_wrong_effect_top1
    ):
        failures.append(f"wrong-effect top1 count {metrics['wrong_effect_top1']} exceeds limit")
    return failures


def proportion(raw: str) -> float:
    value = float(raw)
    if not 0 <= value <= 1:
        raise argparse.ArgumentTypeError("proportion must be between 0 and 1")
    return value


async def evaluate(args: argparse.Namespace) -> int:
    suite = load_suite(args.fixture)
    plan_sha, hints = (
        load_assisted_hints(args.assisted_hints, suite) if args.assisted_hints else (None, None)
    )
    reports = {}
    failures = {}
    for profile in dict.fromkeys(args.profile or PROFILES):
        report = await evaluate_profile(profile, suite, hints=hints)
        reports[profile] = report
        if problems := gate_failures(report, args):
            failures[profile] = problems
    data = {
        "fixture_sha256": suite.sha256,
        **({"assisted_hints_sha256": plan_sha} if plan_sha else {}),
        "gate": {
            "preset": PREREGISTERED_GATE if args.preregistered else None,
            **{key: getattr(args, key) for key in PREREGISTERED_LIMITS},
            "require_matching": args.require_matching,
            "require_model": args.require_model,
            "expect_model": args.expect_model,
        },
        "profiles": reports,
        "gate_failures": failures,
    }
    encoded = json.dumps(data, indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    return int(bool(failures))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=FIXTURE)
    parser.add_argument("--assisted-hints", type=Path, help="A blinded caller's recorded MCP hints")
    parser.add_argument("--profile", choices=PROFILES, action="append")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--preregistered",
        action="store_true",
        help="Enforce the frozen absolute targets, separate from the bundled regression baseline",
    )
    parser.add_argument("--require-matching")
    parser.add_argument("--require-model", action="store_true")
    parser.add_argument(
        "--expect-model",
        action="append",
        help="Accept only these exact semantic model IDs for all rows (repeatable)",
    )
    parser.add_argument("--min-top1", type=proportion)
    parser.add_argument("--min-top3", type=proportion)
    parser.add_argument("--min-language-top1", type=proportion)
    parser.add_argument("--min-language-top3", type=proportion)
    parser.add_argument("--max-wrong-effect-top1", type=int)
    args = parser.parse_args(argv)
    if args.preregistered:
        if args.assisted_hints is not None:
            parser.error("--preregistered evaluates raw original requests")
        if any(getattr(args, key) is not None for key in PREREGISTERED_LIMITS):
            parser.error("--preregistered does not accept overrides of its absolute thresholds")
        if args.require_matching not in (None, "hybrid"):
            parser.error("--preregistered requires hybrid matching")
        vars(args).update(PREREGISTERED_LIMITS)
        args.require_matching = "hybrid"
        args.require_model = True
    else:
        for key in PREREGISTERED_LIMITS:
            if key.startswith("min_") and getattr(args, key) is None:
                setattr(args, key, 0.0)
    return anyio.run(evaluate, args)


if __name__ == "__main__":
    raise SystemExit(main())
