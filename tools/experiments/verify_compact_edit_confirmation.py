"""Verify the matched native edit confirmation against its immutable follow-up."""

import argparse
import json
import sys
from pathlib import Path
from zipfile import ZipFile

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.experiments import verify_compact_edit_followup as followup
from tools.experiments.verify_agent_outcome_holdout import canonical, digest, require, summary
from tools.experiments.verify_compact_edit import read


def verified_archives(work, pins):
    sources = {}
    for key, directory, archive in (
        ("package", work / "dependencies", "mcp.zip"),
        ("runtime", work / "product", "source.zip"),
    ):
        members = {}
        with ZipFile(directory / archive) as source:
            require(source.comment.decode("ascii") == pins[key], "Product revision differs")
            for entry in source.infolist():
                if entry.is_dir():
                    continue
                path = (directory / entry.filename).resolve()
                require(path.is_relative_to(directory.resolve()), "Unexpected archive member")
                content = source.read(entry)
                require(path.read_bytes() == content, "Extracted product differs from archive")
                members[entry.filename] = digest(content)
        sources[key] = {"files": len(members), "sha256": canonical(members)}
    return sources


def host_tool_calls(raw):
    counts = {}
    for call in raw["host_calls"]:
        status = call["status"]
        require(status in {"completed", "failed"}, "Unexpected host tool status")
        counts[status] = counts.get(status, 0) + 1
    return counts


def discovery_use(raw):
    counts = {}
    for call in raw["calls"]:
        if call["name"] != "manage_tools":
            continue
        args = call["arguments"]
        action = args.get("action")
        if action in {"describe", "prepare", "list_instances"}:
            counts[action] = counts.get(action, 0) + 1
        if action == "prepare" and "exact_text" in args:
            counts["prepare_with_exact_text"] = counts.get("prepare_with_exact_text", 0) + 1
    return counts


def sdk_controls(raw):
    calls = raw["calls"]
    instances = calls["instances"]["result"]
    inspect = calls["inspect"]["result"]
    card = calls["describe"]["result"].get("operation", {})
    offered = card.get("prepare_call", {})
    prepared = calls.get("prepare", {}).get("result", {})
    outbound = prepared.get("prepared_call", {})
    args = outbound.get("arguments", {})
    target = calls["inspect"]["arguments"]["material_path"]
    guard = calls["mismatch"]["result"]
    return {
        "receipt_sha256": canonical(raw),
        "listed_owned_editor": instances.get("success") is True
        and [item["id"] for item in instances.get("instances", [])] == [raw["instance"]],
        "read_live_material": inspect.get("success") is True
        and inspect.get("data", {}).get("render_queue") == 2011
        and inspect.get("data", {}).get("material", {}).get("asset_path") == target,
        "provided_prepare_call": offered.get("tool") == "manage_tools"
        and offered.get("arguments", {}).get("operation") == "set_render_state",
        "prepared_material_edit": prepared.get("ready") is True
        and outbound.get("tool") == "manage_material"
        and args.get("action") == "set_render_state"
        and args.get("material_path") == target
        and args.get("render_state") == {"render_queue": 2457},
        "material_unchanged_by_preparation": raw["material_after"] == raw["material_before"],
        "refused_text_mismatch": guard.get("ready") is False
        and calls["mismatch"]["arguments"]["tool"] == "ui_source"
        and "prepared_call" not in guard
        and guard.get("exact_text_errors") == [{"index": 0, "reason": "mismatch"}],
        "mismatch_control_target_unavailable": guard.get("ready") is False
        and guard.get("error") == "The tool is unavailable on this connection.",
        "tooltip_unchanged_on_disk_and_in_editor": raw["tooltip_unchanged"]
        and raw["tooltip_disk"]["before"] == raw["tooltip_disk"]["after"],
    }


def sdk_literal_control(raw):
    calls = raw["calls"]
    target = "ui_source"
    requested = calls["match"]["arguments"]
    mismatch = calls["mismatch"]["arguments"]
    expected = requested["exact_text"][0]["expected"]
    outbound = requested["arguments"] | {"action": "patch_source"}
    authorized_draft = (
        target in raw["advertised_tools"]
        and requested["tool"] == mismatch["tool"] == target
        and requested["action"] == mismatch["action"] == "prepare"
        and requested["operation"] == mismatch["operation"] == "patch_source"
        and requested["exact_text"] == mismatch["exact_text"]
        and requested["exact_text"][0]["path"] == ["source_changes", "tooltip"]
        and requested["query"] == mismatch["query"]
        and expected == "Opens the handover log"
        and expected in requested["query"]
        and requested["arguments"]["source_changes"]["tooltip"] == expected
        and mismatch["arguments"]["source_changes"]["tooltip"] == expected + "."
    )
    failed = calls["mismatch"]["result"]
    passed = calls["match"]["result"]
    return {
        "receipt_sha256": canonical(raw),
        "target": target,
        "listed_and_used_target": authorized_draft,
        "refused_mismatch": authorized_draft
        and failed.get("ready") is False
        and "prepared_call" not in failed
        and failed.get("exact_text_errors") == [{"index": 0, "reason": "mismatch"}],
        "prepared_exact_match": authorized_draft
        and passed.get("ready") is True
        and passed.get("exact_text_checked") == 1
        and passed.get("prepared_call") == {"tool": target, "arguments": outbound},
        "preserved_editor_and_disk": raw["before"] == raw["after"]
        and raw["disk"]["before"] == raw["disk"]["after"],
        "control_editor_stopped": raw["editor_stopped"],
        "shared_preference_count": raw["shared_preference_count"],
        "shared_preferences_sha256": raw["shared_preferences"],
    }


def verify(data, archive, pins, published):
    verdicts = followup.verify_matched_run(data, archive, pins, followup.PUBLISHED, "followup")
    run = data["runs"]["followup"]
    first = run["separate_sdk_controls"]
    control = run["sdk_literal_control"]
    require(
        first["mismatch_control_target_unavailable"] is True
        and first["refused_text_mismatch"] is False
        and control["target"] == "ui_source"
        and all(
            control[key] is True
            for key in (
                "listed_and_used_target",
                "refused_mismatch",
                "prepared_exact_match",
                "preserved_editor_and_disk",
                "control_editor_stopped",
            )
        )
        and control["shared_preference_count"] == run["shared_preferences"]["count"]
        and control["shared_preferences_sha256"] == run["shared_preferences"]["before_sha256"],
        "Advertised exact-text comparison, initial negative, or cleanup differs",
    )
    for panel in run["panels"].values():
        for row in panel["cases"]:
            calls = row["host_tool_calls"]
            require(
                set(calls) <= {"completed", "failed"}
                and all(type(value) is int and value > 0 for value in calls.values())
                and sum(calls.values()) == row["host_item_types"].get("mcp_tool_call", 0),
                "Host tool denominator differs",
            )
    require(canonical(data) == published, "Frozen confirmation payload differs")
    return verdicts


def verify_private(data, root, verdicts):
    followup.verify_private(data, root, verdicts)
    work = root / "followup"
    run = data["runs"]["followup"]
    require(read(work / "actor-version.json") == run["actor"], "Installed actor differs")
    require(
        sdk_controls(read(work / "sdk-controls.json")) == run["separate_sdk_controls"],
        "Separate SDK controls differ",
    )
    require(
        sdk_literal_control(read(work / "sdk-literal-control.json")) == run["sdk_literal_control"],
        "Advertised literal-control target, results or protected state differs",
    )
    require(
        verified_archives(work, run["product_pins"]) == run["verified_product_archives"],
        "Product archive contents differ from the confirmed revisions",
    )
    require(
        read(work / "frozen-sources.json") == run["verified_product_archives"],
        "Pre-submission package and server receipt differs",
    )
    for label, panel in run["panels"].items():
        for row in panel["cases"]:
            folder = work / ("receipts-" + label) / row["id"]
            require(
                [path.name for path in folder.glob("attempt-*")] == ["attempt-1"],
                "Repeated or missing attempt",
            )
            raw = read(folder / "row.json")
            require(
                raw["model"] == run["actor"]["model"]
                and raw["effort"] == run["actor"]["reasoning_effort"]
                and host_tool_calls(raw) == row["host_tool_calls"]
                and discovery_use(raw) == row["discovery_use"],
                "Host execution conditions differ",
            )


def main():
    directory = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "report", type=Path, nargs="?", default=directory / "compact_edit_confirmation_results.json"
    )
    parser.add_argument("--private-root", type=Path)
    parser.add_argument("--require-perfect", action="store_true")
    args = parser.parse_args()
    data = read(args.report)
    archive = read(directory / "compact_edit_followup_results.json")
    verdicts = verify(data, archive, FROZEN_PINS, PUBLISHED)
    if args.private_root:
        verify_private(data, args.private_root, verdicts)
    totals = {
        label: summary(panel["cases"])
        for label, panel in data["runs"]["followup"]["panels"].items()
    }
    print(json.dumps(totals, indent=2))
    if args.require_perfect and not followup.perfect(totals):
        raise SystemExit(1)


FROZEN_PINS = {
    "package": "194d3dce1120b7ae05220a447e10b9fbbf7ece6f",
    "runtime": "91527cc5c6e302fee54e4604a60fc77f62e8e3c0",
    "skill": "bf612cb3b23957fc6eb42067850ac1e29d16ade1",
}
PUBLISHED = "2e4856537d521beccf9f3f6a8562ee9b16e7ded505d2437f23beb1cd8d941bd3"

if __name__ == "__main__":
    main()
