"""Audit every shipped public tool via read-only in-process MCP discovery calls."""

import argparse
import asyncio
import hashlib
import inspect
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Literal, get_args, get_origin

ROOT = Path(__file__).resolve().parents[2]
NATIVE_REPORT = Path(__file__).with_name("live_agent_followup_results.json")


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def literal_values(annotation):
    if get_origin(annotation) is Literal:
        return {value for value in get_args(annotation) if isinstance(value, str)}
    return {value for child in get_args(annotation) for value in literal_values(child)}


def native_inventory():
    report = json.loads(NATIVE_REPORT.read_bytes())
    members = report["native_evidence"]["catalog_members"]
    for profile, inventory in members.items():
        if digest(inventory) != report["catalogs"][profile]["sha256"]:
            raise ValueError(f"Pinned native {profile} inventory changed")
    return report["product_tree"], members


async def inspect_catalog(server, mode, entries, capabilities):
    from fastmcp import Client
    from jsonschema import Draft202012Validator

    findings = []
    rows = {}
    async with Client(server, mode=mode, cache=False) as client:

        async def discover(action, **selection):
            result = await client.call_tool("manage_tools", {"action": action, **selection})
            payload = result.structured_content
            if not isinstance(payload, dict) or payload.get("success") is False:
                raise ValueError((action, selection, payload))
            return payload

        wire = {tool.name: tool.input_schema for tool in await client.list_tools()}
        index = await discover("catalog")
        groups = {name: group["name"] for group in index["groups"] for name in group["tools"]}
        if set(wire) != set(groups) or len(groups) != index["total_tools"]:
            raise ValueError("Full in-process catalog and tools/list disagree")
        if not entries.keys() <= groups.keys():
            raise ValueError(
                f"Registered tools missing from full catalog: {entries.keys() - groups.keys()}"
            )

        for name, schema in sorted(wire.items()):
            entry = entries.get(name)
            signature = inspect.signature(entry["func"], eval_str=True) if entry else None
            properties = schema.get("properties", {})
            if signature:
                # FastMCP injects ctx; the routing middleware consumes unity_instance.
                wire_names = set(properties) - {"unity_instance"}
                handler_names = set(signature.parameters) - {"ctx"}
                if wire_names != handler_names:
                    findings.append(
                        [
                            name,
                            None,
                            "wire_handler_parameter_names",
                            sorted(wire_names ^ handler_names),
                        ]
                    )
            selector = properties.get("action", {})
            expected = set(selector.get("enum", ()))
            if "const" in selector:
                expected.add(selector["const"])
            Draft202012Validator.check_schema(schema)
            examples = schema.get("examples", [])
            public_validator = Draft202012Validator(schema)
            for example in examples:
                if not public_validator.is_valid(example):
                    findings.append([name, example.get("action"), "invalid_public_example"])
            operations = {}
            offset = 0
            while True:
                page = await discover("catalog", tool=name, limit=20, offset=offset)
                for card in page["actions"]:
                    action = card["action"]
                    if action in operations:
                        raise ValueError(f"Duplicate action in {name}: {action}")
                    selection = {"operation": action} if action is not None else {}
                    bound = await discover("describe", tool=name, **selection)
                    bound_schema = bound["tool"]["input_schema"]
                    for field in (
                        "action",
                        "required_parameters",
                        "forbidden_parameters",
                        "invoke",
                    ):
                        if bound["operation"].get(field) != card.get(field):
                            findings.append([name, action, "catalog_describe_disagree", field])
                    invocation = card["invoke"]
                    required = set(card["required_parameters"])
                    forbidden = set(card["forbidden_parameters"])
                    bound_required = set(bound_schema.get("required", ()))
                    if not (required | forbidden) <= properties.keys() or required & forbidden:
                        findings.append([name, action, "invalid_parameter_names"])
                    if not required <= bound_required:
                        findings.append(
                            [
                                name,
                                action,
                                "omitted_from_bound_schema",
                                sorted(required - bound_required),
                            ]
                        )
                    if (
                        invocation["tool"] != name
                        or invocation["arguments"].get("action") != action
                    ):
                        findings.append([name, action, "invalid_invocation"])
                    if action is not None:
                        fixed = bound_schema["properties"]["action"]
                        if fixed.get("const") != action or not Draft202012Validator(
                            selector
                        ).is_valid(action):
                            findings.append([name, action, "invalid_selector"])
                    if signature:
                        # Bind public names and required inputs without executing any authored tool.
                        forwarded = dict.fromkeys(required | set(schema.get("required", ())))
                        forwarded.update(invocation["arguments"])
                        if "ctx" in signature.parameters:
                            forwarded["ctx"] = None
                        signature.bind(**forwarded)
                    for example in examples:
                        if example.get("action") == action and not Draft202012Validator(
                            bound_schema
                        ).is_valid(example):
                            findings.append([name, action, "describe_rejects_public_example"])
                    alias = card.get("alias_of")
                    if alias and alias not in expected:
                        findings.append([name, action, "unlisted_alias_target", alias])
                    operations[action] = {
                        "action": action,
                        "required": sorted(required),
                        "forbidden": sorted(forbidden),
                        "purpose_source": card["purpose_source"],
                        "classified_intent": card.get("intent"),
                        "alias_of": alias,
                        "evidence_limit": bool(card.get("evidence_limit")),
                    }
                if page["next_offset"] is None:
                    break
                if page["next_offset"] <= offset:
                    raise ValueError(f"Non-advancing catalog page for {name}")
                offset = page["next_offset"]
            named = set(operations) - {None}
            if expected and expected != named:
                findings.append([name, None, "schema_selectors_disagree", sorted(expected ^ named)])
            if signature and "action" in signature.parameters:
                literals = literal_values(signature.parameters["action"].annotation)
                if literals and literals != named:
                    findings.append(
                        [name, None, "handler_selectors_disagree", sorted(literals ^ named)]
                    )
            if page["total"] != len(operations) or set(page["tool"]["actions"]) != named:
                raise ValueError(f"Tool summary and page count disagree for {name}")
            rows[name] = {
                "group": groups[name],
                "unity_target": entry.get("unity_target") if entry else None,
                "schema_hash": digest(schema),
                "overload_branches": len(schema.get("oneOf", schema.get("anyOf", []))),
                "validated_examples": len(examples),
                "requirements": page["tool"]["requirements"],
                "parameters": sorted(properties),
                "operations": list(operations.values()),
            }
        if set(wire) != {tool.name for tool in await client.list_tools()}:
            raise ValueError("Read-only audit changed the client inventory")

    for cap in capabilities:
        source = {row["action"]: row for row in rows[cap.source]["operations"]}
        for row in rows[cap.name]["operations"]:
            action = row["action"]
            for field in ("required", "forbidden"):
                applicable = set(source[action][field]) & set(rows[cap.name]["parameters"])
                missing = applicable - set(row[field])
                if missing:
                    findings.append(
                        [cap.name, action, f"forwarded_{field}_missing", sorted(missing)]
                    )
    return rows, findings


async def audit():
    from core.config import config
    from fastmcp import FastMCP
    from services.registry import get_registered_tools
    from services.registry.capability_catalog import CAPABILITIES
    from services.tools import register_all_tools
    from transport.execution_receipts import install_execution_receipts

    config.transport_mode = "stdio"
    config.http_remote_hosted = False
    server = FastMCP("offline-tool-contract-census")
    register_all_tools(server)
    install_execution_receipts(server, None)
    entries = {row["name"]: row for row in get_registered_tools()}
    rows = None
    by_protocol = {}
    for mode in ("legacy", "2026-07-28"):
        current, issues = await inspect_catalog(server, mode, entries, CAPABILITIES)
        by_protocol[mode] = {"digest": digest(current), "findings": issues}
        if rows is not None and rows != current:
            raise ValueError("Protocol contract inventories differ")
        rows = current
    revision, native = native_inventory()
    for name, tool in rows.items():
        tool["prior_native_profiles"] = [p for p, v in native.items() if name in v["tools"]]
    actions = [row for tool in rows.values() for row in tool["operations"]]
    return {
        "scope": "Full local in-process discovery. No native invocation or project extensions loaded.",
        "protocols": by_protocol,
        "prior_native_revision": revision,
        "prior_native_only": {p: sorted(set(v["tools"]) - rows.keys()) for p, v in native.items()},
        "summary": {
            "decorated_tools": len(entries),
            "tools": len(rows),
            "groups": len({row["group"] for row in rows.values()} - {None}),
            "ungrouped": sum(row["group"] is None for row in rows.values()),
            "named_actions": sum(row["action"] is not None for row in actions),
            "actionless": sum(row["action"] is None for row in actions),
            "purposes": dict(Counter(row["purpose_source"] for row in actions)),
            "intent_classified": sum(row["classified_intent"] is not None for row in actions),
            "forwarding_tools": len(CAPABILITIES),
        },
        "tools": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-src", type=Path, default=ROOT / "Server/src")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(args.server_src.resolve(strict=True)))
    os.environ["UNITY_MCP_TOOL_PROFILE"] = "full"
    os.environ["UNITY_MCP_DISABLE_TELEMETRY"] = "1"
    report = asyncio.run(audit())
    if args.output:
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("summary", "protocols")}, indent=2))
    return int(any(p["findings"] for p in report["protocols"].values()))


if __name__ == "__main__":
    raise SystemExit(main())
