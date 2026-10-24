# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# How to run: python tools/native_assurance.py path/to/receipt.json
"""Validate complete Windows native lease evidence without accepting compile substitutes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

EXPECTATIONS = (
    ("graceful-a-acquire", 0, 1, 1),
    ("graceful-b-acquire", 0, 1, 2),
    ("graceful-a-release", 0, 1, 1),
    ("graceful-b-release", 4, 0, 0),
    ("forced-a-acquire", 0, 1, 1),
    ("forced-b-acquire", 0, 1, 2),
    ("forced-b-recover", 0, 1, 1),
    ("forced-b-release", 4, 0, 0),
    ("restart-b-acquire", 0, 1, 1),
    ("restart-a-recover", 4, 0, 0),
    ("restart-a-acquire", 0, 1, 1),
    ("restart-a-release", 4, 0, 0),
    ("manual-a-acquire", 0, 1, 1),
    ("manual-b-acquire", 0, 1, 2),
    ("manual-a-change", 12, 2, 2),
    ("manual-b-maintain", 12, 2, 2),
    ("manual-a-release", 12, 2, 1),
    ("manual-b-release", 12, 2, 0),
    ("identity-a-reset", 4, 0, 0),
    ("identity-a-acquire", 0, 1, 1),
    ("identity-a-recover", 4, 0, 0),
    ("admission-a-acquire", 0, 1, 1),
    ("admission-a-release", 4, 0, 0),
)
# Indices refer to the five actual launches: a0, b0, a1, a2, b1.
ACTORS = (0, 1, 0, 1, 2, 1, 1, 1, 1, 3, 3, 3, 3, 4, 3, 4, 3, 4, 3, 3, 3, 3, 3)
OWNERS = (
    (0,),
    (0, 1),
    (1,),
    (),
    (2,),
    (1, 2),
    (1,),
    (),
    (1,),
    (),
    (3,),
    (),
    (3,),
    (3, 4),
    (3, 4),
    (3, 4),
    (4,),
    (),
    (),
    (3,),
    (),
    (3,),
    (),
)


def check_receipt(path: Path) -> dict[str, str | int]:
    """Reject missing, skipped, stale-identity or incomplete native observations."""
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if (
        data.get("schema") != "unity-mcp-native-preference-lease-v1"
        or data.get("native") is not True
        or data.get("platform") != "win32"
        or data.get("inputs_verified_before_after") is not True
        or not re.fullmatch(r"[0-9a-f]{64}", data.get("editor_sha256", ""))
        or not re.fullmatch(r"\d+\.\d+\.\d+[abfp]\d+", data.get("editor_version", ""))
    ):
        raise ValueError("Native platform, Editor binary identity and verified inputs are required")
    launches = data.get("launches", [])
    identities = {(row["pid"], row["started_ticks"]) for row in launches}
    if (
        len(identities) != 5
        or [row.get("peer") for row in launches] != ["a", "b", "a", "a", "b"]
        or any(
            type(pid) is not int or pid <= 0 or type(start) is not int or start <= 0
            for pid, start in identities
        )
    ):
        raise ValueError("Five actual Editor launch identities are required across two projects")
    ordered = [(row["pid"], row["started_ticks"]) for row in launches]
    steps = data.get("steps", [])
    if [step.get("case") for step in steps] != [row[0] for row in EXPECTATIONS]:
        raise ValueError("Required native observation inventory differs")
    for step, (name, idle, mode, count), actor, owners in zip(steps, EXPECTATIONS, ACTORS, OWNERS):
        if (
            (step.get("pid"), step.get("started_ticks")) != ordered[actor]
            or step.get("error") is not None
            or step.get("preferences") != {"ApplicationIdleTime": idle, "InteractionMode": mode}
            or len(step.get("owners", [])) != count
            or len({(row["Pid"], row["Started"]) for row in step["owners"]}) != count
            or any((row["Pid"], row["Started"]) not in identities for row in step["owners"])
        ):
            raise ValueError("Native observation failed: " + name)
        if {(row["Pid"], row["Started"]) for row in step["owners"]} != {
            ordered[index] for index in owners
        }:
            raise ValueError("Native owner identity failed: " + name)
    terminations = data.get("terminations", [])
    if (
        len(terminations) != len(launches)
        or {(row.get("pid"), row.get("started_ticks")) for row in terminations} != identities
        or {row.get("mode") for row in terminations} != {"graceful", "forced"}
        or any(
            row.get("identity_verified") is not True
            or row.get("exited") is not True
            or (row.get("pid"), row.get("started_ticks")) not in identities
            for row in terminations
        )
    ):
        raise ValueError("Owned termination requires verified PID/start identity and observed exit")
    if data.get("cleanup") != {"owned_survivors": 0, "protected_preserved": True}:
        raise ValueError("Owned cleanup or protected process preservation failed")
    return {"platform": "win32", "steps": len(steps), "editor_processes": len(identities)}


def check_client_receipt(path: Path) -> dict[str, str | int]:
    """Validate guarded client replay without claiming any lease observations."""
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if (
        data.get("schema") != "unity-mcp-native-client-window-v1"
        or data.get("mode") != "client-only"
        or data.get("lease_observations_requested") is not False
        or data.get("steps") != []
        or data.get("native") is not True
        or data.get("platform") != "win32"
        or data.get("client_phase_succeeded") is not True
        or data.get("failure") is not None
        or data.get("inputs_verified_before_after") is not True
        or not re.fullmatch(r"[0-9a-f]{64}", data.get("editor_sha256", ""))
        or not re.fullmatch(r"\d+\.\d+\.\d+[abfp]\d+", data.get("editor_version", ""))
    ):
        raise ValueError(
            "A complete native client-only replay with explicit zero lease scope is required"
        )
    launches = data.get("launches", [])
    identities = {(row["pid"], row["started_ticks"]) for row in launches}
    if (
        len(identities) != 2
        or len(launches) != 2
        or [row.get("peer") for row in launches] != ["a", "b"]
        or any(
            type(pid) is not int or pid <= 0 or type(start) is not int or start <= 0
            for pid, start in identities
        )
    ):
        raise ValueError("Client replay requires two actual Editor identities")
    terminations = data.get("terminations", [])
    if (
        len(terminations) != 2
        or {(row.get("pid"), row.get("started_ticks")) for row in terminations} != identities
        or any(
            row.get("identity_verified") is not True
            or row.get("exited") is not True
            or row.get("mode") != "graceful"
            for row in terminations
        )
        or data.get("cleanup") != {"owned_survivors": 0, "protected_preserved": True}
    ):
        raise ValueError(
            "Client replay requires both verified exits and complete protected cleanup"
        )
    return {"platform": "win32", "steps": 0, "editor_processes": 2}


def main() -> int:
    """Check one saved native receipt; malformed or incomplete evidence fails closed."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipt", type=Path)
    parser.add_argument(
        "--client-only", action="store_true", help="Validate the distinct client replay scope"
    )
    args = parser.parse_args()
    checker = check_client_receipt if args.client_only else check_receipt
    print(json.dumps(checker(args.receipt), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
