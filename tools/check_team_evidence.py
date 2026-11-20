"""Exercise a real installed team handoff using declared synthetic verification inputs."""

import argparse
import json
import os
import shutil
import socket
import subprocess
import tempfile
from collections import Counter
from pathlib import Path


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
    pins = {}
    for name, options in controls.items():
        result = invoke(command, ["team", *options, "--bundle", transfer / f"{name}.json"], 0)
        pins[name] = result["bundle_sha256"]
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
            for name, code in (("change", 1), ("partial", 2), ("selection", 0)):
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
    return {
        "schema": "installed_team_handoff@1",
        "inputs": "synthetic verification test controls",
        "network_interfaces": interfaces,
        "states": {k: v["state"] for k, v in results.items()},
        "before": {key: val for key, val in expected.items() if key != "pins"},
        "after_classifications": classifications(changed["assessment"]),
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
