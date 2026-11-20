"""Compare four stable/candidate wire pairs and model the candidate continuation lifecycle."""

import argparse
import json
import shutil
import subprocess
import sys
from itertools import product
from pathlib import Path
from tempfile import TemporaryDirectory

from player_e2e_artifacts import digest, read_json, write_json
from run_stable_workflow_upgrade import archive, git_id, server_pin
from unity_ci_packages import ROOT

FIXTURE = ROOT / "tools/tests/fixtures/transport_wire"
STABLE = "ykh09242-v2.5.0"
ROLES = ("stable", "candidate")


def meaning(case: dict | None) -> dict | None:
    """Compare machine-readable outcomes; keep original diagnostic text in each pair's report."""
    if case is None or "payload" not in case:
        return case
    payload = dict(case["payload"])
    payload.pop("error", None)
    return {**case, "payload": payload}


def build_peer(work: Path, package: Path) -> Path:
    project = work / "fixture"
    project.mkdir()
    for name in ("WirePeer.csproj", "EditorBoundary.cs", "WirePeer.cs"):
        shutil.copyfile(FIXTURE / name, project / name)
    output = work / "compiled"
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools/run_dotnet.py"),
            "build",
            str(project / "WirePeer.csproj"),
            "-c",
            "Release",
            "--output",
            str(output),
            f"-p:PackageRoot={package}",
        ],
        check=True,
        timeout=180,
    )
    return (output / "WirePeer.dll").resolve(strict=True)


def compare(work: Path, output: Path, stable: str, candidate: str, replay: dict | None) -> int:
    commits = {"stable": git_id(stable, "commit"), "candidate": git_id(candidate, "commit")}
    report = {
        "schema_version": 2,
        "boundary": "Managed production codecs and resume journal, simulated transport loss and handler",
        "fixture_sha256": {
            path.name: digest(path) for path in sorted(FIXTURE.iterdir()) if path.is_file()
        },
        "revisions": {},
        "pairs": {},
        "differences_from_stable_pair": {},
    }
    servers = {}
    editors = {}
    for role, commit in commits.items():
        directory = work / role
        directory.mkdir()
        package = directory / "package-source"
        archive(commit, package, "MCPForUnity")
        pin = server_pin(package / "MCPForUnity") if role == "stable" else commit
        server = directory / "server-source"
        archive(pin, server, "Server/src")
        servers[role] = server / "Server/src"
        editors[role] = build_peer(directory, package / "MCPForUnity")
        report["revisions"][role] = {"package_commit": commit, "server_commit": pin}
    if replay is not None and report["revisions"] != replay["revisions"]:
        raise ValueError("Reproducer revisions do not match the selected source")
    failed = False
    for server_role, editor_role in product(ROLES, repeat=2):
        pair = f"{server_role}-python_{editor_role}-csharp"
        check_model = server_role == editor_role == "candidate"
        pair_output = work / f"{pair}.json"
        command = [
            sys.executable,
            "-I",
            str(FIXTURE / "wire_pair.py"),
            str(servers[server_role]),
            str(editors[editor_role]),
            str(pair_output),
            "model" if check_model else "wire",
        ]
        if replay and check_model:
            command.append(json.dumps(replay["events"]))
        print(f"Checking {pair}", flush=True)
        subprocess.run(command, check=True, timeout=300)
        result = read_json(pair_output)
        report["pairs"][pair] = result
        reference = report["pairs"]["stable-python_stable-csharp"]["cases"]
        differences = {
            name: {"stable": reference.get(name), "observed": result["cases"].get(name)}
            for name in sorted(reference.keys() | result["cases"].keys())
            if meaning(reference.get(name)) != meaning(result["cases"].get(name))
        }
        report["differences_from_stable_pair"][pair] = differences
        preservation_failures = [
            direction
            for direction, observation in result["preservation"].items()
            if observation["status"] != "passed"
        ]
        for direction in preservation_failures:
            detail = result["preservation"][direction]
            print(
                f"{pair} {direction}: {json.dumps(detail['failure'], ensure_ascii=True)}",
                flush=True,
            )
        if result["divergence"] is not None:
            write_json(
                output / "reproducer.json",
                {"pair": pair, "revisions": report["revisions"], **result["divergence"]},
            )
        failed |= bool(
            differences
            or result["unexpected_cases"]
            or result["divergence"]
            or preservation_failures
        )
    write_json(output / "comparison.json", report)
    print(output / "comparison.json", flush=True)
    return int(failed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stable", default=STABLE)
    parser.add_argument("--candidate", default="HEAD")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--replay", type=Path, help="Re-run a saved model divergence at its exact revisions"
    )
    args = parser.parse_args()
    replay = read_json(args.replay) if args.replay else None
    if replay:
        args.stable = replay["revisions"]["stable"]["package_commit"]
        args.candidate = replay["revisions"]["candidate"]["package_commit"]
    output = args.output.resolve()
    output.mkdir(exist_ok=True, parents=True)
    if any((output / name).exists() for name in ("comparison.json", "reproducer.json")):
        parser.error("Choose an output directory without existing wire evidence")
    with TemporaryDirectory(prefix="unity-mcp-wire-") as work:
        return compare(Path(work), output, args.stable, args.candidate, replay)


if __name__ == "__main__":
    raise SystemExit(main())
