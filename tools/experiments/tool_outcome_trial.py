"""Run the additional sealed outcome panel on four owned native editor lanes."""

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.experiments import live_agent_trial as trial
from tools.experiments.live_editor_fixture import lane

ORIGINALS = Path(__file__).with_name("tool_outcome_originals.json")
ORIGINAL_SHA = "143f7ba00a35d6f6a0dac7fdf86cd329ae00f560bbc2834235d9f956c53a3d88"
TARGETS = {"lower_lamp": ("intensity", 2.75), "lower_bell": ("volume", 0.375)}
FIELDS = {
    "beacon_light": {"range", "intensity"},
    "hatch_surface": {"material", "shader"},
    "bell_audio": {"volume", "pitch"},
    "mist_particles": {"rate", "maximum"},
    "hoist_animation": {"controller"},
    "float_body": {"mass", "angularDamping"},
    "lower_lamp": {"intensity", "range"},
    "lower_bell": {"volume", "pitch"},
}


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def schedule():
    originals = ORIGINALS.read_bytes().replace(b"\r\n", b"\n")
    if hashlib.sha256(originals).hexdigest() != ORIGINAL_SHA:
        raise ValueError("Sealed outcome prompts changed")
    rows = []
    for i, item in enumerate(json.loads(originals)):
        query = item["query"]
        profiles = ("core", "full") if i % 2 == 0 else ("full", "core")
        rows.append(
            [
                item
                | {
                    "id": digest(profile + "/" + query)[:16],
                    "profile": profile,
                    "seed": int(digest(query)[:7], 16),
                    "language": "en",
                }
                for profile in profiles
            ]
        )
    return rows


def pin_server():
    directory = lane.real_child(lane.WORK / "product")
    directory.mkdir(exist_ok=False)
    archive = directory / "source.zip"
    subprocess.run(
        ["git", "archive", "--format=zip", "-o", str(archive), lane.REVISION, "Server"],
        cwd=lane.ROOT,
        check=True,
    )
    with zipfile.ZipFile(archive) as source:
        if source.comment != lane.REVISION.encode("ascii"):
            raise ValueError("Unexpected server revision")
        source.extractall(directory)
    # The script directory takes precedence over the working tree's editable install.
    entry = directory / "Server/src/main.py"
    if not entry.is_file():
        raise FileNotFoundError(entry)
    wrapper = directory / "server.cmd"
    wrapper.write_text(
        f'@echo off\nset "FASTMCP_CHECK_FOR_UPDATES=off"\n"{sys.executable}" -B -X utf8 "{entry}" %*\n',
        encoding="utf-8",
    )
    return wrapper


def setup(observer, lanes):
    lane.shared()
    pin_server()
    if observer is None:
        lane.compile_dll("OutcomeWorld")
    else:
        shutil.copy2(observer, lane.real_child(lane.WORK / "bin/OutcomeWorld.dll"))
    for i in range(lanes):
        lane.launch(i)
        project_lane = lane.WORK / f"lane-{i}"
        shutil.copy2(
            lane.WORK / "bin/OutcomeWorld.dll", lane.real_child(project_lane / "control/World.dll")
        )


def preflight(index, assignment, server):
    project_lane = lane.WORK / f"lane-{index}"
    ping = lane.request(project_lane, "ping")
    if not ping["ok"] or Path(ping["project_root"]).resolve() != project_lane / "project":
        raise RuntimeError("Unexpected native identity")
    inventories = {
        p: asyncio.run(trial.preflight(server, project_lane, ping["instance"], p))
        for p in ("core", "full")
    }
    records = []
    for pair in assignment:
        row = pair[0]
        a = lane.request(project_lane, "prepare", row["task"], row["language"], row["seed"])
        if not a.get("ok"):
            raise RuntimeError(a.get("error"))
        b = lane.request(project_lane, "snapshot", row["task"], row["language"], row["seed"])
        if not b.get("ok"):
            raise RuntimeError(b.get("error"))
        if a["state"] != b["state"] or set(a["state"]["facts"]) != FIELDS[row["task"]]:
            raise ValueError(
                f"Unstable or incomplete observer for {row['task']}: {a['state']} {b['state']}"
            )
        records.append(a)
    trial.write(
        lane.WORK / f"preflight-{index}.json", {"records": records, "inventories": inventories}
    )


def verified_server():
    directory = lane.real_child(lane.WORK / "product")
    with zipfile.ZipFile(directory / "source.zip") as archive:
        if archive.comment != lane.REVISION.encode("ascii"):
            raise ValueError("Wrong pinned server revision")
        for info in archive.infolist():
            if not info.is_dir() and (directory / info.filename).read_bytes() != archive.read(info):
                raise ValueError("Pinned server changed: " + info.filename)
    return directory / "server.cmd"


def pending(assigned):
    for pair in assigned:
        for row in pair:
            if (lane.WORK / "pause-before-next-turn").is_file():
                return
            yield row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("setup", "preflight", "run", "stop"))
    parser.add_argument("--lanes", type=int, choices=range(1, 5), default=4)
    parser.add_argument(
        "--observer", type=Path, help="Replay using the archived baseline observer DLL"
    )
    args = parser.parse_args()
    if not os.environ.get("LIVE_AGENT_TRIAL_DIR") or not os.environ.get(
        "LIVE_AGENT_PRODUCT_REVISION"
    ):
        parser.error("Explicit LIVE_AGENT_TRIAL_DIR and LIVE_AGENT_PRODUCT_REVISION are required")
    rows = schedule()
    if args.action == "setup":
        setup(args.observer, args.lanes)
        return
    if args.action == "stop":
        for i in range(args.lanes):
            print(lane.request(lane.WORK / f"lane-{i}", "stop")["ok"])
        return
    lane.check_work_root()
    server = verified_server()
    if args.action == "preflight":
        trial.write(lane.WORK / "manifest.json", rows)
        with ThreadPoolExecutor(args.lanes) as pool:
            list(pool.map(lambda i: preflight(i, rows[i :: args.lanes], server), range(args.lanes)))
        return
    if json.loads((lane.WORK / "manifest.json").read_bytes()) != rows:
        raise ValueError("Missing matching frozen manifest")
    for i in range(args.lanes):
        checks = json.loads((lane.WORK / f"preflight-{i}.json").read_bytes())
        if [item["task"] for item in checks["records"]] != [
            pair[0]["task"] for pair in rows[i :: args.lanes]
        ]:
            raise ValueError("Native preflight does not match the assigned cases")
    codex = shutil.which("codex")
    if not codex:
        raise FileNotFoundError("Codex CLI")
    fingerprint = trial.actor_version(codex)
    path = lane.WORK / "actor-version.json"
    if path.exists() and json.loads(path.read_bytes()) != fingerprint:
        raise ValueError("Actor identity changed")
    trial.write(path, fingerprint)
    receipts = lane.WORK / "receipts"
    receipts.mkdir(exist_ok=True)
    with ThreadPoolExecutor(args.lanes) as pool:
        futures = [
            pool.submit(
                trial.run_lane,
                lane.WORK / f"lane-{i}",
                pending(rows[i :: args.lanes]),
                receipts,
                codex,
                server,
            )
            for i in range(args.lanes)
        ]
        for future in futures:
            future.result()


if __name__ == "__main__":
    main()
