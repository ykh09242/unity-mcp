"""Resolve literal scene-load sites against two retained EditorBuildSettings files."""

import hashlib
import json
import re
from bisect import bisect_right
from pathlib import Path, PurePosixPath
from uuid import UUID

import click
from cli.utils.play_scenario_analysis_io import read_bytes, read_json

SETTINGS = "ProjectSettings/EditorBuildSettings.asset"
LIMIT = 1024 * 1024
COMMENT_OR_STRING = re.compile(
    r"""//[^\n]*|/\*[\s\S]*?\*/|@"(?:""|[^"])*"|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'"""
)
CALL = re.compile(r"\bSceneManager\s*\.\s*(LoadScene(?:Async)?)\s*\(\s*([0-9]+)\s*(?=[,)])")


def scene_list(content):
    text = content.decode("utf-8-sig").splitlines()
    if text.count("EditorBuildSettings:") == 1 and text.count("  m_Scenes: []") == 1:
        return []
    if text.count("EditorBuildSettings:") != 1 or text.count("  m_Scenes:") != 1:
        raise ValueError("Expected a Unity text EditorBuildSettings asset with a scene list")
    lines = iter(text[text.index("  m_Scenes:") + 1 :])
    records = []
    for line in lines:
        if re.match(r"^  [\w]+:", line):
            break
        enabled = re.fullmatch(r"  - enabled: ([01])", line)
        path = re.fullmatch(r"    path: (Assets/.+\.unity)", next(lines, ""))
        guid = re.fullmatch(r"    guid: ([a-f0-9]{32})", next(lines, ""))
        if not enabled or not path or not guid:
            raise ValueError("Unsupported scene entry in EditorBuildSettings")
        if enabled[1] == "1":
            records.append({"path": path[1], "guid": guid[1]})
    return records


def capture(root, source):
    if (
        not source.startswith("Assets/")
        or "/resources/gamedata/" in source.casefold()
        or not source.endswith(".cs")
        or "\\" in source
        or ":" in source
        or ".." in PurePosixPath(source).parts
    ):
        raise ValueError("Choose a project-relative C# source under Assets")
    settings = read_bytes(root / SETTINGS, LIMIT)
    code = read_bytes(root / source, LIMIT)
    text = code.decode("utf-8-sig")
    if '"""' in text:
        raise ValueError("Raw C# strings require a compiler-backed call-site inspection")

    # Replace strings with a token so a string expression cannot disappear between code tokens.
    def hide(match):
        value = re.sub(r"[^\n]", " ", match[0])
        return value if match[0].startswith("/") else "@" + value[1:]

    masked = COMMENT_OR_STRING.sub(hide, text)
    scenes = scene_list(settings)
    starts = [0, *(m.end() for m in re.finditer("\n", text))]
    sites = []
    for call in CALL.finditer(masked):
        if len(sites) >= 2048:
            raise ValueError("Select a source with at most 2048 numeric scene calls")
        line = bisect_right(starts, call.start())
        column = call.start() - starts[line - 1] + 1
        index = int(call[2])
        sites.append(
            {
                "file": source,
                "line": line,
                "column": column,
                "method": call[1],
                "index": index,
                "scene": scenes[index] if index < len(scenes) else None,
            }
        )
    return {
        "source_sha256": hashlib.sha256(code).hexdigest(),
        "settings_sha256": hashlib.sha256(settings).hexdigest(),
        "enabled_scenes": scenes,
        "calls": sites,
    }


def observed(root, snapshot, site):
    build = read_json(root / "build.json")
    launch = read_json(root / "player-process/launch.json")
    lines = read_bytes(root / "player.log", 4 * LIMIT).decode("utf-8-sig").splitlines()
    marker = "MCP_SCENE_INDEX "
    events = [json.loads(line[len(marker) :]) for line in lines if line.startswith(marker)]
    command = launch.get("command")
    if (
        len(events) != 1
        or not isinstance(events[0], dict)
        or not isinstance(command, list)
        or command.count("--scene-trial") != 1
        or command.index("--scene-trial") + 1 >= len(command)
    ):
        raise ValueError("Expected one identified scene callback from the Player")
    event = events[0]
    build_id = UUID(str(build.get("build_guid")))
    player_build_id = UUID(str(event.get("build_guid")))
    if (
        not build_id.int
        or not re.fullmatch(r"[0-9a-f]{32}", str(event.get("trial")))
        or command[command.index("--scene-trial") + 1] != event["trial"]
        or build_id != player_build_id
        or type(event.get("pid")) is not int
        or event["pid"] != launch.get("process_id")
        or build.get("settings_sha256") != snapshot["settings_sha256"]
        or build.get("source_sha256") != snapshot["source_sha256"]
        or build.get("source") != site["file"]
        or build.get("scenes") != [s["path"] for s in snapshot["enabled_scenes"]]
        or site["scene"] is None
        or event.get("scene") != site["scene"]["path"]
        or type(event.get("index")) is not int
        or event["index"] != site["index"]
        or launch.get("process_ended") is not True
        or launch.get("forced_termination") is not False
        or type(launch.get("actual_exit_code")) is not int
        or launch["actual_exit_code"] != 0
    ):
        raise ValueError("Player callback does not match its build, source, process and scene")
    event["build_guid"] = build_id.hex
    event["scene_guid_from_settings"] = site["scene"]["guid"]
    return event


def compare_scene_indices(roots, source, player_line=None):
    snapshots = [capture(root, source) for root in roots]
    old, new = snapshots[:2]
    if old["source_sha256"] != new["source_sha256"]:
        raise ValueError("Capture the same numeric loader source before and after reordering")
    affected = [
        {"before": a, "after": b}
        for a, b in zip(old["calls"], new["calls"], strict=True)
        if a["scene"] != b["scene"]
    ]
    result = {
        "coverage": "literal_decimal_SceneManager_load_calls_in_selected_source",
        "scene_reorder_only_in_enabled_list": (
            old["enabled_scenes"] != new["enabled_scenes"]
            and sorted((s["path"], s["guid"]) for s in old["enabled_scenes"])
            == sorted((s["path"], s["guid"]) for s in new["enabled_scenes"])
        ),
        "affected_calls": affected,
        "snapshots": dict(
            zip(("before", "after", "repaired")[: len(roots)], snapshots, strict=True)
        ),
        "player_observation": {"state": "unavailable"},
    }
    if player_line is not None:
        events = []
        for root, snapshot in zip(roots, snapshots, strict=True):
            sites = [c for c in snapshot["calls"] if c["line"] == player_line]
            if len(sites) != 1:
                raise ValueError("Select one numeric call on the same line of each snapshot")
            events.append(observed(root, snapshot, sites[0]))
        for key in ("trial", "build_guid"):
            if len({e[key] for e in events}) != len(events):
                raise ValueError("Reused native scene observation")
        identities = [(e["scene"], e["scene_guid_from_settings"]) for e in events]
        result["player_observation"] = {
            "state": "observed",
            "events": events,
            "original_scene_restored": (
                identities[0] != identities[1] and identities[0] == identities[2]
                if len(events) == 3
                else None
            ),
        }
    return result


@click.command("scene-index-diff")
@click.argument("baseline", type=click.Path(path_type=Path))
@click.argument("candidate", type=click.Path(path_type=Path))
@click.option("--source", required=True, help="Project-relative C# source in both snapshots.")
@click.option("--repaired", type=click.Path(path_type=Path))
@click.option(
    "--player-line",
    type=click.IntRange(min=1),
    help="Match retained Player observations to this call.",
)
def scene_index_diff(baseline, candidate, source, repaired, player_line):
    """Resolve numeric scene loads in project snapshots and optional Standalone runs."""
    try:
        roots = [baseline, candidate] + ([repaired] if repaired else [])
        click.echo(json.dumps(compare_scene_indices(roots, source, player_line), indent=2))
    except (ValueError, OSError) as error:
        raise click.ClickException(str(error)) from None
