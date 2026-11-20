import json

import pytest
from cli.commands.verification_scene_index import (
    SETTINGS,
    capture,
    compare_scene_indices,
    scene_index_diff,
)
from click.testing import CliRunner

SOURCE = "Assets/Loader.cs"


def snapshot(root, order, code):
    path = root / SETTINGS
    path.parent.mkdir(parents=True)
    lines = ["EditorBuildSettings:", "  m_Scenes:"]
    for name, enabled in order:
        lines.extend(
            [
                f"  - enabled: {enabled}",
                f"    path: Assets/{name}.unity",
                f"    guid: {ord(name):032x}",
            ]
        )
    path.write_text("\n".join(lines) + "\n")
    source = root / SOURCE
    source.parent.mkdir()
    source.write_text(code)


def test_numeric_calls_resolve_against_enabled_scenes(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    code = '// SceneManager.LoadScene(0)\nvar s = "SceneManager.LoadScene(1)";\nSceneManager.LoadSceneAsync(1);\n'
    snapshot(old, [("X", 0), ("A", 1), ("B", 1)], code)
    snapshot(new, [("B", 1), ("X", 0), ("A", 1)], code)
    raw = CliRunner().invoke(scene_index_diff, [str(old), str(new), "--source", SOURCE])
    assert raw.exit_code == 0, raw.output
    result = json.loads(raw.output)
    assert result["scene_reorder_only_in_enabled_list"]
    [call] = result["affected_calls"]
    assert call["before"]["line"] == 3
    assert call["before"]["scene"]["path"] == "Assets/B.unity"
    assert call["after"]["scene"]["path"] == "Assets/A.unity"
    assert result["player_observation"] == {"state": "unavailable"}
    raw = CliRunner().invoke(
        scene_index_diff, [str(old), str(new), "--source", SOURCE, "--player-line", "3"]
    )
    assert raw.exit_code != 0


def test_capture_rejects_escaping_sources_and_marks_out_of_range(tmp_path):
    snapshot(tmp_path, [("A", 1)], "SceneManager.LoadScene(100);")
    assert capture(tmp_path, SOURCE)["calls"][0]["scene"] is None
    with pytest.raises(ValueError):
        capture(tmp_path, "Assets/../../Escape.cs")


@pytest.mark.parametrize("replaced_scene", [False, True], ids=["same-asset", "same-path-new-guid"])
def test_player_callback_is_bound_to_each_build_process_and_destination(tmp_path, replaced_scene):
    roots = []
    for phase, names, index in (("before", "AB", 1), ("after", "BA", 1), ("fixed", "BA", 0)):
        root = tmp_path / phase
        roots.append(root)
        snapshot(root, [(name, 1) for name in names], f"SceneManager.LoadScene({index});")
        if phase == "fixed" and replaced_scene:
            settings = root / SETTINGS
            settings.write_text(
                settings.read_text().replace(f"guid: {ord('B'):032x}", "guid: " + "c" * 32)
            )
        data = capture(root, SOURCE)
        job = f"{len(roots):032x}"
        path = data["calls"][0]["scene"]["path"]
        build = {
            "build_guid": job,
            "source": SOURCE,
            "settings_sha256": data["settings_sha256"],
            "source_sha256": data["source_sha256"],
            "scenes": [s["path"] for s in data["enabled_scenes"]],
        }
        event = {"trial": job, "build_guid": job, "pid": 100, "scene": path, "index": index}
        launch = {
            "process_id": 100,
            "process_ended": True,
            "forced_termination": False,
            "actual_exit_code": 0,
            "command": ["Player", "--scene-trial", job],
        }
        (root / "build.json").write_text(json.dumps(build))
        (root / "player-process").mkdir()
        (root / "player-process/launch.json").write_text(json.dumps(launch))
        (root / "player.log").write_text("MCP_SCENE_INDEX " + json.dumps(event))
    observed = compare_scene_indices(roots, SOURCE, 1)["player_observation"]
    assert observed["events"][0]["scene"] == observed["events"][2]["scene"]
    assert observed["original_scene_restored"] is (not replaced_scene)
    with pytest.raises(ValueError, match="Reused native"):
        compare_scene_indices([roots[0], roots[1], roots[0]], SOURCE, 1)
    event["pid"] = 101
    (roots[-1] / "player.log").write_text("MCP_SCENE_INDEX " + json.dumps(event))
    with pytest.raises(ValueError, match="Player callback does not match"):
        compare_scene_indices(roots, SOURCE, 1)
