"""Replay a two-stage agent selection using only public MCP discovery calls."""

from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

import anyio
from fastmcp import Client, FastMCP

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.experiments import universal_discovery_evaluation as public
from tools.experiments.discovery_evaluation_fixture import (
    FIXTURE,
    EvaluationSuite,
    Request,
    load_suite,
)

HOLDOUT_SHA = "8526a396abfeb0b4b1e32a2e17b8d1726604ecbd8aa9aeb7763c5474e4d806c8"


def digest(value):
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(data.encode()).hexdigest()


def read(path):
    return json.loads(path.read_bytes())


def write(path, value):
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )


@dataclass(frozen=True)
class Case:
    key: str
    actor: int
    profile: str
    panel: str
    request: Request


def cases():
    recall = load_suite(FIXTURE.with_name("universal_discovery_read_recall_confirmation.json"))
    activation = load_suite(FIXTURE.with_name("universal_discovery_activation_confirmation.json"))
    menu = json.loads(FIXTURE.with_name("blind_tool_tasks.json").read_bytes())["menu-declaration"]
    oracle = next(r for r in load_suite(FIXTURE).requests if r.task == "menu-path")
    active = next(r for r in recall.requests if r.task == "current_scene" and r.language == "ko")
    existing = [
        *(r for r in recall.requests if r.language in {"ja", "ko"}),
        *(
            r
            for r in activation.requests
            if r.language in {"ja", "es"} and r.task in {"activate_scene", "activate_named_scene"}
        ),
        *(
            replace(oracle, language=lang, query=text)
            for lang, text in menu.items()
            if lang != "en"
        ),
        replace(active, task="short_current_scene", query="현재 활성 씬이 뭐야?"),
        replace(active, task="short_active_scene", query="지금 활성 씬이 뭐야?"),
    ]
    holdout = load_suite(FIXTURE.with_name("agent_family_holdout.json"))
    if len(existing) != 23 or len(holdout.requests) != 36 or holdout.sha256 != HOLDOUT_SHA:
        raise ValueError("The preregistered panel changed")
    rows = [("existing", r) for r in existing] + [("held_out", r) for r in holdout.requests]
    # The same original goes to two different callers and neither packet reveals the profile.
    rows.sort(key=lambda row: digest(row[1].query))
    result = [
        Case(digest([profile, r.query])[:16], (index + 2 * p) % 4, profile, panel, r)
        for index, (panel, r) in enumerate(rows)
        for p, profile in enumerate(public.PROFILES)
    ]
    if len({c.key for c in result}) != len(result):
        raise ValueError("Duplicate evaluation originals")
    return result


@asynccontextmanager
async def catalog_client(profile):
    scope = {"UNITY_MCP_TOOL_PROFILE": profile, "UNITY_MCP_DISABLE_TELEMETRY": "1"}
    denied = AssertionError("Catalog selection crossed the discovery boundary")
    with (
        patch.dict(os.environ, scope),
        patch.object(public.config, "http_remote_hosted", False),
        patch.object(public.PluginHub, "send_command_for_instance", side_effect=denied) as http,
        patch.object(
            public.unity_connection, "_async_send_command_response", side_effect=denied
        ) as stdio,
        patch.object(
            public.unity_connection, "_send_command_with_retry", side_effect=denied
        ) as sync,
        patch("services.registry.task_search._term_evidence", side_effect=denied) as ranking,
    ):
        os.environ.pop("UNITY_MCP_TOOL_GROUPS", None)
        boundary = public.SearchBoundary()
        server = FastMCP("agent-family-evaluation", middleware=[boundary])
        public.register_all_tools(server)
        public.register_all_resources(server)
        async with Client(server, mode="legacy", cache=False) as client:
            before = await public.read_inventory(client, set())
            yield client
            after = await public.read_inventory(client, set())
        if (
            before != after
            or boundary.unexpected_calls
            or any(t.called for t in (http, stdio, sync, ranking))
        ):
            raise ValueError("Discovery changed inventory or attempted ranking or business access")


def binding(card):
    if "tool" in card:
        return ".".join(filter(None, (card["tool"], card["action"])))
    return card.get("uri") or card["uri_template"]


async def pages(client, query, family=None):
    args = {"query": query, "selection": "agent", "limit": 20}
    if family is not None:
        args["family"] = family
    view = "operations" if family is not None else "families"
    rows = []
    while True:
        args["offset"] = len(rows)
        response = (await client.call_tool(public.SEARCH, args)).structured_content
        if (
            response["query"],
            response["matching"],
            response["scope"],
            response["view"],
            response["offset"],
            response.get("family"),
        ) != (query, "catalog", "mcp_context", view, len(rows), family):
            raise ValueError("The public catalog returned a different query, scope or page")
        page = response[view]
        rows.extend(page)
        if response["next_offset"] is None:
            if len(rows) != response["total"]:
                raise ValueError("Incomplete public catalog")
            break
        if not page or response["next_offset"] != len(rows):
            raise ValueError("Catalog pagination did not advance")
    ids = [binding(row) if family is not None else row["family"] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate public bindings or families")
    if family is not None and any(row["advertised"] is not True for row in rows):
        raise ValueError("Unadvertised binding in live reply")
    return rows


def validate_keys(values, expected):
    if not isinstance(values, dict) or set(values) != set(expected):
        raise ValueError("Missing or extra case IDs")


def actor_path(folder, actor, name):
    return folder / f"actor-{actor}-{name}.json"


async def capture(folder, operations):
    all_cases = cases()
    packets = [{"catalogs": {}, "cases": {}} for _ in range(4)]
    choices = {}
    if operations:
        for actor in range(4):
            selected = read(actor_path(folder, actor, "families"))
            directory = read(actor_path(folder, actor, "index"))
            validate_keys(selected, [c.key for c in all_cases if c.actor == actor])
            for key, chosen in selected.items():
                public_ids = {
                    f["family"] for f in directory["catalogs"][directory["cases"][key]["directory"]]
                }
                if (
                    not isinstance(chosen, list)
                    or len(set(chosen)) != len(chosen)
                    or not set(chosen) <= public_ids
                ):
                    raise ValueError(
                        f"Family choice {key} is malformed or outside the public directory"
                    )
            choices.update(selected)
    for profile in public.PROFILES:
        async with catalog_client(profile) as client:
            for c in (c for c in all_cases if c.profile == profile):
                packet = packets[c.actor]
                row = {"query": c.request.query}
                if operations:
                    row["families"] = {}
                    for family in choices[c.key]:
                        cards = await pages(client, c.request.query, family)
                        index = digest(cards)
                        packet["catalogs"][index] = cards
                        row["families"][family] = index
                else:
                    directory = await pages(client, c.request.query)
                    index = digest(directory)
                    packet["catalogs"][index] = directory
                    row["directory"] = index
                packet["cases"][c.key] = row
    for actor, packet in enumerate(packets):
        packet["cases"] = dict(sorted(packet["cases"].items()))
        write(actor_path(folder, actor, "operations" if operations else "index"), packet)


def summarize(rows):
    result = {}
    for profile in public.PROFILES:
        result[profile] = {}
        for panel in ("existing", "held_out"):
            subset = [row for row in rows if (row["profile"], row["panel"]) == (profile, panel)]
            result[profile][panel] = {
                "count": len(subset),
                **{
                    metric: sum(row[metric] for row in subset)
                    for metric in ("covered", "correct_first", "opposite_first", "abstained")
                },
            }
    return result


def record(folder, server):
    all_cases = cases()
    records, packets = [], []
    for actor in range(4):
        index = read(actor_path(folder, actor, "index"))
        operations = read(actor_path(folder, actor, "operations"))
        families = read(actor_path(folder, actor, "families"))
        first = read(actor_path(folder, actor, "choices"))
        assigned = [c for c in all_cases if c.actor == actor]
        for data in (index["cases"], operations["cases"], families, first):
            validate_keys(data, [c.key for c in assigned])
        packets.append(
            {
                "actor": actor,
                "directory_sha256": digest(index),
                "families_sha256": digest(families),
                "operations_sha256": digest(operations),
                "choices_sha256": digest(first),
            }
        )
        for c in assigned:
            op = operations["cases"][c.key]
            if (
                index["cases"][c.key]["query"] != c.request.query
                or op["query"] != c.request.query
                or list(op["families"]) != families[c.key]
            ):
                raise ValueError("Original request or sealed family choices changed")
            offered = {
                binding(card)
                for catalog in op["families"].values()
                for card in operations["catalogs"][catalog]
            }
            choice = first[c.key]
            if choice is not None and (not isinstance(choice, str) or choice not in offered):
                raise ValueError(f"First choice {c.key} is outside the offered bindings")
            records.append(
                {
                    "id": c.key,
                    "actor": actor,
                    "profile": c.profile,
                    "panel": c.panel,
                    "language": c.request.language,
                    "task": c.request.task,
                    "families": families[c.key],
                    "offered": len(offered),
                    "first": choice,
                    "covered": bool(c.request.expected & offered),
                    "correct_first": choice in c.request.expected,
                    "opposite_first": choice in c.request.wrong_effect,
                    "abstained": choice is None,
                }
            )
    return {
        "schema": 1,
        "server_revision": server,
        "holdout_sha256": HOLDOUT_SHA,
        "model": "gpt-6.2-sol",
        "reasoning_effort": "medium",
        "packets": packets,
        "summary": summarize(records),
        "choices": records,
    }


async def run(args):
    args.folder.mkdir(parents=True, exist_ok=True)
    if args.step in ("directory", "operations"):
        await capture(args.folder, args.step == "operations")
        return
    if not args.result:
        raise ValueError("A result path is required")
    if args.step == "raw":
        reports = {}
        for profile in public.PROFILES:
            sample = [c for c in cases() if c.profile == profile]
            report = await public.evaluate_profile(
                profile, EvaluationSuite(HOLDOUT_SHA, tuple(c.request for c in sample))
            )
            if (
                not report["inventory_unchanged"]
                or report["native_dispatch_attempts"]
                or report["unexpected_business_calls"]
            ):
                raise ValueError("Raw search crossed the discovery boundary")
            reports[profile] = {}
            for panel in ("existing", "held_out"):
                rows = [
                    row for c, row in zip(sample, report["rows"], strict=True) if c.panel == panel
                ]
                reports[profile][panel] = {
                    k: v
                    for k, v in public.summarize(rows).items()
                    if k in {"count", "top1", "top3", "wrong_effect_top1", "available"}
                }
        write(args.result, reports)
        return
    if args.step == "verify":
        saved = read(args.result)
        assigned = {row["id"]: row for row in saved["choices"]}
        validate_keys(assigned, (c.key for c in cases()))
        if len(assigned) != len(saved["choices"]):
            raise ValueError("Duplicate recorded choice")
        for actor in range(4):
            rows = [assigned[c.key] for c in cases() if c.actor == actor]
            for file, key in (("families", "families"), ("choices", "first")):
                write(actor_path(args.folder, actor, file), {row["id"]: row[key] for row in rows})
        await capture(args.folder, False)
        await capture(args.folder, True)
        if saved != record(args.folder, saved["server_revision"]):
            raise ValueError("Live replay differs from the recorded study")
    else:
        if not args.server or len(args.server) != 40:
            raise ValueError("Record the immutable server revision")
        result = record(args.folder, args.server)
        write(args.result, result)
        print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("step", choices=("directory", "operations", "score", "verify", "raw"))
    parser.add_argument("folder", type=Path)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--server")
    anyio.run(run, parser.parse_args())
