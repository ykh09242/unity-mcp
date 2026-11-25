"""Publish per-case native outcomes without distributing the private editor and host transcripts."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.experiments import live_agent_outcomes as outcomes
from tools.experiments import live_agent_trial as trial


def public_text(text):
    # In free-form answers absolute local file links can contain the machine's account name.
    text = re.sub(
        r"(?i)(?<![a-z0-9_])(?:file:/+)?[a-z]:[\\/][^\n\r<>|\"'\x60)\]]+", "[local path]", text
    )
    return re.sub(r"(?i)(?:https?://)?(?:localhost|127\.0\.0\.1):\d+", "[loopback]", text)


def public_value(value):
    if isinstance(value, dict):
        return {key: public_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [public_value(item) for item in value]
    return public_text(value) if isinstance(value, str) else value


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def bind(call):
    args = call.get("arguments") or {}
    action = args.get("action")
    name = call["name"]
    return f"{name}.{action}" if isinstance(action, str) and action else name


def native_blockers(calls):
    issues = []
    for i, call in enumerate(calls):
        uri = "mcpforunity://tool-diagnostics"
        if call["name"] != uri and not call["name"].startswith(uri + "/"):
            continue
        result = call.get("result")
        if not isinstance(result, dict):
            continue
        native = (result.get("data") or {}).get("native") or {}
        for row in native.get("rows", []):
            if row.get("state") in {"disabled", "blocked", "unavailable"}:
                issues.append(
                    {
                        "index": i,
                        **{k: row.get(k) for k in ("tool_name", "stage", "state", "reason")},
                    }
                )
    return issues


def wire_error(result):
    if not isinstance(result, dict):
        return {"message": public_text(result)[:600]} if isinstance(result, str) else {}
    detail = result.get("error")
    fields = detail if isinstance(detail, dict) else result
    message = detail if isinstance(detail, str) else fields.get("message")
    error = {"message": public_text(message)[:600]} if isinstance(message, str) else {}
    if isinstance(fields.get("code"), (int, str)):
        error["code"] = fields["code"]
    return error


def public_case(row):
    score = outcomes.score(row)
    before = row.get("before") or {}
    after = row.get("after") or {}
    facts = set(outcomes.REQUIRED[row["task"]])
    if row["task"] in outcomes.ACTIVE | outcomes.ACTIVATE | {"scene_list"}:
        facts.update(("activeScene", "loadedScenes"))
    if row["task"] == "menu-path":
        facts.add("frameDebuggerWindows")
    if row["task"] == "installed_package":
        facts.add("registryRecord")
    calls = row.get("calls", [])
    changes = sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))
    short_calls = []
    for call in calls:
        entry = {"binding": bind(call), "ok": call.get("success")}
        if entry["ok"] is False:
            error = wire_error(call.get("result") or call.get("excerpt"))
            if error:
                entry["error"] = error
        if entry["binding"] in {"search_unity_tools", "manage_tools.search"}:
            args = call.get("arguments") or {}
            entry.update(selection=args.get("selection", "ranked"), family=args.get("family"))
        short_calls.append(entry)
    return public_value(
        {
            **score,
            "query": row["query"],
            "prompt_sha256": row.get("prompt_sha256"),
            "seconds": row.get("seconds"),
            "status": row["status"],
            "answer": row.get("answer", ""),
            "decision": (row.get("review") or {}).get("reason"),
            "stage_detail": row.get("stage_detail"),
            "calls": short_calls,
            "outside_integrations": row.get("other_integrations", []),
            "host_actions": {
                k: v
                for k, v in row.get("host_items", {}).items()
                if k in {"command_execution", "web_search", "file_change"}
            },
            "host_failures": [
                {k: call.get(k) for k in ("server", "tool", "status")}
                | {"error": wire_error(call.get("error")) or None}
                for call in row.get("host_calls", [])
                if call.get("status") == "failed" or call.get("error")
            ],
            "diagnostic_observations": native_blockers(calls),
            "target_before": {k: before[k] for k in sorted(facts & before.keys())},
            "target_after_changes": {k: after.get(k) for k in sorted(facts.intersection(changes))},
            "changed_native_fields": changes if before and after else None,
            "state_sha256": {"before": digest(before), "after": digest(after)}
            if before and after
            else {},
            "attempts": row.get("attempts", []),
        }
    )


def counts(rows):
    groups = {}
    for row in rows:
        for group in (row["profile"], f"{row['profile']}/{row['panel']}"):
            count = groups.setdefault(group, Counter())
            count["scheduled"] += 1
            count["delivered"] += int(row["delivered"])
            count[row["outcome"]] += 1
            count["observed"] += int(row["observed"])
    return dict(sorted(groups.items()))


def read_ledger(root, row, observer):
    case_id = row["id"]
    found = [
        path
        for path in root.glob(f"reviews-*/{case_id}.json")
        if (path.parent.name == "reviews-registry") == (observer == "registered_package")
    ]
    if len(found) > 1:
        raise ValueError(f"Conflicting independent reviews for {case_id}")
    if found:
        row["review"] = json.loads(found[0].read_bytes())
    return row


def native_evidence(root, gate_root, registry_root, controls):
    preflight = json.loads((root / "fixture-preflight.json").read_bytes())
    control = json.loads((controls / "control-summary.json").read_bytes())
    for directory, artifacts in (
        (root, preflight["evidence_sha256"].items()),
        (controls, ((v["file"], v["sha256"]) for v in control["versions"])),
    ):
        for filename, expected in artifacts:
            if (
                Path(filename).name != filename
                or hashlib.sha256((directory / filename).read_bytes()).hexdigest() != expected
            ):
                raise ValueError("Independent native control receipt mismatch")
    corrected = json.loads((gate_root / "gate-v2-preflight.json").read_bytes())
    configs = set(preflight["configuration_sha256"])
    for probe in corrected:
        before, after = probe["prepare"], probe["snapshot"]
        if not before["ok"] or not after["ok"] or before["state"] != after["state"]:
            raise ValueError("Corrected observer failed the stable-state preflight")
        if (
            before["state"]["configDigest"] not in configs
            or before["state"]["gateObserverVersion"] != 2
        ):
            raise ValueError("Corrected lanes used different settings or observer")
    actor = json.loads((root / "actor-version.json").read_bytes())
    if any(
        actor != json.loads((directory / "actor-version.json").read_bytes())
        for directory in (gate_root, registry_root)
    ):
        raise ValueError("The live trials used different actor executables")
    registry_files = {
        name: (registry_root / name).read_bytes()
        for name in ("registry-evidence.json", "registry-preflight.json")
    }
    registered = json.loads(registry_files["registry-evidence.json"])
    registry_probe = json.loads(registry_files["registry-preflight.json"])
    if (
        registry_probe["officialRegistryEvidenceSha256"]
        != hashlib.sha256(registry_files["registry-evidence.json"]).hexdigest()
        or registry_probe["originalArchiveSha256"] != preflight["frozen_product_archive_sha256"]
        or set(registry_probe["packageLockDelta"]) != set(registered["packages"])
        or registry_probe["identicalOtherPackages"] + len(registered["packages"])
        != preflight["installed_package_inventory_count"]
    ):
        raise ValueError("Registry preflight was not bound to the frozen project and records")
    for pair in registry_probe["native"].values():
        a, b = pair["prepare"], pair["snapshot"]
        if not a["ok"] or not b["ok"] or a["state"] != b["state"]:
            raise ValueError("Registry preflight did not provide stable native state")
        state = a["state"]
        if state["registryRecord"] != registered["packages"][state["targetPackage"]["name"]]:
            raise ValueError("Native registry preflight disagrees with independent evidence")
    return {
        "actor": actor,
        "editor": {
            k: preflight[k]
            for k in (
                "unity",
                "native_prepare_snapshot_original_count",
                "stable",
                "configuration_sha256",
                "frozen_product_archive_sha256",
                "installed_package_inventory_count",
                "evidence_sha256",
            )
        },
        "packages": [
            {k: p[k] for k in ("name", "version", "source")} for p in preflight["packages"]
        ],
        "controls": control["versions"],
        "corrected_observers": sorted({p["observer_sha256"] for p in corrected}),
        "registry": {
            "upstream": registered["registry"],
            "installed_registry": public_text(registered["installedRegistry"]),
            "packages": public_value(registered["packages"]),
            "identical_other_packages": registry_probe["identicalOtherPackages"],
            "external_downloads_blocked": registry_probe["noExternalPackageDownloads"],
            "evidence_sha256": {
                name: hashlib.sha256(content).hexdigest()
                for name, content in registry_files.items()
            },
        },
    }


def build(root, *, final, gate_root=None, registry_root=None):
    planned = [row for pair in trial.pairs() for row in pair]
    gate_root = gate_root or root.with_name("live-agent-gate-v2-20261125")
    registry_root = registry_root or root.with_name("live-agent-registry-20261125")
    result = []
    original_gates = []
    original_registry = []
    corrections = {
        "gate_sensor_trigger": (gate_root, "corrected_collider", original_gates),
        "installed_package": (registry_root, "registered_package", original_registry),
    }
    for spec in planned:
        path = root / "receipts-reviewed" / spec["id"] / "row.json"
        if not path.is_file():
            if final:
                raise ValueError(f"Scheduled case is missing: {spec['id']}")
            continue
        row = json.loads(path.read_bytes())
        observer = "first"
        if spec["task"] in corrections:
            correction_root, label, originals = corrections[spec["task"]]
            first = outcomes.score(row)
            historical = {
                k: first.get(k)
                for k in (
                    "id",
                    "profile",
                    "outcome",
                    "stage",
                    "reason",
                    "target_reached",
                    "evidence_sha256",
                    "answer_sha256",
                )
            }
            if spec["task"] == "installed_package":
                package = (row.get("before") or {}).get("targetPackage") or {}
                historical["installed"] = {k: package.get(k) for k in ("name", "version", "source")}
            originals.append(historical)
            replacement = correction_root / "receipts-reviewed" / spec["id"] / "row.json"
            if replacement.exists():
                row = json.loads(replacement.read_bytes())
                observer = label
            elif final:
                raise ValueError(f"Missing corrected {label} outcome: {spec['id']}")
        if spec["task"] not in corrections or observer != "first":
            row = read_ledger(root, row, observer)
        if any(row[k] != v for k, v in spec.items()):
            raise ValueError(f"Receipt differs from the frozen manifest: {spec['id']}")
        result.append(public_case(row) | {"observer": observer})
    report = {
        "version": 1,
        "product_tree": "3973b243680eede8639343694edcb075c060b3e5",
        "new_originals_sha256": trial.NEW_SHA,
        "model": trial.MODEL,
        "effort": trial.EFFORT,
        "catalogs": {},
        "summary": counts(result),
        "cases": result,
        "first_collider_observations": original_gates,
        "first_registry_observations": original_registry,
    }
    inventories = [
        p
        for run_root in (root, gate_root, registry_root)
        for p in (run_root / "receipts-reviewed").glob("lane-*-inventories.json")
    ]
    for p in sorted(inventories):
        catalogs = json.loads(p.read_bytes())
        for profile, inventory in catalogs.items():
            fingerprint = {k: len(v) for k, v in inventory.items()} | {"sha256": digest(inventory)}
            if report["catalogs"].setdefault(profile, fingerprint) != fingerprint:
                raise ValueError(f"Catalog differs between lanes: {profile}")
    if final:
        report["native_evidence"] = native_evidence(
            root,
            gate_root,
            registry_root,
            root.with_name("live-agent-controls-20261125") / "calibration",
        )
        verify(report)
    return report


def verify(report):
    planned = {r["id"]: r for pair in trial.pairs() for r in pair}
    rows = report["cases"]
    if len(rows) != len(planned) or {r["id"] for r in rows} != set(planned):
        raise ValueError("Per-case coverage differs from the 142 scheduled cases")
    if report["summary"] != counts(rows) or report["new_originals_sha256"] != trial.NEW_SHA:
        raise ValueError("Summary or original seal differs")
    for row in rows:
        spec = planned[row["id"]]
        for field in ("profile", "task", "panel", "language", "query"):
            if row[field] != spec[field]:
                raise ValueError(f"Case identity mismatch: {row['id']}")
        if (
            row["delivered"]
            and row["prompt_sha256"] != hashlib.sha256(spec["query"].encode()).hexdigest()
        ):
            raise ValueError(f"Delivered prompt mismatch: {row['id']}")
        if row["outcome"] not in {"pass", "fail", "unobserved", "incomplete"}:
            raise ValueError(f"Outstanding substantive review: {row['id']}")
        if row["observed"] != (row["outcome"] in {"pass", "fail"}):
            raise ValueError(f"Invalid observed status: {row['id']}")
        if row["outside_integrations"] or row["host_actions"]:
            raise ValueError(f"Observed action outside the declared Unity trial: {row['id']}")
        for receipt in row["positive_receipts"]:
            index = receipt["index"]
            if (
                not 0 <= index < len(row["calls"])
                or row["calls"][index]["ok"] is not True
                or not re.fullmatch(r"[0-9a-f]{64}", receipt["sha256"])
            ):
                raise ValueError(f"Invalid supporting tool receipt: {row['id']}")
        if row["outcome"] == "pass" and (
            not row["positive_receipts"]
            or not row["delivered"]
            or not row["answer"]
            or not row["state_sha256"]
            or (row["task"] in outcomes.EDITS and row["native_effect"] is not True)
        ):
            raise ValueError(f"Missing evidence for a reported completion: {row['id']}")
    if (
        set(report["catalogs"]) != {"core", "full"}
        or report["catalogs"]["core"]["tools"] >= report["catalogs"]["full"]["tools"]
    ):
        raise ValueError("Two distinct live catalogs are required")
    gate_ids = {r["id"] for r in rows if r["task"] == "gate_sensor_trigger"}
    if (
        len(gate_ids) != 12
        or {r["id"] for r in report["first_collider_observations"]} != gate_ids
        or {r["id"] for r in rows if r["observer"] == "corrected_collider"} != gate_ids
    ):
        raise ValueError("All twelve collider cases require independent corrected observations")
    registry_ids = {r["id"] for r in rows if r["task"] == "installed_package"}
    first_registry = report["first_registry_observations"]
    if (
        len(registry_ids) != 4
        or {r["id"] for r in first_registry} != registry_ids
        or any(r["reason"] != "registry_record_unavailable" for r in first_registry)
        or {r["id"] for r in rows if r["observer"] == "registered_package"} != registry_ids
    ):
        raise ValueError(
            "All four registry cases require independent registered-package observations"
        )
    native = report["native_evidence"]
    registered = native["registry"]
    for row in rows:
        if row["observer"] != "registered_package":
            continue
        state = row["target_before"]
        package = state["targetPackage"]
        independent = registered["packages"].get(package["name"])
        if (
            not independent
            or state.get("registryRecord") != independent
            or package.get("source") != "Registry"
            or package.get("registry") != registered["installed_registry"]
            or any(
                package.get(k) != independent.get(k)
                for k in ("name", "version", "displayName", "description")
            )
        ):
            raise ValueError(f"Independent registry entry differs from native package: {row['id']}")
    if native["editor"]["stable"] != 71 or native["actor"]["model"] != trial.MODEL:
        raise ValueError("Native calibration or actor provenance mismatch")
    v2 = next(v for v in native["controls"] if v["version"] == 2)
    control = {v["operation"]: v for v in v2["controls"]}
    if (
        native["corrected_observers"] != [v2["observer_sha256"]]
        or control["box-set"]["changed_observer_fields"] != ["boxTrigger"]
        or "boxSharedMaterialId" not in control["box-getter"]["changed_observer_fields"]
    ):
        raise ValueError("Corrected collider controls do not distinguish the requested change")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("progress", "publish", "verify"))
    parser.add_argument("--root", type=Path)
    parser.add_argument(
        "--gate-root", type=Path, help="Private replacement collider trial directory"
    )
    parser.add_argument(
        "--registry-root", type=Path, help="Private registry-backed trial directory"
    )
    parser.add_argument("--result", type=Path, default=trial.HERE / "live_agent_results.json")
    args = parser.parse_args()
    if args.action == "verify":
        report = json.loads(args.result.read_bytes())
        verify(report)
    else:
        if not args.root:
            parser.error("--root must refer to the private trial directory")
        report = build(
            args.root,
            final=args.action == "publish",
            gate_root=args.gate_root,
            registry_root=args.registry_root,
        )
        if args.action == "publish":
            trial.write(args.result, report)
    print(json.dumps(report["summary"], sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
