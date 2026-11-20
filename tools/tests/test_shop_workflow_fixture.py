"""Independent controls for the moved Resources prefab demonstration."""

from pathlib import Path
from hashlib import sha256
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from TestProjects.UnityMCPTests.Verification.run_prefab_move import (
    PROBE,
    SCENE,
    confirm_inputs,
    retain_sources,
)
from TestProjects.UnityMCPTests.Verification.shop_workflow_observation import (
    check_observation,
    read_player_observation,
)


@pytest.fixture
def moved():
    guid = "0123456789abcdef0123456789abcdef"
    editor = {
        "phase": "moved",
        "source_revision": "candidate",
        "asset_guid": guid,
        "serialized_guid": guid,
        "dependency_retained": True,
        "key": "VerifiedShop/UI/ShopBanner",
        "loader_guid": "",
    }
    player = {
        "key": editor["key"],
        "expected": "ShopBanner",
        "loaded": "",
        "same_asset": False,
        "visible_banners": 0,
    }
    return guid, editor, player


def test_moved_asset_preserves_guid_and_breaks_only_the_loader(moved):
    guid, editor, player = moved
    check_observation("moved", "candidate", guid, editor, player)


@pytest.mark.parametrize(
    ("source", "key", "replacement"),
    [
        ("editor", "serialized_guid", ""),
        ("editor", "asset_guid", "f" * 32),
        ("editor", "source_revision", "stale-build"),
        ("editor", "dependency_retained", False),
        ("editor", "loader_guid", "f" * 32),
        ("player", "loaded", "ShopBanner"),
        ("player", "expected", ""),
        ("player", "visible_banners", 1),
    ],
)
def test_unrelated_failure_or_stale_result_cannot_prove_the_move(moved, source, key, replacement):
    guid, editor, player = moved
    (editor if source == "editor" else player)[key] = replacement
    with pytest.raises(ValueError):
        check_observation("moved", "candidate", guid, editor, player)


def test_repaired_case_needs_the_expected_asset_and_visible_instance(moved):
    guid, editor, player = moved
    key = "VerifiedShop/Store/ShopBanner"
    editor.update(phase="repaired", key=key, loader_guid=guid)
    player.update(key=key, loaded="ShopBanner", same_asset=True, visible_banners=1)
    check_observation("repaired", "candidate", guid, editor, player)
    player["same_asset"] = False
    with pytest.raises(ValueError):
        check_observation("repaired", "candidate", guid, editor, player)


def test_historical_candidate_bytes_survive_move_and_detect_new_edits(tmp_path):
    project = tmp_path / "fixture"
    retained = tmp_path / "phase"
    retained.mkdir()
    prefab = "Assets/VerifiedWorkflowShop/Resources/VerifiedShop/UI/ShopBanner.prefab"
    inputs = {}
    for name in sorted(p + suffix for p in (SCENE, prefab, PROBE) for suffix in ("", ".meta")):
        path = project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(name.encode())
        inputs[name] = sha256(path.read_bytes()).hexdigest()
    manifest = "".join(f"{p}\n{h}\n" for p, h in inputs.items())
    revision = sha256(manifest.encode()).hexdigest()
    editor = {
        "project": str(project),
        "phase": "original",
        "session": "session-A",
        "unity_version": "fixture",
        "package_version": "fixture",
        "asset_path": prefab,
        "scene": SCENE,
        "inputs": inputs,
        "source_revision": revision,
    }
    assert confirm_inputs(project, editor, "original", "session-A") == revision
    retain_sources(project, retained, inputs)
    (project / PROBE).write_bytes(b"concurrent edit")
    with pytest.raises(ValueError, match="changed"):
        confirm_inputs(project, editor, "original", "session-A")
    assert sha256((retained / "sources" / PROBE).read_bytes()).hexdigest() == inputs[PROBE]


def test_player_marker_must_start_a_log_line_and_conflicting_results_fail(tmp_path):
    path = tmp_path / "player.log"
    path.write_text('previous test mentions SHOP_WORKFLOW_OBSERVATION {"loaded":"unrelated"}\n')
    with pytest.raises(ValueError, match="missing_or_ambiguous"):
        read_player_observation(path)
    path.write_text(
        'SHOP_WORKFLOW_OBSERVATION {"loaded":"yes"}\nSHOP_WORKFLOW_OBSERVATION {"loaded":"no"}\n'
    )
    with pytest.raises(ValueError, match="missing_or_ambiguous"):
        read_player_observation(path)
