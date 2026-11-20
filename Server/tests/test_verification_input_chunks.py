"""Large candidate inventories remain bounded and source-linked across execution."""

from hashlib import sha256
import json

from click.testing import CliRunner
import pytest

from cli.commands.verification_input_chunks import assemble_index, capture_chunk, input_chunks
from services.script_quality.pages import encoded_size
from services.verification.input_chunks import CHUNK_BYTES, ChunkRef, load_chunks
from services.verification.player_candidate import (
    PreparedCandidate,
    parse_manifest,
    prepare_candidate,
    source_revision,
)
from .test_play_scenario_player import bundle, child
from .test_play_scenario_player_assurance import verified_bundle
from .test_verification_player_candidates import registered
from .test_verification_service import service


def setup_inventory(registered, count):
    project, _, register = registered
    marker = project / "ProjectSettings" / "ProjectVersion.txt"
    marker.parent.mkdir()
    marker.write_bytes(b"fixture")
    doc = register("large")
    directory = project.parent / "registrations" / "large"
    paths = []
    for index in range(count):
        for suffix in (".cs", ".cs.meta"):
            path = f"Assets/A{index:03}{suffix}"
            (project / path).write_text(path)
            paths.append(path)
    return project, directory, doc, paths


def publish_index(project, directory, doc, groups):
    refs = [capture_chunk(directory, project, group) for group in groups]
    index = assemble_index(directory, [ref["sha256"] for ref in refs])
    doc.pop("inputs")
    doc.update(index)
    (directory / "candidate.json").write_text(json.dumps(doc))
    bundle_path = directory / "build" / "scenario-bundle.json"
    frozen = json.loads(bundle_path.read_bytes())
    frozen["build_source_revision"] = doc["source_revision"]
    bundle_path.write_text(json.dumps(frozen))
    return refs


@pytest.mark.asyncio
async def test_verify_300_paths_and_reprocess_only_changed_chunk(registered, service, child):
    project, directory, doc, paths = setup_inventory(registered, 150)
    groups = [paths[n : n + 100] for n in range(0, 300, 100)]
    refs = publish_index(project, directory, doc, groups)
    assert doc["source_revision"] == source_revision(
        {path: sha256((project / path).read_bytes()).hexdigest() for path in paths}
    )
    subject, ctx, _, native = service
    native.return_value["data"]["projectRoot"] = str(project)
    response = await subject.execute(ctx, "run", {"candidate_id": "large", "wait_seconds": 5})
    run_id = response["data"]["run_id"]
    report = (await subject.execute(ctx, "report", {"run_id": run_id}))["data"]
    assert report["state"] == "passed", report
    proof = report["latest_input_inventory"]["candidate"]
    assert proof["complete"] and proof["verified_path_count"] == proof["path_count"] == 300
    assert all(chunk["live_state"] == "matched" for chunk in proof["chunks"])
    assert proof["source_revision"] == doc["source_revision"]

    (project / groups[1][0]).write_bytes(b"changed")
    stale = await subject.execute(ctx, "run", {"candidate_id": "large", "wait_seconds": 5})
    assert stale["data"]["state"] == "stale" and len(child[1]) == 1
    changed = stale["data"]["latest_input_inventory"]["candidate"]["chunks"]
    assert [row["live_state"] for row in changed] == ["matched", "stale", "matched"]
    new_ref = capture_chunk(directory, project, groups[1])
    assert new_ref["sha256"] != refs[1]["sha256"]
    refs[1] = new_ref
    rebuilt = assemble_index(directory, [row["sha256"] for row in refs])
    assert rebuilt["input_chunks"] == refs
    assert rebuilt["source_revision"] != proof["source_revision"]


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing", "stale"])
async def test_partial_registration_retains_remaining_groups_and_refuses_frozen_launch(
    registered, service, child, fault
):
    project, directory, doc, paths = setup_inventory(registered, 3)
    refs = publish_index(project, directory, doc, [paths[:2], paths[2:4], paths[4:]])
    path = directory / "input-chunks" / (refs[1]["sha256"] + ".json")
    if fault == "missing":
        path.unlink()
    else:
        path.write_bytes(b"{}")
    subject, ctx, _, native = service
    native.return_value["data"]["projectRoot"] = str(project)
    response = await subject.execute(
        ctx,
        "run",
        {
            "candidate_id": "large",
            "source_mode": "frozen",
            "wait_seconds": 5,
        },
    )
    report = response["data"]
    assert report["state"] == "incomplete" and not child[1]
    proof = report["latest_input_inventory"]["candidate"]
    assert not proof["complete"] and proof["verified_path_count"] == 4 and proof["path_count"] == 6
    assert [r["registration_state"] for r in proof["chunks"]] == ["verified", fault, "verified"]
    with pytest.raises(ValueError, match="candidate_chunk_inventory_incomplete"):
        parse_manifest((directory / "candidate.json").read_bytes(), directory, project)


def test_chunk_cli_recovery_reuses_identical_content_and_checks_global_order(registered):
    project, directory, _, paths = setup_inventory(registered, 2)
    runner = CliRunner()
    args = ["capture", "--candidate-id", "large", "--project", str(project), "--path", paths[0]]
    first = runner.invoke(input_chunks, args)
    assert first.exit_code == 0, first.output
    assert runner.invoke(input_chunks, args).output == first.output
    second = capture_chunk(directory, project, paths[1:])
    digests = [json.loads(first.output)["sha256"], second["sha256"]]
    index = ["index", "--candidate-id", "large", "--chunk", digests[0], "--chunk", digests[1]]
    assert json.loads(runner.invoke(input_chunks, index).output)["schema_version"] == 2
    with pytest.raises(ValueError, match="order_or_duplicate"):
        assemble_index(directory, reversed(digests))
    with pytest.raises(ValueError, match="duplicate_input_chunk"):
        assemble_index(directory, [digests[0], digests[0]])
    assert runner.invoke(input_chunks, args + ["--path", "../Outside.cs"]).exit_code != 0
    assert runner.invoke(input_chunks, args + ["--path", paths[0]]).exit_code != 0


@pytest.mark.parametrize("fault", ["duplicate_key", "unsafe", "bad_count", "long"])
def test_invalid_chunk_payload_is_bounded(tmp_path, fault):
    digest = "a" * 64
    key = "../outside.cs" if fault == "unsafe" else "Assets/Wind.cs"
    pair = json.dumps(key) + ":" + json.dumps(digest)
    payload = (
        '{"inputs":{' + pair + ("," + pair if fault == "duplicate_key" else "") + "}}"
    ).encode()
    if fault == "long":
        payload = payload.ljust(CHUNK_BYTES + 1)
    folder = tmp_path / "input-chunks"
    folder.mkdir()
    identity = sha256(payload).hexdigest()
    (folder / (identity + ".json")).write_bytes(payload)
    ref = ChunkRef(
        sha256=identity,
        count=2 if fault == "bad_count" else 1,
        size_bytes=min(len(payload), CHUNK_BYTES),
    )
    if fault == "long":
        assert load_chunks(tmp_path, [ref])[1][0].state == "unavailable"
    else:
        with pytest.raises(ValueError):
            load_chunks(tmp_path, [ref])


def test_live_edit_during_multi_file_sample_invalidates_capture(registered, monkeypatch):
    from services.verification import input_chunks as chunks

    project, directory, doc, paths = setup_inventory(registered, 3)
    publish_index(project, directory, doc, [paths[:2], paths[2:]])
    original = chunks.read_bytes

    def changed_earlier_file(path, limit):
        if path == project / paths[-1]:
            (project / paths[0]).write_bytes(b"edited during observation")
        return original(path, limit)

    monkeypatch.setattr(chunks, "read_bytes", changed_earlier_file)
    with pytest.raises(ValueError, match="changed_during_sample"):
        prepare_candidate({"projectRoot": str(project)}, {"candidate_id": "large"})


@pytest.mark.asyncio
async def test_registered_chunk_disappearing_during_player_overrides_native_pass(
    registered, service, child, monkeypatch
):
    from services.verification import player_runner

    project, directory, doc, paths = setup_inventory(registered, 2)
    refs = publish_index(project, directory, doc, [paths[:2], paths[2:]])
    launch = player_runner.run_player

    def change_during_run(options):
        (directory / "input-chunks" / (refs[1]["sha256"] + ".json")).unlink()
        return launch(options)

    monkeypatch.setattr(player_runner, "run_player", change_during_run)
    subject, ctx, _, native = service
    native.return_value["data"]["projectRoot"] = str(project)
    response = await subject.execute(ctx, "run", {"candidate_id": "large", "wait_seconds": 5})
    report = response["data"]
    states = {row["name"]: row["state"] for row in report["stages"]}
    assert len(child[1]) == 1 and states["standalone_player"] == "passed"
    assert report["state"] == states["final_source"] == "incomplete"
    proof = report["latest_input_inventory"]["candidate"]
    assert proof["verified_path_count"] == 2 and proof["path_count"] == 4


def test_global_revision_rejects_a_valid_chunk_from_another_generation(registered):
    project, directory, doc, paths = setup_inventory(registered, 2)
    refs = publish_index(project, directory, doc, [paths[:2], paths[2:]])
    (project / paths[-1]).write_bytes(b"new metadata")
    replacement = capture_chunk(directory, project, paths[2:])
    doc["input_chunks"] = [refs[0], replacement]
    with pytest.raises(ValueError, match="candidate_input_revision_mismatch"):
        parse_manifest(json.dumps(doc).encode(), directory, project)


def test_1024_long_paths_preserve_all_mapped_checks_and_broader_fallback(tmp_path):
    paths = [f"Assets/{'Area/' * 44}Quest{i:04d}.cs" for i in range(1024)]
    inputs = dict.fromkeys(paths, "b" * 64)
    chunks = tmp_path / "input-chunks"
    chunks.mkdir()
    refs = []
    for offset in range(0, len(paths), 128):
        group = {path: inputs[path] for path in paths[offset : offset + 128]}
        content = json.dumps({"inputs": group}).encode()
        digest = sha256(content).hexdigest()
        (chunks / (digest + ".json")).write_bytes(content)
        refs.append({"sha256": digest, "size_bytes": len(content), "count": len(group)})
    fields = {
        "project": str(tmp_path),
        "scenario": "quest",
        "build_id": "a" * 32,
        "payload_hash": "c" * 64,
    }
    candidate = parse_manifest(
        json.dumps(
            {
                **fields,
                "schema_version": 2,
                "source_revision": source_revision(inputs),
                "input_chunks": refs,
                "broader_check": "complete-mode",
                "mappings": [
                    {"path": path, "tests": ["Quest.Check"], "scenarios": ["quest-flow"]}
                    for path in paths[:100]
                ],
            }
        ).encode(),
        tmp_path,
        tmp_path,
    )
    before = {paths[0]: "a" * 64}
    baseline = parse_manifest(
        json.dumps(
            {
                **fields,
                "schema_version": 1,
                "source_revision": source_revision(before),
                "inputs": before,
            }
        ).encode(),
        tmp_path,
        tmp_path,
    )
    prepared = PreparedCandidate(
        tmp_path, tmp_path, "after", "before", candidate, baseline, "d" * 64, "e" * 64, inputs
    )
    selection = prepared.comparison()["selected_checks"]
    rows = {(r["kind"], r["id"]): r for r in selection["checks"]}
    assert set(rows) == {
        ("profile", "complete-mode"),
        ("test", "Quest.Check"),
        ("scenario", "quest-flow"),
    }
    fallback = rows[("profile", "complete-mode")]
    assert fallback["reasons"] == ["unmapped_affected_paths"] and not fallback["reasons_truncated"]
    assert selection["check_reason_count"] == 201
    assert rows[("test", "Quest.Check")]["reason_count"] == 100
    assert rows[("test", "Quest.Check")]["reasons_truncated"]
    assert selection["unmapped_path_count"] == 924 and selection["unmapped_paths_truncated"]
    assert selection["evidence_truncated"] and not selection["outstanding"]
    assert not selection["mapping_complete"] and encoded_size(selection) <= 64 * 1024
