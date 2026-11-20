"""Regression controls for replaying dependency and write-recovery claims offline."""

import json
import runpy
from hashlib import sha256
from pathlib import Path

import pytest
from services.verification.input_chunks import InputChunk
from services.verification.player_candidate import ChunkedCandidate
from unity_mcp_evidence.team_bundle import pack, replay


@pytest.fixture
def captures(tmp_path):
    tools = Path(__file__).resolve().parents[2] / "tools"
    controls = runpy.run_path(str(tools / "check_team_evidence.py"))
    return controls["workflow_controls"](tmp_path)


def _handoff(tmp_path, captures, kind, omit=None, withhold=()):
    path = tmp_path / "handoff.json"
    inputs = {
        role: () if role == omit else (source,)
        for role, source in captures[kind].items()
        if role not in {"asset-guid", "mapping-chunk"}
    }
    if inputs.get("mappings") and "mapping-chunk" in captures[kind]:
        inputs["mappings"] += (captures[kind]["mapping-chunk"],)
    result = pack(kind, inputs, withhold, (), path, subject=captures[kind].get("asset-guid"))
    report, code = replay(path, result["bundle_sha256"])
    return path, report, code


@pytest.mark.parametrize(
    ("kind", "omitted", "gaps"),
    [
        ("impact", "baseline", {"asset_change"}),
        ("impact", "candidate", {"asset_change", "consuming_scene", "selected_tests"}),
        ("impact", "mappings", {"selected_tests"}),
        ("impact", "selected", {"selected_tests"}),
        (
            "recovery",
            "reconcile",
            {"last_confirmed_write", "resumed_step", "resumed_write_confirmed"},
        ),
        ("recovery", "replan", {"resumed_step", "resumed_write_confirmed"}),
        ("recovery", "resumed", {"resumed_step", "resumed_write_confirmed"}),
    ],
)
def test_each_missing_input_marks_only_its_dependent_conclusions(
    tmp_path, captures, kind, omitted, gaps
):
    _, report, code = _handoff(tmp_path, captures, kind, omit=omitted)
    assert code == 2 and report["unavailable_roles"] == [omitted]
    assert set(report["assessment"]["unavailable_conclusions"]) == gaps


def test_mismatched_fixture_input_does_not_establish_test_selection(tmp_path, captures):
    selected = captures["impact"]["selected"]
    selected.write_text(selected.read_text().replace("b" * 64, "c" * 64), encoding="utf-8")
    _, report, code = _handoff(tmp_path, captures, "impact")
    assert code == 2 and report["assessment"]["selected_tests"] is None
    assert report["assessment"]["asset_changed"] is True
    assert report["assessment"]["scene_dependency"][-1] == report["assessment"]["asset"]


def test_impact_control_uses_the_production_candidate_manifest_and_matching_input_chunk(
    tmp_path, captures
):
    paths = captures["impact"]
    candidate = ChunkedCandidate.model_validate_json(paths["mappings"].read_bytes())
    raw = paths["mapping-chunk"].read_bytes()
    chunk = InputChunk.model_validate_json(raw)
    assert candidate.input_chunks[0].sha256 == sha256(raw).hexdigest()
    candidate.validate_capture(chunk.inputs)
    _, report, code = _handoff(tmp_path, captures, "impact")
    assert code == 0 and list(report["assessment"]["selected_tests"].values()) == ["Passed"]


def test_stale_manifest_cannot_attribute_selected_tests_even_with_the_same_scene_guid(
    tmp_path, captures
):
    paths = captures["impact"]
    candidate = json.loads(paths["mappings"].read_bytes())
    inputs = json.loads(paths.pop("mapping-chunk").read_bytes())["inputs"]
    target = next(path for path in inputs if path.endswith(".mat.meta"))
    inputs[target] = "e" * 64
    del candidate["input_chunks"]
    candidate.update(
        schema_version=1,
        inputs=inputs,
        source_revision=sha256(
            "".join(f"{name}\n{blob}\n" for name, blob in sorted(inputs.items())).encode()
        ).hexdigest(),
    )
    paths["mappings"].write_text(json.dumps(candidate), encoding="utf-8")
    _, report, code = _handoff(tmp_path, captures, "impact")
    assert code == 2 and report["assessment"]["selected_tests"] is None
    assert report["assessment"]["unavailable_conclusions"] == {
        "selected_tests": "mapping_source_does_not_confirm_candidate_scene_and_asset"
    }


def test_mapping_manifest_without_its_input_chunk_preserves_only_the_graph_conclusions(
    tmp_path, captures
):
    del captures["impact"]["mapping-chunk"]
    _, report, code = _handoff(tmp_path, captures, "impact")
    assert code == 2 and report["assessment"]["asset_changed"] is True
    assert report["assessment"]["unavailable_conclusions"] == {
        "selected_tests": "mapping_source_does_not_confirm_candidate_scene_and_asset"
    }


@pytest.mark.parametrize("part", ["owner", "receipt", "observation", "legacy", "pending"])
def test_prefab_successor_needs_same_receipt_owned_approval(tmp_path, captures, part):
    resumed = captures["recovery"]["resumed"]
    doc = json.loads(resumed.read_bytes())
    if part == "owner":
        doc["data"]["handoff"]["previous_workflow_id"] = "d" * 64
    if part == "receipt":
        doc["execution_receipt"]["receipt_id"] = "1" * 32
    if part == "observation":
        doc["data"]["handoff"]["approved_observation_id"] = "d" * 64
    if part == "legacy":
        del doc["data"]["handoff"]
    if part == "pending":
        doc["data"]["handoff"]["phase"] = "registering"
        doc["success"] = False
    resumed.write_text(json.dumps(doc), encoding="utf-8")
    path, report, code = _handoff(tmp_path, captures, "recovery")
    assessment = report["assessment"]
    assert code == 2 and assessment["last_confirmed_write"] == "create_prefab"
    assert assessment["resumed_write_confirmed"] is None
    assert assessment["resumed_step"] == ("register_prefab" if part == "pending" else None)
    assert b"PrivateControl" not in path.read_bytes()
    if part == "legacy":
        assert report["sources"]["resumed"]["observation"] == "legacy"


def test_reader_ignores_sender_summaries_and_recomputes_actual_checkpoint_phase(tmp_path, captures):
    source = captures["recovery"]["reconcile"]
    doc = json.loads(source.read_bytes())
    doc["data"]["completed_steps"] = ["register_prefab"]
    doc["state"] = "registered"
    source.write_text(json.dumps(doc), encoding="utf-8")
    _, report, code = _handoff(tmp_path, captures, "recovery")
    assert code == 0
    assert report["assessment"]["last_confirmed_write"] == "create_prefab"
    assert report["assessment"]["resumed_step"] == "register_prefab"
