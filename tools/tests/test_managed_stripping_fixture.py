"""Offline controls for the evidence reader. The fixture itself supplies native coverage."""

import json
import sys
from pathlib import Path, PurePosixPath
from uuid import UUID

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from run_managed_stripping_fixture import (
    MARKER,
    EvidenceError,
    check_metadata,
    check_observation,
    fixture_inputs,
    read_log_observation,
)


def test_generated_runtime_assembly_excludes_editor_build_script():
    common, _, _ = fixture_inputs()
    sources = {PurePosixPath(path).name: PurePosixPath(path) for path in common}
    runtime_root = sources["ManagedStripping.Runtime.asmdef"].parent
    editor = sources["FixtureBuild.cs"]

    # A parent asmdef would override Unity's predefined Editor folder assembly.
    assert "Editor" in editor.parent.parts
    assert runtime_root not in editor.parents
    assert runtime_root in sources["ReflectionProbe.cs"].parents
    assert runtime_root in sources["ReflectionTarget.cs"].parents


def test_rejects_player_marker_from_another_build(tmp_path):
    nonce = "a" * 32
    player_log = tmp_path / "player.log"
    player_log.write_text(
        MARKER
        + json.dumps(
            {
                "nonce": nonce,
                "unityVersion": "6000.0.69f1",
                "platform": "WindowsPlayer",
                "backend": "IL2CPP",
                "buildGuid": "b" * 32,
                "typeName": "ManagedStrippingFixture.ReflectionTarget",
                "methodName": "Read",
                "state": "missing_type",
                "value": "",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(EvidenceError, match="another build"):
        check_observation(
            read_log_observation(player_log), nonce, "6000.0.69f1", False, False, UUID("c" * 32)
        )


@pytest.mark.parametrize(
    ("repaired", "after"),
    [
        (False, {"probe": True, "target": True, "method": True}),
        (True, {"probe": True, "target": True, "method": False}),
        (True, {"probe": False, "target": True, "method": True}),
    ],
)
def test_rejects_output_missing_the_case_specific_linker_change(repaired, after):
    before = {"probe": True, "target": True, "method": True}
    with pytest.raises(EvidenceError, match="UnityLinker output"):
        check_metadata(before, after, repaired)
