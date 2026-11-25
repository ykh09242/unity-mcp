"""Check every native world twice without dispatching a model or any MCP call."""

from concurrent.futures import ThreadPoolExecutor
import json
import sys

from lane import SOURCE, WORK, dump, request


def originals():
    path = SOURCE.parent
    tasks = []
    for lang in ("ko", "ja"):
        for task in (
            "current_scene",
            "scene_list",
            "installed_package",
            "package_list",
            "selected_agent",
            "agent_list",
        ):
            tasks.append((task, lang))
    for lang in ("ja", "es"):
        tasks.extend((task, lang) for task in ("activate_scene", "activate_named_scene"))
    menu = json.loads((path / "blind_tool_tasks.json").read_bytes())["menu-declaration"]
    tasks.extend(("menu-path", lang) for lang in menu if lang != "en")
    tasks.extend((x, "ko") for x in ("short_current_scene", "short_active_scene"))
    holdout = json.loads((path / "agent_family_holdout.json").read_bytes())["queries"]
    tasks.extend((task, lang) for lang, group in holdout.items() for task in group)
    extra = json.loads((path / "live_agent_originals.json").read_bytes())["tasks"]
    tasks.extend((task, lang) for task, group in extra.items() for lang in group["queries"])
    assert len(set(tasks)) == len(tasks) == 71
    return tasks


def inspect(index, entries):
    lane = WORK / f"lane-{index}"
    results = []
    for task, lang in entries:
        before = request(lane, "prepare", task, lang, 41)
        after = request(lane, "snapshot", task, lang, 41) if before["ok"] else {}
        changed = []
        if before["ok"] and after.get("ok"):
            a = before["state"]
            b = after["state"]
            changed = sorted(k for k in a.keys() | b.keys() if a.get(k) != b.get(k))
            if task == "gate_sensor_trigger":
                assert a["boxTrigger"] is False and a["boxEnabled"]
            if task == "restore_clip_inheritance":
                assert a["audioOverride"] is True
            if task == "author_two_control_maps":
                assert len(a["inputMaps"]) == 1 and a["importCount"] == 0
            if task == "fill_surface_gap":
                assert a["surfaceSamples"][4]["surface"] is False
            if task == "selected_agent":
                assert len(a["selectedAgent"]) >= 18
        row = {
            "task": task,
            "language": lang,
            "before": before,
            "after": after,
            "stable": before["ok"] and after.get("ok", False) and not changed,
            "changed": changed,
        }
        results.append(row)
        name = f"preflight-{index}.json"
        dump(WORK / name, results)
        if not row["stable"]:
            print(
                index, task, lang, before.get("error") or after.get("error") or changed, flush=True
            )
    return sum(row["stable"] for row in results), len(entries)


if __name__ == "__main__":
    lanes = [int(i) for i in sys.argv[1:]]
    if not lanes or len(set(lanes)) != len(lanes) or any(i not in range(4) for i in lanes):
        sys.exit("Pass one or more distinct idle lane numbers (0 through 3).")
    entries = originals()
    with ThreadPoolExecutor(max_workers=len(lanes)) as pool:
        jobs = [
            pool.submit(inspect, lane, entries[n :: len(lanes)]) for n, lane in enumerate(lanes)
        ]
        result = [j.result() for j in jobs]
    print(result)
    sys.exit(any(ok != total for ok, total in result))
