"""Offline controls for native compiler and rendered texture observation comparisons."""

from copy import deepcopy
import hashlib
import json

import pytest
from click.testing import CliRunner

from cli.commands.verification_configuration import (
    compare,
    configuration_diff,
    fingerprint,
    load_observation,
)
from models.verification import parse_verification_options


def seal(value):
    raw = fingerprint({k: v for k, v in value.items() if k != "observation_revision"})
    value["observation_revision"] = hashlib.sha256(raw.encode("ascii")).hexdigest()
    return value


def common(kind):
    return {
        "schema_version": 1,
        "kind": kind,
        "project_identity": "1" * 64,
        "guid": "1" * 32,
        "source_sha256": "b" * 64,
        "target": "StandaloneWindows64",
        "unity_version": "2021.3.45f2",
    }


def assembly():
    return {
        **common("assembly"),
        "path": "Assets/Optional.asmdef",
        "source": "Assets/Type.cs",
        "assembly": "Optional",
        "asmdef_sha256": "3" * 64,
        "resource_versions": {"com.example.module": "1.0.0"},
        "editor_version_symbols": {"WITH_MODULE": True},
        "target_platforms": ["WindowsStandalone64"],
        "conditions": {
            "includePlatforms": [],
            "excludePlatforms": [],
            "defineConstraints": ["UNITY_EDITOR || WITH_MODULE"],
            "versionDefines": [
                {"name": "com.example.module", "expression": "1.0.0", "define": "WITH_MODULE"}
            ],
        },
        "editor": {
            "assemblies_type": "Editor",
            "included": True,
            "source_included": True,
            "defines": ["UNITY_EDITOR", "WITH_MODULE"],
        },
        "player": {
            "assemblies_type": "PlayerWithoutTestAssemblies",
            "included": True,
            "source_included": True,
            "defines": ["WITH_MODULE"],
        },
    }


def test_actual_compiler_membership_and_matching_version_condition():
    before = assembly()
    after = deepcopy(before)
    after["conditions"]["versionDefines"][0]["expression"] = "2.0.0"
    after["editor_version_symbols"]["WITH_MODULE"] = False
    after["editor"]["defines"] = ["UNITY_EDITOR"]
    after["player"].update(included=False, source_included=False, defines=None)
    diff = compare(load_observation(seal(before)), load_observation(seal(after)))
    assert diff["target_assembly_removed"]
    assert diff["compiler_inputs"]["editor"][1]["source_included"]
    assert [r["value"] for r in diff["target_exclusions"]] == ["UNITY_EDITOR || WITH_MODULE"]
    after["conditions"]["defineConstraints"] = ["UNKNOWN || WITH_MODULE"]
    assert (
        compare(load_observation(before), load_observation(seal(after)))["exclusion_evidence"]
        == "unresolved"
    )
    after["conditions"]["includePlatforms"] = ["Editor"]
    assert (
        compare(load_observation(before), load_observation(seal(after)))["target_exclusions"][0][
            "condition"
        ]
        == "includePlatforms"
    )


def texture():
    return {
        **common("texture"),
        "path": "Assets/T.png.meta",
        "meta_sha256": "3" * 64,
        "search_folder": "Assets",
        "platform": "Standalone",
        "importer": {
            "sRGBTexture": True,
            "textureCompression": "Uncompressed",
            "overridden": False,
            "platformFormat": "Automatic",
            "platformTextureCompression": "Uncompressed",
            "platformMaxSize": 2048,
            "platformCompressionQuality": 50,
        },
        "references": [
            {"owner": "Assets/M.mat", "target": "Assets/T.png"},
            {"owner": "Assets/S.unity", "target": "Assets/M.mat"},
        ],
        "dependency_hashes": {"Assets/M.mat": "6" * 32, "Assets/S.unity": "7" * 32},
        "owner_inputs": {
            p + s: "8" * 64 for p in ("Assets/M.mat", "Assets/S.unity") for s in ("", ".meta")
        },
        "coverage": "direct_material_and_saved_scene_dependencies_in_selected_folder",
    }


def run(root, observation, name, red):
    directory = root / name
    directory.mkdir()
    identity = {
        "job_id": name,
        "build_id": name,
        "build_source_revision": observation["observation_revision"],
        "payload_hash": "a" * 64,
        "definition_hash": "b" * 64,
    }
    (directory / "request.json").write_text(json.dumps(identity), encoding="utf-8")
    (directory / "run.json").write_text(
        json.dumps(
            {
                "job_id": name,
                "reproduction": {**identity, "environment": {"platform": "WindowsPlayer"}},
                "execution_environment": "player",
                "status": "succeeded",
                "finalization_state": "completed",
            }
        ),
        encoding="utf-8",
    )
    (directory / "process.json").write_text(
        json.dumps(
            {
                "job_id": name,
                "request_sha256": hashlib.sha256(
                    (directory / "request.json").read_bytes()
                ).hexdigest(),
                "process_ended": True,
                "forced_termination": False,
                "actual_exit_code": 0,
            }
        ),
        encoding="utf-8",
    )
    (directory / "diagnostics.json").write_text(
        json.dumps(
            {
                "job_id": name,
                "payload_verification": "launcher_admission",
                "cleanup_state": "native_finalized",
            }
        ),
        encoding="utf-8",
    )
    pixel = {
        "job_id": name,
        "method": "render_texture_read_pixels",
        "sample": "center",
        "color_space": "Linear",
        "device": "Direct3D11",
        "scene": "Assets/S.unity",
        "material": "Assets/M.mat",
        "rgba": [red] * 3 + [255],
    }
    (directory / "player.log").write_text(
        "MCP_CONFIGURATION_PIXEL " + json.dumps(pixel), encoding="utf-8"
    )
    return directory


def test_prediction_and_matched_player_measurement_are_independent(tmp_path):
    before = seal(texture())
    after = deepcopy(before)
    after["importer"]["sRGBTexture"] = False
    after["meta_sha256"] = "4" * 64
    after = seal(after)
    old, new = load_observation(before), load_observation(after)
    prediction = compare(old, new)
    assert prediction["player_observation"]["state"] == "unavailable"
    assert prediction["source_unchanged"] and prediction["meta_changed"]
    assert prediction["only_meta_changed_in_selected_inputs"]
    competing = deepcopy(after)
    competing["owner_inputs"]["Assets/M.mat"] = "9" * 64
    assert not compare(old, load_observation(seal(competing)))[
        "only_meta_changed_in_selected_inputs"
    ]
    first, second = run(tmp_path, before, "c" * 32, 55), run(tmp_path, after, "d" * 32, 55)
    assert compare(old, new, first, second)["player_observation"]["state"] == "unchanged"
    with pytest.raises(ValueError, match="does_not_match"):
        compare(old, new, second, first)
    (second / "player.log").write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="pixel_missing"):
        compare(old, new, first, second)


def test_closed_selection_and_cli_rejects_changed_native_evidence(tmp_path):
    assert parse_verification_options(
        "observe_assembly", {"path": "Assets/A.asmdef", "source": "Assets/A.cs"}
    ).source.endswith(".cs")
    with pytest.raises(ValueError):
        parse_verification_options(
            "observe_texture", {"path": "Assets/T.png.meta", "platform": "Standalone"}
        )
    before = seal(texture())
    first, second = tmp_path / "first.json", tmp_path / "second.json"
    first.write_text(json.dumps({"success": True, "data": before}), encoding="utf-8")
    second.write_text(json.dumps(before), encoding="utf-8")
    runner = CliRunner()
    assert runner.invoke(configuration_diff, [str(first), str(second)]).exit_code == 0
    before["importer"]["sRGBTexture"] = False
    second.write_text(json.dumps(before), encoding="utf-8")
    rejected = runner.invoke(configuration_diff, [str(first), str(second)])
    assert rejected.exit_code != 0 and "digest_mismatch" in rejected.output
