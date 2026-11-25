"""Publish the matched additional panel from frozen requests, native state and wire receipts."""

import argparse
import hashlib
import json
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.experiments import live_agent_outcomes as grading
from tools.experiments import live_agent_trial as trial
from tools.experiments.live_agent_report import public_text
from tools.experiments.tool_outcome_report import classify
from tools.experiments.tool_outcome_trial import ORIGINAL_SHA, TARGETS, schedule


def load(path):
    return json.loads(path.read_bytes())


def file_sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def archived_product(path, revision):
    with zipfile.ZipFile(path) as archive:
        if archive.comment != revision.encode("ascii"):
            raise ValueError("Unexpected stamped product archive")
        for entry in archive.infolist():
            if not entry.is_dir() and (path.parent / entry.filename).read_bytes() != archive.read(
                entry
            ):
                raise ValueError("Product source differs from its archived revision")
    return file_sha(path)


def primitives(data, path=""):
    if isinstance(data, dict):
        for key, value in data.items():
            yield from primitives(value, f"{path}.{key}" if path else key)
    elif isinstance(data, list):
        for value in data:
            yield from primitives(value, path + "[]")
    else:
        yield path, data


def excerpts(result, facts):
    matches = {}
    for path, value in primitives(result):
        for name, wanted in facts.items():
            # Include only measured values or relative fixture asset names.
            if value == wanted or (
                isinstance(value, str)
                and isinstance(wanted, str)
                and value.startswith("Assets/Harbor/")
                and wanted in value
            ):
                matches.setdefault(name, {"field": path, "value": value})
    return matches


def observed(root, frozen, reviews, *, redact_component_ids=False):
    source = root / "receipts" / frozen["id"] / "row.json"
    row = load(source)
    if any(row[k] != v for k, v in frozen.items()):
        raise ValueError("Receipt does not match the assigned original")
    if (
        row.get("delivered")
        and row.get("prompt_sha256") != hashlib.sha256(frozen["query"].encode("utf-8")).hexdigest()
    ):
        raise ValueError("Delivered prompt bytes differ from the sealed original")
    review = reviews.get(row["id"])
    outcome = classify(row, review)
    if outcome == "awaiting_review":
        raise ValueError("Missing reviewed answer for " + row["id"])
    before, after = row.get("before", {}), row.get("after", {})
    changed = [key for key in before.keys() | after.keys() if before.get(key) != after.get(key)]
    facts = after.get("facts", {})
    if target := TARGETS.get(row["task"]):
        facts = {target[0]: target[1]}
    calls = []
    support = []
    for i, call in enumerate(row["calls"]):
        name = public_text(re.sub(r"(scene/gameobject/)-?\d+", r"\1{id}", call["name"]))
        if redact_component_ids:
            name = re.sub(r"([?&]component_instance_id=)-?\d+", r"\1{id}", name)
        action = (call.get("arguments") or {}).get("action")
        if isinstance(action, str) and re.fullmatch(r"[a-z_]{1,50}", action):
            name += "." + action
        calls.append(name if call["success"] is True else name + " [failed or no reply]")
        if review and i in review["evidence"]:
            support.append(
                {
                    "call_index": i,
                    "values": excerpts(call["result"], facts),
                    "public_reply_sha256": grading._digest(call["result"]),
                }
            )
    return {
        "outcome": outcome,
        "host_status": row["status"],
        "answer": public_text(row["answer"]),
        "before": before.get("facts"),
        "after": after.get("facts"),
        "changed_snapshot_fields": sorted(changed),
        "before_snapshot_sha256": grading._digest(before),
        "after_snapshot_sha256": grading._digest(after),
        "calls": calls,
        "evidence": support,
        "elapsed_seconds": row["seconds"],
        "private_receipt_sha256": file_sha(source),
    }


def provenance(root):
    lanes = sorted(root.glob("lane-*/owned-lane.json"))
    checks = sorted(root.glob("preflight-*.json"))
    if not lanes or len(checks) != len(lanes):
        raise ValueError("Lane count and preflight records disagree")
    catalog = load(checks[0])["inventories"]
    if any(load(p)["inventories"] != catalog for p in checks):
        raise ValueError("Different catalogs across equivalent lanes")
    revision = load(root / "fixture-run.json")["revision"]
    settings = None
    packages = None
    for item in lanes:
        if load(item)["revision"] != revision:
            raise ValueError("Mismatched editor product revision")
        project = item.parent / "project"
        project_settings = {
            p.relative_to(project).as_posix(): file_sha(p)
            for p in (project / "ProjectSettings").rglob("*")
            if p.is_file()
        }
        installed = load(project / "Packages/packages-lock.json")["dependencies"]
        product = installed.pop("com.ykh09242.unity-mcp")
        expected = "file:" + (root / "dependencies/MCPForUnity").resolve().as_posix()
        if product["version"] != expected:
            raise ValueError("The editor resolved a different product directory")
        if settings is not None and (settings != project_settings or packages != installed):
            raise ValueError("Different project settings or non-product packages across lanes")
        settings, packages = project_settings, installed
    return {
        "revision": revision,
        "number_of_editors": len(lanes),
        "actor": load(root / "actor-version.json"),
        "server_archive_sha256": archived_product(root / "product/source.zip", revision),
        "editor_package_archive_sha256": archived_product(root / "dependencies/mcp.zip", revision),
        "project_settings_files": len(settings),
        "project_settings_sha256": grading._digest(settings),
        "non_product_packages": len(packages),
        "non_product_lock_sha256": grading._digest(packages),
        "observer_sha256": file_sha(root / "bin/OutcomeWorld.dll"),
        "editor_host_sha256": file_sha(root / "bin/LaneHost.dll"),
        "catalog_sizes": {
            profile: {k: len(v) for k, v in surfaces.items()}
            for profile, surfaces in catalog.items()
        },
        "catalog_sha256": grading._digest(catalog),
    }


def comparison(baseline, candidate, *, redact_component_ids=False):
    original, updated = provenance(baseline), provenance(candidate)
    for key in (
        "observer_sha256",
        "editor_host_sha256",
        "actor",
        "project_settings_sha256",
        "non_product_lock_sha256",
    ):
        if original[key] != updated[key]:
            raise ValueError("Matched environment differs across products: " + key)
    cases = []
    reviews = [load(root / "reviews.json") for root in (baseline, candidate)]
    for pair in schedule():
        for frozen in pair:
            first = observed(
                baseline, frozen, reviews[0], redact_component_ids=redact_component_ids
            )
            second = observed(
                candidate, frozen, reviews[1], redact_component_ids=redact_component_ids
            )
            if first["before"] and second["before"] and first["before"] != second["before"]:
                raise ValueError("Native facts changed across matched cases")
            cases.append(frozen | {"baseline": first, "candidate": second})
    summary = {}
    for arm in ("baseline", "candidate"):
        summary[arm] = {}
        for profile in ("core", "full"):
            outcomes = [r[arm] for r in cases if r["profile"] == profile]
            summary[arm][profile] = {
                "outcomes": dict(Counter(x["outcome"] for x in outcomes)),
                "calls": sum(len(x["calls"]) for x in outcomes),
            }
    return {
        "originals_sha256": ORIGINAL_SHA,
        "native_editor_version": "6000.0.69f1",
        "protected_snapshot_fields": [
            "activeScene",
            "assetCollateral",
            "configDigest",
            "playing",
            "sceneCollateral",
            "selection",
        ],
        "baseline": original,
        "candidate": updated,
        "summary": summary,
        "cases": cases,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--redact-component-ids",
        action="store_true",
        help="Also mask component IDs in resource query parameters",
    )
    args = parser.parse_args()
    trial.write(
        args.output,
        comparison(args.baseline, args.candidate, redact_component_ids=args.redact_component_ids),
    )


if __name__ == "__main__":
    main()
