"""Crossed and subset trials must be tied to the admitted inputs and independent jobs."""

import json
from hashlib import sha256
from zipfile import ZipFile

import pytest
from cli.main import cli
from cli.utils.verification_causal import oracle_matrix
from cli.utils.verification_causal_minimize import export_subset, minimize_changes
from click.testing import CliRunner
from services.verification.input_chunks import CHUNK_BYTES, encode_input_chunk, source_revision
from tests.test_verification_change_evidence import make_capture, with_editor_check


def digest(text):
    return sha256(text.encode()).hexdigest()


def snapshot(*values):
    inputs = {}
    for index, text in enumerate(values):
        asset = f"Assets/{index}.cs"
        inputs[asset] = digest(text)
        inputs[asset + ".meta"] = digest(asset)
    return inputs


def archive(
    root, index, inputs, state, definition="a" * 64, *, expected=None, observed=None, test=False
):
    player_state = "passed" if expected is not None or test else state
    report_path, native_path = make_capture(root, index, player_state)
    source = source_revision(inputs)
    build = source[:32]
    candidate = {
        "schema_version": 1,
        "project": str(root),
        "scenario": "doors",
        "source_revision": source,
        "inputs": inputs,
        "build_id": build,
        "payload_hash": digest(build),
    }
    if expected is not None:
        candidate["state_check"] = {"log_marker": "STATE ", "expected": expected}
    candidate_path = root / f"candidate-{index}.json"
    content = json.dumps(candidate).encode()
    candidate_path.write_bytes(content)
    report = json.loads(report_path.read_text())["data"]
    report["source_token"] = source
    report["comparison"].update(manifest_sha256=sha256(content).hexdigest(), source_revision=source)
    fields = {"build_id": build, "payload_hash": digest(build), "build_source_revision": source}
    report["stages"][0]["evidence"].update(fields)
    if expected is not None:
        report["state"] = state
        report["stages"].append(
            {
                "name": "observed_state",
                "state": state,
                "evidence": {"expected": expected, "observed": observed},
            }
        )
    native = json.loads(native_path.read_text())
    native["reproduction"].update(**fields, definition_hash=definition)
    report_path.write_text(json.dumps({"success": True, "data": report}))
    native_path.write_text(json.dumps(native))
    if test:
        with_editor_check((report_path, native_path), state=state)
    return {
        "verification": report_path.name,
        "native": native_path.name,
        "candidate": candidate_path.name,
    }


def saved(root, data):
    path = root / "trials.json"
    path.write_text(json.dumps(data))
    return path


def matrix(root, outcomes):
    games = {"old": snapshot("old"), "new": snapshot("new")}
    oracles = {name: {"definition_hash": digest(name)} for name in games}
    data = {
        "schema": "verification_oracle_matrix@1",
        "games": games,
        "oracles": oracles,
        "check": {"kind": "scenario", "id": "doors"},
        "trials": [],
    }
    for index, ((game, oracle), state) in enumerate(
        zip(((g, o) for g in games for o in oracles), outcomes, strict=True), 1
    ):
        pair = archive(root, index, games[game], state, oracles[oracle]["definition_hash"])
        data["trials"].append({"game": game, "oracle": oracle, **pair})
    return data


@pytest.mark.parametrize(
    ("cross", "result", "code"),
    [
        (["failed", "passed"], "repair_supported", 0),
        (["passed", "failed"], "weakened_oracle", 1),
        (["passed", "passed"], "repair_and_weakened_oracle", 1),
        (["failed", "failed"], "joint_change_required", 1),
    ],
)
def test_oracle_matrix_through_registered_cli(tmp_path, cross, result, code):
    path = saved(tmp_path, matrix(tmp_path, ["failed", *cross, "passed"]))
    command = CliRunner().invoke(
        cli,
        [
            "--no-project-context",
            "--format",
            "json",
            "verification-evidence",
            "oracle-matrix",
            str(path),
        ],
    )
    assert command.exit_code == code, command.output
    output = json.loads(command.output)
    assert output["state"] == result
    assert len({cell["job_id"] for cell in output["cells"]}) == 4


@pytest.mark.parametrize(
    "bad",
    [
        "missing",
        "mixed_source",
        "wrong_oracle",
        "replayed",
        "other_environment",
        "candidate",
        "stale",
    ],
)
def test_each_bad_cell_remains_incomplete(tmp_path, bad):
    data = matrix(tmp_path, ["failed", "failed", "passed", "passed"])
    rows = data["trials"]
    if bad == "missing":
        rows.pop()
    elif bad == "candidate":
        (tmp_path / rows[3]["candidate"]).write_bytes(b"{}")
    elif bad == "mixed_source":
        rows[3].update({k: rows[1][k] for k in ("candidate", "verification", "native")})
    elif bad == "replayed":
        rows[1].update({k: rows[0][k] for k in ("candidate", "verification", "native")})
    elif bad == "stale":
        path = tmp_path / rows[3]["verification"]
        report = json.loads(path.read_text())
        report["data"]["stages"].pop()
        path.write_text(json.dumps(report))
    else:
        path = tmp_path / rows[3]["native"]
        native = json.loads(path.read_text())
        if bad == "wrong_oracle":
            native["reproduction"]["definition_hash"] = digest("other")
        else:
            native["reproduction"]["environment"]["architecture"] = "other"
        path.write_text(json.dumps(native))
    output, code = oracle_matrix(saved(tmp_path, data))
    assert code == 2 and output["state"] == "incomplete"
    assert any(row["state"] in {"missing", "incomplete"} for row in output["cells"])


def test_compiled_nunit_oracle_uses_captured_test_source(tmp_path):
    data = matrix(tmp_path, ["failed", "passed", "failed", "passed"])
    data["check"] = {"kind": "test", "id": "Doors.Opens"}
    for revision in data["oracles"]:
        data["oracles"][revision] = {
            "definition_hash": "b" * 64,
            "inputs": {"Assets/Oracle.cs": digest(revision)},
        }
    for index, row in enumerate(data["trials"], 1):
        game = data["games"][row["game"]]
        oracle = data["oracles"][row["oracle"]]
        state = "failed" if row["oracle"] == "old" else "passed"
        row.update(archive(tmp_path, index, game | oracle["inputs"], state, test=True))
    output, code = oracle_matrix(saved(tmp_path, data))
    assert (output["state"], code) == ("weakened_oracle", 1)


def test_state_oracle_relabelled_as_code_fix_is_detected(tmp_path):
    data = matrix(tmp_path, ["failed", "passed", "failed", "passed"])
    data["check"] = {"kind": "observation", "id": "doors"}
    for revision in data["oracles"]:
        data["oracles"][revision] = {"expected_state": {"open": revision == "old"}}
    for index, row in enumerate(data["trials"], 1):
        game = data["games"][row["game"]]
        expected = data["oracles"][row["oracle"]]["expected_state"]
        row.update(
            archive(
                tmp_path,
                index,
                game,
                "failed" if expected["open"] else "passed",
                expected=expected,
                observed={"open": False},
            )
        )
    output, code = oracle_matrix(saved(tmp_path, data))
    assert (output["state"], code) == ("weakened_oracle", 1)


def reduction():
    return {
        "schema": "verification_change_trials@1",
        "broken_inputs": snapshot("a", "b", "c"),
        "repaired_inputs": snapshot("A", "B", "C"),
        "check": {"kind": "scenario", "id": "doors"},
        "trials": [],
    }


def add_trial(root, data, indices, state):
    groups = [f"Assets/{i}.cs" for i in indices]
    inputs = dict(data["broken_inputs"])
    for group in groups:
        for path in (group, group + ".meta"):
            inputs[path] = data["repaired_inputs"][path]
    row = archive(root, len(data["trials"]) + 1, inputs, state)
    data["trials"].append({"groups": groups, **row})


def test_minimization_requires_measured_singletons_for_interacting_repair(tmp_path):
    data = reduction()
    add_trial(tmp_path, data, [], "failed")
    add_trial(tmp_path, data, [0, 1, 2], "passed")
    add_trial(tmp_path, data, [1, 2], "passed")
    output, code = minimize_changes(saved(tmp_path, data))
    assert code == 2 and output["state"] == "needs_trials"
    assert [r["groups"] for r in output["next_trials"]] == [
        ["Assets/0.cs"],
        ["Assets/1.cs"],
        ["Assets/2.cs"],
    ]
    for group in range(3):
        add_trial(tmp_path, data, [group], "failed")
    output, code = minimize_changes(saved(tmp_path, data))
    assert code == 0 and output["state"] == "minimum_cardinality"
    assert output["smallest_observed_repair"] == ["Assets/1.cs", "Assets/2.cs"]


@pytest.mark.parametrize("mutation", ["different_failure", "wrong_source", "reused", "unstable"])
def test_mixed_subsets_cannot_prove_an_unmatched_repair(tmp_path, mutation):
    data = reduction()
    for group, state in (([], "failed"), ([0, 1, 2], "passed"), ([0], "failed")):
        add_trial(tmp_path, data, group, state)
    row = data["trials"][-1]
    if mutation == "different_failure":
        path = tmp_path / row["native"]
        native = json.loads(path.read_text())
        native["failure"]["code"] = "other"
        path.write_text(json.dumps(native))
    elif mutation == "wrong_source":
        row["groups"] = ["Assets/1.cs"]
    elif mutation == "reused":
        data["trials"].append(dict(row))
    else:
        add_trial(tmp_path, data, [0], "passed")
    output, code = minimize_changes(saved(tmp_path, data))
    assert code == 2 and output["state"] in ("different_failure", "incomplete")


def test_export_copies_changed_metadata_with_asset_and_records_deletions(tmp_path):
    data = reduction()
    before, after = tmp_path / "before", tmp_path / "after"
    removed = "Assets/1.cs"
    data["repaired_inputs"] = {
        p: v for p, v in data["repaired_inputs"].items() if not p.startswith(removed)
    }
    data["repaired_inputs"]["Assets/0.cs.meta"] = digest("newmeta")
    for root, texts, inputs in (
        (before, "abc", data["broken_inputs"]),
        (after, "ABC", data["repaired_inputs"]),
    ):
        (root / "Assets").mkdir(parents=True)
        for path in inputs:
            asset = path.removesuffix(".meta")
            text = "newmeta" if root == after and path == "Assets/0.cs.meta" else asset
            (root / path).write_text(text if path.endswith(".meta") else texts[int(path[7])])
    path = saved(tmp_path, data)
    bundle = tmp_path / "overlay.zip"
    export_subset(path, ["Assets/0.cs", removed], before, after, bundle)
    with ZipFile(bundle) as archive_file:
        info = json.loads(archive_file.read("overlay.json"))
        assert info["absent_paths"] == [removed, removed + ".meta"]
        assert archive_file.read("Assets/0.cs.meta") == b"newmeta"
        assert archive_file.read("Assets/2.cs") == b"c"
    with pytest.raises(FileExistsError):
        export_subset(path, [], before, after, bundle)
    del data["broken_inputs"]["Assets/0.cs.meta"]
    with pytest.raises(ValueError, match="both the changed asset"):
        minimize_changes(saved(tmp_path, data))


def test_large_change_reports_only_witnessed_one_minimality(tmp_path):
    data = reduction()
    data["broken_inputs"] = snapshot(*"abcdefghi")
    data["repaired_inputs"] = snapshot(*"ABCDEFGHI")
    for indices, state in (
        ([], "failed"),
        (list(range(9)), "passed"),
        ([3, 6], "passed"),
        ([3], "failed"),
        ([6], "failed"),
    ):
        add_trial(tmp_path, data, indices, state)
    result, code = minimize_changes(saved(tmp_path, data))
    assert (result["state"], code) == ("one_minimal", 0)
    assert result["smallest_observed_repair"] == ["Assets/3.cs", "Assets/6.cs"]


def test_same_game_with_external_oracle_requires_same_admitted_payload(tmp_path):
    data = matrix(tmp_path, ["failed", "failed", "passed", "passed"])
    trial = data["trials"][1]
    candidate_path = tmp_path / trial["candidate"]
    candidate = json.loads(candidate_path.read_bytes())
    candidate["payload_hash"] = digest("unrelated payload")
    candidate["build_id"] = digest("another build")[:32]
    content = json.dumps(candidate).encode()
    candidate_path.write_bytes(content)
    report_path, native_path = (tmp_path / trial[key] for key in ("verification", "native"))
    report = json.loads(report_path.read_bytes())
    native = json.loads(native_path.read_bytes())
    for info in (report["data"]["stages"][0]["evidence"], native["reproduction"]):
        info.update(build_id=candidate["build_id"], payload_hash=candidate["payload_hash"])
    report["data"]["comparison"]["manifest_sha256"] = sha256(content).hexdigest()
    report_path.write_text(json.dumps(report))
    native_path.write_text(json.dumps(native))
    result, code = oracle_matrix(saved(tmp_path, data))
    assert (result["state"], code) == ("incomplete", 2)
    assert "same Player payload" in result["cells"][1]["reason"]


@pytest.mark.parametrize("analyze", [oracle_matrix, minimize_changes])
@pytest.mark.parametrize("version", [1, 2])
def test_one_large_candidate_root_in_mixed_trial_archive(tmp_path, analyze, version):
    if analyze is oracle_matrix:
        data = matrix(tmp_path, ["failed", "failed", "passed", "passed"])
        complete = "repair_supported"
    else:
        data = reduction()
        add_trial(tmp_path, data, [], "failed")
        add_trial(tmp_path, data, [0, 1, 2], "passed")
        add_trial(tmp_path, data, [1], "passed")
        complete = "minimum_cardinality"
    trial = data["trials"][-1]
    candidate_path = tmp_path / trial["candidate"]
    candidate = json.loads(candidate_path.read_bytes())
    if version == 2:
        ref, chunk = encode_input_chunk(candidate.pop("inputs"))
        candidate.update(schema_version=2, input_chunks=[ref.model_dump()])
        chunks = tmp_path / "input-chunks"
        chunks.mkdir()
        (chunks / f"{ref.sha256}.json").write_bytes(chunk)
    # Change only the root byte length, preserving the actual inventory and chunk.
    content = json.dumps(candidate).encode().ljust(CHUNK_BYTES + 1, b" ")
    candidate_path.write_bytes(content)
    report_path = tmp_path / trial["verification"]
    report = json.loads(report_path.read_bytes())
    report["data"]["comparison"]["manifest_sha256"] = sha256(content).hexdigest()
    report_path.write_text(json.dumps(report))
    result, code = analyze(saved(tmp_path, data))
    if version == 2:
        assert (result["state"], code) == (complete, 0)
    else:
        assert (result["state"], code) == ("incomplete", 2)
        rows = result["cells"] if analyze is oracle_matrix else result["trials"]
        assert rows[-1]["reason"] == "candidate_manifest_size_limit"
