"""Exercise a real installed team handoff using declared synthetic verification inputs."""

import argparse
import json
import os
import shutil
import socket
import subprocess
import tempfile
from collections import Counter
from hashlib import sha256
from pathlib import Path
from uuid import UUID
from xml.etree import ElementTree


def invoke(program, args, code):
    env = {
        k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"}
    }
    env["UNITY_MCP_DISABLE_TELEMETRY"] = "1"
    run = subprocess.run(
        [*program, *map(str, args)],
        env=env,
        capture_output=True,
        encoding="utf-8",
        check=False,
        timeout=45,
    )
    if run.returncode != code:
        raise ValueError(
            f"{args[0]} returned {run.returncode}, expected {code}: {run.stdout} {run.stderr}"
        )
    return json.loads(run.stdout)


def classifications(verdict):
    return dict(Counter(row["classification"] for row in verdict["checks"]))


def workflow_controls(folder):
    """Build declared source observations for both new installed handoff paths."""
    scene, prefab, asset = (
        f"Assets/PrivateControl/{name}" for name in ("Entry.unity", "Part.prefab", "Surface.mat")
    )
    scene_guid, prefab_guid, asset_guid = (c * 32 for c in ("1", "2", "3"))
    test = "PrivateControl.SceneUsesSurface"
    for name, commit, query, material_hash in (("before", "a", "4", "a"), ("after", "b", "5", "b")):
        assets = [
            (scene, scene_guid, "1" * 64),
            (prefab, prefab_guid, "2" * 64),
            (asset, asset_guid, material_hash * 64),
        ]
        hashes = {
            key: value
            for path, _, blob in assets
            for key, value in ((path, blob), (path + ".meta", "9" * 64))
        }
        revision = sha256(
            "".join(f"{path}\n{value}\n" for path, value in sorted(hashes.items())).encode()
        ).hexdigest()
        header = {
            "schema_version": 1,
            "kind": "verification_dependency_scan",
            "state": "complete",
            "next_cursor": None,
            "commit": commit * 40,
            "root": scene,
            "root_node": "asset:" + scene_guid,
            "graph_scope": "e" * 64,
            "graph_revision": int(query),
            "query_id": query * 32,
            "pages": 1,
            "scanned_edges": 2,
            "reachable_assets": len(assets),
            "closure_source_revision": revision,
            "issues": 0,
            "graph_commit_binding": "verified",
            "coverage": "partial_static",
            "native_asset_catalog_unfiltered": True,
        }
        asset_rows = [
            {
                "kind": "asset",
                "path": path,
                "node": "asset:" + node,
                "source_hash": blob,
                "meta_hash": "9" * 64,
            }
            for path, node, blob in assets
        ]
        edge_rows = [
            {
                "kind": "dependency",
                "id": f"edge-{index}",
                "owner": "asset:" + owner,
                "target": "asset:" + target,
            }
            for index, (owner, target) in enumerate(
                ((scene_guid, prefab_guid), (prefab_guid, asset_guid))
            )
        ]
        (folder / f"{name}.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in [header, *asset_rows, *edge_rows]),
            encoding="utf-8",
        )
    mapping_chunk = json.dumps({"inputs": hashes}, separators=(",", ":")).encode()
    (folder / "mapping-input.json").write_bytes(mapping_chunk)
    (folder / "mappings.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "project": str(folder / "PrivateControlProject"),
                "scenario": "scene-asset-verification",
                "build_id": "f" * 32,
                "payload_hash": "5" * 64,
                "source_revision": revision,
                "assets": {scene: scene_guid, asset: asset_guid},
                "mappings": [{"path": scene, "tests": [test]}],
                "input_chunks": [
                    {
                        "sha256": sha256(mapping_chunk).hexdigest(),
                        "count": len(hashes),
                        "size_bytes": len(mapping_chunk),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    run = ElementTree.Element("test-run", {"duration": "1", "total": "1", "result": "Passed"})
    case = ElementTree.SubElement(
        run, "test-case", {"fullname": test, "result": "Passed", "duration": "1"}
    )
    ElementTree.SubElement(case, "output").text = (
        f"MCP_VERIFICATION_EXECUTION {UUID(int=84, version=4).hex}\n"
        "MCP_VERIFICATION_CONTEXT Editor private fixture\n"
        f"MCP_VERIFICATION_EXPECTED_CASE {test}\n"
        f"{scene} {scene_guid} {'1' * 64}\n"
        f"{asset} {asset_guid} {'b' * 64}\n"
    )
    ElementTree.ElementTree(run).write(folder / "impact.xml", encoding="utf-8")
    return {
        "impact": {
            "asset-guid": asset_guid,
            "baseline": folder / "before.jsonl",
            "candidate": folder / "after.jsonl",
            "mappings": folder / "mappings.json",
            "mapping-chunk": folder / "mapping-input.json",
            "selected": folder / "impact.xml",
        },
        "recovery": _receipt_controls(folder, prefab_guid),
    }


def _receipt_controls(folder, prefab_guid):
    # Checkpoint identities are synthetic. The separate Unity recovery profile
    # exercises their generation and linkage on actual prefabs and restarts.
    first, second, observation = (key * 64 for key in ("a", "b", "c"))
    old_session, new_session = (key * 32 for key in ("5", "6"))
    prefab_hash = f"{'a' * 32}:{'A' * 43}=:{'B' * 43}="
    paths = {}
    for index, (role, action, phase) in enumerate(
        (
            ("reconcile", "reconcile_prefab", "created"),
            ("replan", "replan_prefab", "created"),
            ("resumed", "register_prefab", "registered"),
        ),
        start=1,
    ):
        original = role == "reconcile"
        body = {
            "success": True,
            "data": {
                "editor_session_id": old_session if original else new_session,
                "prefab_guid": prefab_guid,
                "prefab_hash": prefab_hash,
                "group_guid": "8" * 32,
                "handoff": {
                    "version": 1,
                    "action": action,
                    "phase": phase,
                    "workflow_id": first if original else second,
                    "previous_workflow_id": None if original else first,
                    "observation_id": observation if role != "resumed" else None,
                    "approved_observation_id": None if original else observation,
                },
                "affected_paths": ["Assets/PrivateControl/Restored.prefab"],
            },
            "execution_receipt": {
                "version": 1,
                "status": "completed",
                "project_hash": "f" * 16,
                "store_id": "9" * 32,
                "receipt_id": str(index) * 32,
                "attempts": [{"dispatched": True, "observed": True}],
            },
        }
        if role != "resumed":
            body["data"]["reconciliation"] = {
                "recorded_phase": "created",
                "recorded_editor_session_id": old_session,
                "observing_editor_session_id": new_session,
                "expected_prefab_guid": prefab_guid,
                "expected_prefab_hash": prefab_hash,
                "observed_prefab_guid": prefab_guid,
                "observed_prefab_hash": prefab_hash,
                "continuation_claimed": not original,
                "replan_available": original,
                "observation": "private-receipt-value",
            }
        path = folder / f"{role}.json"
        path.write_text(json.dumps(body), encoding="utf-8")
        paths[role] = path
    return paths


def produce(executable, fixtures, transfer):
    transfer.mkdir(parents=True, exist_ok=False)
    command = [str(executable), "--no-project-context", "--format", "json", "verification-evidence"]
    arguments = [
        "--baseline",
        fixtures / "verification-1.json",
        fixtures / "native-1.json",
        "--candidate",
        fixtures / "verification-2.json",
        fixtures / "native-2.json",
    ]
    original = invoke(command, ["compare", *arguments], 1)
    raw_selection = ["--selected", fixtures / "selected.xml", "--full", fixtures / "full.xml"]
    selection = invoke(command, ["selection", *raw_selection], 0)
    controls = {
        "change": ["pack-change", *arguments],
        "partial": ["pack-change", *arguments, "--withhold", "candidate"],
        "selection": ["pack-selection", *raw_selection],
    }
    workflows = workflow_controls(fixtures)
    for analysis, missing in (("impact", "baseline"), ("recovery", "resumed")):
        options = [f"pack-{analysis}"]
        for key, value in workflows[analysis].items():
            options.extend((f"--{key}", value))
        controls[analysis] = options
        controls[f"{analysis}-partial"] = [*options, "--withhold", missing]
    pins = {}
    for name, options in controls.items():
        path = transfer / f"{name}.json"
        result = invoke(command, ["team", *options, "--bundle", path], 0)
        pins[name] = result["bundle_sha256"]
        if name.startswith(("impact", "recovery")) and any(
            private in path.read_bytes()
            for private in (b"PrivateControl", b"private-receipt-value", b"Editor private fixture")
        ):
            raise ValueError("Team bundle published a private source value")
    return {
        "pins": pins,
        "change_state": original["state"],
        "classifications": classifications(original),
        "selection_state": selection["state"],
        "missed": len(selection["selection"]["missed_failures"]),
    }


def independent_replay(executable, transfer, expected):
    interfaces = sorted(name for _, name in socket.if_nameindex())
    if interfaces != ["lo"]:
        raise RuntimeError("Run replay in a new network namespace containing only loopback")
    command = [str(executable), "--format", "json", "team", "replay"]
    with tempfile.TemporaryDirectory(prefix="teammate-") as fresh:
        target = Path(fresh)
        previous = Path.cwd()
        try:
            os.chdir(target)
            results = {}
            for name, code in (
                ("change", 1),
                ("partial", 2),
                ("selection", 0),
                ("impact", 0),
                ("impact-partial", 2),
                ("recovery", 0),
                ("recovery-partial", 2),
            ):
                copied = target / f"relocated-{name}.json"
                shutil.copyfile(transfer / f"{name}.json", copied)
                result = invoke(command, [copied, "--sha256", expected["pins"][name]], code)
                results[name] = result
            tampered = target / "relocated-change.json"
            with tampered.open("ab") as stream:
                stream.write(b" ")
            rejected = invoke(command, [tampered, "--sha256", expected["pins"]["change"]], 2)
        finally:
            os.chdir(previous)
    changed, partial, selected = (results[key] for key in ("change", "partial", "selection"))
    if (
        changed["state"] != expected["change_state"]
        or classifications(changed["assessment"]) != expected["classifications"]
        or selected["state"] != expected["selection_state"]
        or len(selected["assessment"]["selection"]["missed_failures"]) != expected["missed"]
        or partial["unavailable_roles"] != ["candidate"]
        or partial["assessment"] is not None
        or rejected.get("state") != "incomplete"
    ):
        raise ValueError("Offline replay differs from the original verdict or failure controls")
    impact = results["impact"]["assessment"]
    partial_impact = results["impact-partial"]["assessment"]
    recovery = results["recovery"]["assessment"]
    partial_recovery = results["recovery-partial"]["assessment"]
    if (
        impact["asset_changed"] is not True
        or len(impact["scene_dependency"]) != 3
        or impact["scene_dependency"][-1] != impact["asset"]
        or list(impact["selected_tests"].values()) != ["Passed"]
        or set(partial_impact["unavailable_conclusions"]) != {"asset_change"}
        or partial_impact["asset_changed"] is not None
        or len(partial_impact["scene_dependency"]) != 3
        or recovery["last_confirmed_write"] != "create_prefab"
        or recovery["resumed_step"] != "register_prefab"
        or recovery["resumed_write_confirmed"] is not True
        or partial_recovery["last_confirmed_write"] != "create_prefab"
        or partial_recovery["resumed_step"] is not None
        or set(partial_recovery["unavailable_conclusions"])
        != {"resumed_step", "resumed_write_confirmed"}
    ):
        raise ValueError("Offline impact or authored recovery did not match the declared evidence")
    return {
        "schema": "installed_team_handoff@1",
        "inputs": "synthetic verification test controls",
        "network_interfaces": interfaces,
        "states": {k: v["state"] for k, v in results.items()},
        "before": {key: val for key, val in expected.items() if key != "pins"},
        "after_classifications": classifications(changed["assessment"]),
        "impact": impact,
        "impact_partial": partial_impact,
        "recovery": recovery,
        "recovery_partial": partial_recovery,
        "raw_sources_transferred": 0,
        "tamper_rejected": True,
        "bundle_pins": expected["pins"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    producer = sub.add_parser("produce")
    producer.add_argument("executable", type=Path)
    producer.add_argument("fixtures", type=Path)
    producer.add_argument("transfer", type=Path)
    receiver = sub.add_parser("replay")
    receiver.add_argument("executable", type=Path)
    receiver.add_argument("transfer", type=Path)
    receiver.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.action == "produce":
        result = produce(
            args.executable.resolve(), args.fixtures.resolve(), args.transfer.resolve()
        )
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as out:
            print("expectations=" + json.dumps(result, separators=(",", ":")), file=out)
    else:
        result = independent_replay(
            args.executable.resolve(),
            args.transfer.resolve(),
            json.loads(os.environ["TEAM_EXPECTATIONS"]),
        )
        with args.output.open("x", encoding="utf-8") as out:
            json.dump(result, out, indent=2)
            out.write("\n")


if __name__ == "__main__":
    main()
