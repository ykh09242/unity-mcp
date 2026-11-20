"""Recognize the maintained prefab control in a successful dual-host comparison."""

import json
from pathlib import Path
import re
import sys


def confirmed(report):
    semantic = report["semantics"]
    if semantic["scenario"] != "verified-shop":
        return False
    if report["status"] != "consistent":
        raise ValueError("prefab_pair_requires_consistent_native_replays")
    spec = json.loads(Path(__file__).with_name("prefab-pair-expectations.json").read_bytes())
    reference = spec["serialized_references"][0]
    identities = []
    for side in ("baseline", "candidate"):
        expected = spec[side]
        observed = semantic["runs"][side]
        admission = semantic["admissions"][side]
        shared = [
            row
            for row in admission["serialized_references"]
            if all(row[k] == v for k, v in reference.items())
        ]
        state = expected["state"]
        if (
            not re.fullmatch("[a-f0-9]{64}", semantic["builds"][side].get("capture_sha256") or "")
            or len(shared) != 1
            or not re.fullmatch("[a-f0-9]{32}", shared[0]["guid"])
            or admission["assets"].get(reference["target"]) != shared[0]["guid"]
            or not int(shared[0]["file_id"])
            or any(observed[k] != expected[k] for k in ("status", "checkpoint", "action"))
            or observed["log_marker"] != state["log_marker"]
            or any(
                json.dumps(observed["state"]["observed"][k]) != json.dumps(v)
                for k, v in state["expected"].items()
            )
        ):
            raise ValueError("prefab_pair_lacks_maintained_control_observation: " + side)
        captured = admission["source_observations"]
        editor = {
            "key": state["expected"]["key"],
            "asset_guid": shared[0]["guid"],
            "serialized_guid": shared[0]["guid"],
            "dependency_retained": True,
            "loader_guid": "" if side == "baseline" else shared[0]["guid"],
        }
        if any(
            json.dumps(captured[key]) != json.dumps(value)
            for key, value in editor.items()
            if key in captured
        ):
            raise ValueError("prefab_pair_conflicting_editor_observation: " + side)
        identities.append((shared[0]["guid"], shared[0]["file_id"]))
    if identities[0] != identities[1]:
        raise ValueError("prefab_pair_serialized_identity_changed")
    return True


if __name__ == "__main__":
    report = json.loads(Path(sys.argv[1]).read_bytes())
    print("confirmed=" + str(confirmed(report)).lower())
