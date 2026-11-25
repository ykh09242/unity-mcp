"""Run the isolated native edit controls or check an archived native control receipt."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time
import uuid

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.experiments.live_editor_fixture.lane import (
    REVISION,
    ROOT,
    WORK,
    compile_dll,
    dump,
    launch,
    request,
    shared,
)


CASES = (
    ("box-set", "gate_sensor_trigger", "ka"),
    ("box-save", "gate_sensor_trigger", "ka"),
    ("box-save-only", "gate_sensor_trigger", "ka"),
    ("box-getter", "gate_sensor_trigger", "ka"),
    ("scene", "activate_named_scene", "ja"),
    ("audio", "restore_clip_inheritance", "lv"),
    ("input", "author_two_control_maps", "sk"),
    ("terrain", "fill_surface_gap", "ca"),
)


def differing(left, right):
    return sorted(k for k in left.keys() | right.keys() if left.get(k) != right.get(k))


def native_action(control, op, state):
    rid = uuid.uuid4().hex
    packet = {
        k: state[k]
        for k in ("targetScene", "audioPath", "audioPlatform", "inputPath", "targetMaps")
        if k in state
    }
    packet.update(id=rid, op=op)
    if op == "terrain":
        packet.update(x=35, y=42)
    temp = control / (rid + ".tmp")
    dump(temp, packet)
    temp.rename(control / "positive-control.request")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            response = json.loads((control / "positive-control.json").read_bytes())
        except (FileNotFoundError, PermissionError):
            response = {}
        if response.get("id") == rid:
            if not response["ok"]:
                raise RuntimeError(response.get("error", "Native control failed"))
            return response
        time.sleep(0.1)
    raise TimeoutError(f"No native control response for {op}")


def verify(row):
    before, after = row["before"], row["after"]
    op = row["op"]
    changed = set(differing(before, after))
    expected = {
        "scene": {"activeScene"},
        "audio": {"audioOverride"},
        "input": {"fileHash", "inputMaps", "importCount"},
        "terrain": {"surfaceSamples"},
    }
    if op.startswith("box"):
        v2 = before.get("gateObserverVersion") == 2
        facts = row["nativeActionFacts"]
        assert facts["sharedMaterialBefore"] == 0
        assert before["boxTrigger"] is False and after["boxTrigger"] is (op != "box-save-only")
        if op == "box-getter":
            assert facts["sharedMaterialAfter"] == facts["getterMaterial"] != 0
            if v2:
                assert after["boxSharedMaterialId"] == facts["getterMaterial"]
                if before["boxSharedMaterial"] != after["boxSharedMaterial"]:
                    changed.remove("boxSharedMaterial")
            assert changed == (
                {"boxTrigger", "sceneCollateral", "boxSharedMaterialId"}
                if v2
                else {"boxTrigger", "sceneCollateral"}
            )
        else:
            assert facts["sharedMaterialAfter"] == 0
            wanted = {"boxTrigger"} if op != "box-save-only" else set()
            if not v2 and op != "box-save-only":
                wanted.add("sceneCollateral")
            assert changed == wanted
    else:
        assert changed == expected[op]
        if op == "scene":
            assert after["activeScene"] == before["targetScene"] != before["activeScene"]
        elif op == "audio":
            assert before["audioOverride"] is True and after["audioOverride"] is False
        elif op == "input":
            assert after["importCount"] - before["importCount"] == 1
            assert set(after["inputMaps"]) - set(before["inputMaps"]) == set(before["targetMaps"])
            assert all(after["inputMaps"][k] == v for k, v in before["inputMaps"].items())
            assert all(
                not after["inputMaps"][k]["actions"] and not after["inputMaps"][k]["bindings"]
                for k in before["targetMaps"]
            )
        elif op == "terrain":
            changes = [
                (b, a)
                for b, a in zip(before["surfaceSamples"], after["surfaceSamples"], strict=True)
                if b != a
            ]
            assert changes == [
                ({"x": 35, "y": 42, "surface": False}, {"x": 35, "y": 42, "surface": True})
            ]


def run(baseline, observer):
    if (
        WORK == baseline
        or WORK.is_relative_to(baseline)
        or baseline.is_relative_to(WORK)
        or any((WORK / f"lane-{i}").exists() for i in range(4))
    ):
        raise ValueError(
            "Calibration requires a fresh dedicated LIVE_AGENT_TRIAL_DIR separate from all trial lanes"
        )
    shared()
    compile_dll("PositiveControl")
    if observer is None:
        compile_dll("World")
        observer = WORK / "bin/World.dll"
    else:
        observer = observer.resolve(strict=True)
    shutil.copy2(baseline / "bin/LaneHost.dll", WORK / "bin/LaneHost.dll")
    shutil.copy2(baseline / "dependencies/manifest.json", WORK / "dependencies/manifest.json")
    lane = WORK / "lane-0"
    project = lane / "project"
    control = lane / "control"
    plugin = project / "Assets/Editor/PositiveControl.dll"
    plugin.parent.mkdir(parents=True)
    control.mkdir()
    dump(lane / "owned-lane.json", {"lane": 0, "revision": REVISION, "project": str(project)})
    (control / "positive-control-only").touch()
    shutil.copy2(WORK / "bin/PositiveControl.dll", plugin)
    shutil.copy2(observer, control / "World.dll")
    launch(0)
    rows = []
    try:
        ping = request(lane, "ping", timeout=180)
        if not ping["ok"] or Path(ping["project_root"]) != project:
            raise ValueError("Calibration endpoint identity mismatch")
        for op, task, lang in CASES:
            b = request(lane, "prepare", task, lang, 1439627)
            if not b["ok"]:
                raise RuntimeError(b["error"])
            native = native_action(control, op, b["state"])
            a = request(lane, "snapshot", task, lang, 1439627)
            if not a["ok"]:
                raise RuntimeError(a["error"])
            row = {
                "op": op,
                "task": task,
                "language": lang,
                "before": b["state"],
                "after": a["state"],
                "nativeActionFacts": {
                    k: v for k, v in native.items() if k not in ("id", "ok", "before", "after")
                },
                "changedStateFields": differing(b["state"], a["state"]),
                "nativeComponentChanges": {
                    k: [native["before"].get(k), native["after"].get(k)]
                    for k in differing(native["before"], native["after"])
                },
            }
            rows.append(row)
            dump(
                WORK / "positive-controls.json",
                {
                    "unity": "6000.0.69f1",
                    "revision": REVISION,
                    "instance": ping["instance"],
                    "native_pid": ping["pid"],
                    "observer_sha256": hashlib.sha256(observer.read_bytes()).hexdigest(),
                    "controls": rows,
                },
            )
            verify(row)
    finally:
        request(lane, "stop", timeout=30)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=ROOT / ".work/live-agent-20261125")
    parser.add_argument(
        "--observer",
        type=Path,
        help="Use this archived World.dll; defaults to compiling the checkout",
    )
    parser.add_argument(
        "--verify", type=Path, help="Check an existing control receipt without starting an editor"
    )
    args = parser.parse_args()
    if args.verify:
        rows = json.loads(args.verify.read_bytes())["controls"]
        if [r["op"] for r in rows] not in ([s[0] for s in CASES], [s[0] for s in CASES[:4]]):
            raise ValueError("Incomplete or unrecognized native control panel")
        for row in rows:
            verify(row)
        print(f"Verified {len(rows)} native controls.")
    else:
        run(args.baseline.resolve(), args.observer)


if __name__ == "__main__":
    main()
