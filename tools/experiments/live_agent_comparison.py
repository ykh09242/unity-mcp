"""Publish or verify the matched repeat of the original unhinted Unity trial."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.experiments import live_agent_report as report
from tools.experiments import live_agent_trial as trial

BASELINE_SHA = "78c152defaf3195a79189093bb53882c02e25bb37610e877b59a92e1d66500ee"
CANDIDATE = "fe9fb9a70e15c86351f3f83de11308f6ee6f0d6f"
OUT = trial.HERE / "live_agent_followup_results.json"
PACKAGE_URIS = {
    "resources": "mcpforunity://project/packages",
    "templates": "mcpforunity://project/packages/{package_name}",
}


def file_sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_baseline():
    path = trial.HERE / "live_agent_results.json"
    if file_sha(path) != BASELINE_SHA:
        raise ValueError("The published baseline has changed")
    baseline = json.loads(path.read_bytes())
    report.verify(baseline)
    return baseline


def compare(previous, current):
    old = {case["id"]: case for case in previous}
    transitions = {}
    differences = {}
    local_differences = Counter()
    for row in current:
        before = old[row["id"]]
        if before["query"] != row["query"] or before["profile"] != row["profile"]:
            raise ValueError(f"Different original or profile for {row['id']}")
        cell = transitions.setdefault(row["profile"], Counter())
        cell[f"{before['outcome']}_to_{row['outcome']}"] += 1
        changed = set()
        for field, value in before["target_before"].items():
            if field not in row["target_before"] or row["target_before"][field] != value:
                changed.add(field)
        # Unity assigns instance IDs and imported asset GUIDs in each fresh editor.
        # The audio collateral digest includes the imported clip's GUID.
        local_fields = {"boxId", "audioCollateral"}
        local_differences.update(changed & local_fields)
        changed -= local_fields
        if changed:
            differences[row["id"]] = sorted(changed)
    return {
        "by_profile": transitions,
        "different_starting_facts": differences,
        "editor_local_starting_differences": local_differences,
    }


def native_proof(roots, native_run):
    proof = {}
    archives = set()
    for kind, root in roots.items():
        path = root / "native-smoke.json"
        smoke = json.loads(path.read_bytes())
        lanes = {}
        for lane, identity in smoke["identity"].items():
            if (
                not identity["ok"]
                or identity["commit"] != CANDIDATE
                or smoke["revision"] != CANDIDATE
            ):
                raise ValueError(f"Unexpected native Unity project in {kind}/{lane}")
            lanes[lane] = {
                key: identity[key]
                for key in (
                    "archive_sha256",
                    "host_sha256",
                    "observer_sha256",
                    "unchanged_nonproduct_packages",
                )
            }
            settings = identity["settings_file_sha256"]
            lanes[lane]["settings_files"] = len(settings)
            lanes[lane]["settings_sha256"] = report.digest(settings)
            archives.add(identity["archive_sha256"])
        checks = []
        for check in smoke["checks"]:
            if check["status"] == "PASS" and check["expected"] != check["observed"]:
                raise ValueError(
                    f"Native control {check['criterion']} contradicts its observations"
                )
            checks.append(
                report.public_value(
                    {key: check[key] for key in ("criterion", "invocation", "status", "observed")}
                )
            )
        members = smoke["inventories"]
        if proof.setdefault("catalog_members", members) != members:
            raise ValueError("Different public catalogs on the two native fixtures")
        proof[kind] = {"receipt_sha256": file_sha(path), "lanes": lanes, "checks": checks}
    run = json.loads((native_run / "invocation.json").read_bytes())
    xml = native_run / "results.xml"
    tests = [
        {"name": c.attrib["fullname"], "result": c.attrib["result"]}
        for c in ET.parse(xml).iter("test-case")
    ]
    pref = (native_run / "preference-before.txt").read_bytes()
    if (
        archives != {run["archive"].lower()}
        or run["revision"] != CANDIDATE
        or not tests
        or any(t["result"] != "Passed" for t in tests)
        or pref != (native_run / "preference-after.txt").read_bytes()
        or b"REG_DWORD" not in pref
        or not re.search(
            rb"MCPForUnity\.ToolEnabled\.manage_terrain\S*\s+REG_DWORD\s+0x0\s*$", pref, re.M
        )
    ):
        raise ValueError(
            "Supplemental tests, candidate archive or preserved operator preference differ"
        )
    proof["native_tests"] = {
        "result_sha256": file_sha(xml),
        "test_archive_sha256": run["testsArchiveSha256"].lower(),
        "preference_sha256": hashlib.sha256(pref).hexdigest(),
        "terrain_enabled": False,
        "tests": tests,
    }
    return proof


def check_inventories(members, current, previous):
    for profile in ("core", "full"):
        inventory = members[profile]
        sizes = {key: len(value) for key, value in inventory.items()}
        if sizes | {"sha256": report.digest(inventory)} != current[profile]:
            raise ValueError(f"Native and actor inventory differ for {profile}")
        old = {key: value.copy() for key, value in inventory.items()}
        for key, uri in PACKAGE_URIS.items():
            if old[key].count(uri) != 1:
                raise ValueError(f"Missing or duplicated new package URI in {profile}")
            old[key].remove(uri)
        if report.digest(old) != previous[profile]["sha256"]:
            raise ValueError(f"Catalog differs from {profile} baseline beyond the two package URIs")


def check_native(native, baseline):
    common = set()
    for kind, expected_lanes in (("regular", 4), ("registry", 1)):
        group = native[kind]
        if set(group["lanes"]) != {f"lane-{i}" for i in range(expected_lanes)}:
            raise ValueError("Missing native lane identity")
        for lane in group["lanes"].values():
            if lane["unchanged_nonproduct_packages"] != 44 or lane["settings_files"] != 22:
                raise ValueError("Fixture packages or settings differ")
            if kind == "regular" and lane["observer_sha256"] not in baseline["corrected_observers"]:
                raise ValueError("Different native collider observer from baseline")
            common.add((lane["archive_sha256"], lane["host_sha256"], lane["settings_sha256"]))
        checks = group["checks"]
        statuses = Counter(check["status"] for check in checks)
        if statuses != ({"PASS": 5, "BLOCKED": 1} if kind == "regular" else {"PASS": 3}):
            raise ValueError(f"Incomplete native controls in {kind}")
        for check in checks:
            if check["status"] == "BLOCKED" and "manage_terrain" not in check["criterion"]:
                raise ValueError("Unexpected blocked control")
    tests = native["native_tests"]
    if (
        len(common) != 1
        or tests["terrain_enabled"] is not False
        or len({t["name"] for t in tests["tests"] if t["result"] == "Passed"}) != 9
        or len(tests["tests"]) != 9
    ):
        raise ValueError("Native fixtures or isolated test evidence differ")


def build(root, registry_root, native_run, *, final):
    baseline = load_baseline()
    roots = {"regular": root, "registry": registry_root}
    rows = []
    catalogs = {}
    actor = baseline["native_evidence"]["actor"]
    for directory in roots.values():
        if json.loads((directory / "actor-version.json").read_bytes()) != actor:
            raise ValueError("Actor binary, model or effort differs from baseline")
        for path in (directory / "receipts-reviewed").glob("lane-*-inventories.json"):
            for profile, inventory in json.loads(path.read_bytes()).items():
                data = {k: len(v) for k, v in inventory.items()} | {
                    "sha256": report.digest(inventory)
                }
                if catalogs.setdefault(profile, data) != data:
                    raise ValueError(f"Inconsistent {profile} inventory")
    for pair in trial.pairs():
        for spec in pair:
            registered = spec["task"] == "installed_package"
            directory = registry_root if registered else root
            case_file = directory / "receipts-reviewed" / spec["id"] / "row.json"
            if not case_file.is_file() and not final:
                continue
            row = json.loads(case_file.read_bytes())
            if (
                any(row[key] != value for key, value in spec.items())
                or row["model"] != trial.MODEL
                or row["effort"] != trial.EFFORT
            ):
                raise ValueError(f"Delivered case differs from the sealed schedule: {spec['id']}")
            observer = "registered_package" if registered else "regular"
            report.read_ledger(root, row, observer)
            extras = (
                ("gateObserverVersion", "boxSharedMaterialId")
                if row["task"] == "gate_sensor_trigger"
                else ()
            )
            rows.append(
                report.public_case(row, extra_facts=extras)
                | {"seed": row["seed"], "fixture": observer}
            )
    result = {
        "version": 1,
        "product_tree": CANDIDATE,
        "baseline_sha256": BASELINE_SHA,
        "new_originals_sha256": trial.NEW_SHA,
        "actor": actor,
        "catalogs": catalogs,
        "summary": report.counts(rows),
        "comparison": compare(baseline["cases"], rows),
        "cases": rows,
    }
    if final:
        result["native_evidence"] = native_proof(roots, native_run)
        verify(result, baseline)
    return result


def verify(result, baseline):
    report.verify_cohort(result)
    if (
        result["product_tree"] != CANDIDATE
        or result["baseline_sha256"] != BASELINE_SHA
        or result["actor"] != baseline["native_evidence"]["actor"]
        or result["comparison"] != compare(baseline["cases"], result["cases"])
    ):
        raise ValueError("Baseline or candidate comparisons differ from the actual cases")
    schedule = {v["id"]: v for pair in trial.pairs() for v in pair}
    for row in result["cases"]:
        if row["seed"] != schedule[row["id"]]["seed"]:
            raise ValueError("Different native seed")
        registered = row["task"] == "installed_package"
        if row["fixture"] != ("registered_package" if registered else "regular"):
            raise ValueError("Wrong package fixture")
        state = row["target_before"]
        if registered:
            package = state["targetPackage"]
            entry = baseline["native_evidence"]["registry"]["packages"].get(package["name"])
            if package.get("source") != "Registry" or not entry or state["registryRecord"] != entry:
                raise ValueError("Different installed registry record from baseline")
        if row["task"] == "gate_sensor_trigger":
            if state.get("gateObserverVersion") != 2 or state.get("boxSharedMaterialId") != 0:
                raise ValueError("Wrong starting collider or observer")
    native = result["native_evidence"]
    check_native(native, baseline["native_evidence"])
    check_inventories(native["catalog_members"], result["catalogs"], baseline["catalogs"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("progress", "publish", "verify"))
    parser.add_argument("--root", type=Path)
    parser.add_argument("--registry-root", type=Path)
    parser.add_argument("--native-run", type=Path)
    parser.add_argument("--result", type=Path, default=OUT)
    args = parser.parse_args()
    if args.action == "verify":
        data = json.loads(args.result.read_bytes())
        verify(data, load_baseline())
    else:
        if (
            not args.root
            or not args.registry_root
            or (args.action == "publish" and not args.native_run)
        ):
            parser.error(
                "Supply both private actor roots and the isolated native run for publication"
            )
        data = build(args.root, args.registry_root, args.native_run, final=args.action == "publish")
        if args.action == "publish":
            trial.write(args.result, data)
    print(json.dumps(data["summary"], indent=2))


if __name__ == "__main__":
    main()
