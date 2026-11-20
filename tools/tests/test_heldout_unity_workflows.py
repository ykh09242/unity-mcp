"""Holdout admission, trace accounting, and fresh teammate replay controls."""

from copy import deepcopy
from importlib.metadata import PathDistribution
import json
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.experiments import heldout_unity_workflows as study


def git(directory, *args):
    subprocess.run(["git", "-C", str(directory), *args], check=True, capture_output=True)


def project(path, guid):
    (path / "ProjectSettings").mkdir(parents=True)
    (path / "Assets").mkdir()
    (path / "Assets" / "sample.txt").write_text(guid)
    (path / "ProjectSettings" / "ProjectSettings.asset").write_text(f"  productGUID: {guid}\n")
    (path / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 6000.0.69f1\n")
    git(path, "init")
    git(path, "add", ".")
    git(
        path,
        "-c",
        "user.name=fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-m",
        guid,
    )


def test_prepare_excludes_related_copies_and_unfrozen_projects(tmp_path, monkeypatch):
    paths = [tmp_path / name for name in ("one", "two", "training")]
    for i, path in enumerate(paths):
        project(path, f"{i + 1:032x}")
    for name in ("UnityMCPTests", "AssetStoreUploads"):
        assets = paths[2] / "TestProjects" / name / "Assets"
        assets.mkdir(parents=True)
        (assets / "fixture.txt").write_text(name)
    git(paths[2], "add", ".")
    git(
        paths[2],
        "-c",
        "user.name=fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-m",
        "training",
    )
    monkeypatch.setattr(study, "ROOT", paths[2])
    for name in ("observer", "policy"):
        (tmp_path / name).write_text(name)
    (tmp_path / "catalog.json").write_text('["test_tool"]')
    spec = {
        "training": {"inventory_complete": True, "projects": [], "used_evidence": []},
        "model": "test",
        "host": "host",
        "effort": "high",
        "products": {
            "fork": {"product_identity": "fork@sha"},
            "official": {
                "product_identity": "official@pin",
                "unity_cli": "v",
                "pipeline_package": "p",
            },
        },
        "permissions": {mode: "policy" for mode in study.paired.MODES},
        "projects": [],
    }
    for path in paths[:2]:
        snapshot = study.project_snapshot(path)
        fixtures = {}
        for task in study.paired.TASKS:
            name = f"{path.name}-{task}.json"
            state = {
                "project_guid": snapshot["product_guid"],
                "project_revision": snapshot["revision"],
                "unity_version": snapshot["unity_version"],
                "task": task,
                "facts": {"target": "Test"},
            }
            (tmp_path / name).write_text(json.dumps(state))
            fixtures[task] = {
                "initial_state": name,
                "observer": "observer",
                "context": {"en": "Target", "ko": "대상"},
            }
        spec["projects"].append(
            {
                "path": path.name,
                "excluded_from_tuning": True,
                "fixtures": fixtures,
                "catalogs": {
                    mode: dict.fromkeys(study.paired.ARMS, "catalog.json")
                    for mode in study.paired.MODES
                },
            }
        )
    plan = study.prepare(spec, tmp_path)
    assert len(plan["cases"]) == 24
    assert len({slot[0] for slot in study.allocations(plan)}) == 96
    assert {row["language"] for row in plan["cases"]} == {"en", "ko"}
    with ZipFile(tmp_path / "reader.whl", "w") as wheel:
        wheel.writestr("pkg.dist-info/METADATA", "Name: ykh09242-unity-mcp-evidence\n")
        wheel.writestr(
            "pkg.dist-info/entry_points.txt",
            "[console_scripts]\nunity-mcp-evidence = reader:main\n",
        )
        wheel.writestr("reader.py", "def main(): pass\n")
    spec.update(design="three-arm", reader_tool="read_offline_evidence", reader_wheel="reader.whl")
    spec["operators"] = {phase: [f"{phase}-{i}" for i in range(12)] for phase in study.PHASES}
    (tmp_path / "reader.json").write_text('["test_tool", "read_offline_evidence"]')
    for entry in spec["projects"]:
        for fixture in entry["fixtures"].values():
            fixture["raw_inputs"] = {"start": fixture["initial_state"]}
        for mode in study.paired.MODES:
            entry["catalogs"][mode]["official-reader"] = (
                "reader.json" if mode == "mcp-only" else "catalog.json"
            )
    plan = study.prepare(spec, tmp_path)
    assert len({slot[0] for slot in study.allocations(plan)}) == 144
    assert all(
        {run["arm"] for run in case["runs"]} == set(study.three_arm.ARMS) for case in plan["cases"]
    )
    assert plan["products"]["official-reader"] == plan["products"]["official"]
    study.three_arm.validate_operator_schedule(plan)
    for phase, operators in plan["operators"].items():
        for operator in operators:
            run_arms = [
                run["arm"]
                for case in plan["cases"]
                for run in case["runs"]
                if run[phase + "_operator"] == operator
            ]
            assert all(run_arms.count(arm) == 2 for arm in study.three_arm.ARMS)
    packet = spec["projects"][0]["fixtures"]["asset-move"]["raw_inputs"]
    renamed = tmp_path / "neutral-input.bin"
    packet["guide"] = renamed.name
    spec["training"]["used_evidence"].append(study.checksum(b"tuning capture").upper())
    for forbidden in ((tmp_path / "observer").read_bytes(), b"tuning capture"):
        renamed.write_bytes(forbidden)
        with pytest.raises(ValueError, match="observer or tuning"):
            study.prepare(spec, tmp_path)
    del packet["guide"]
    spec["projects"][0]["catalogs"]["mcp-only"]["official-reader"] = "catalog.json"
    with pytest.raises(ValueError, match="same provider tools"):
        study.prepare(spec, tmp_path)
    spec["training"]["projects"].append(study.project_snapshot(paths[1]))
    with pytest.raises(ValueError, match="overlaps"):
        study.prepare(spec, tmp_path)
    spec["training"]["projects"].clear()
    spec["projects"][1]["path"] = "one"
    with pytest.raises(ValueError, match="overlaps"):
        study.prepare(spec, tmp_path)
    (paths[0] / "Assets" / "new.txt").write_text("dirty")
    with pytest.raises(ValueError, match="clean"):
        study.prepare(spec, tmp_path)


def plan_and_receipts(*, three=False):
    case = {
        "project": 0,
        "task": "blocked-click",
        "language": "en",
        "mode": "mcp-only",
        "repetition": 1,
        "fixture_digest": "0" * 64,
        "observer_digest": "1" * 64,
        "prompt": "Investigate the target.",
        "runs": [
            {"id": arm + "-actor", "replay_id": arm + "-replay", "arm": arm}
            for arm in (study.three_arm.ARMS if three else study.paired.ARMS)
        ],
    }
    plan = {
        "projects": [
            {"product_guid": "2" * 32, "revision": "3" * 40, "unity_version": "6000.0.69f1"}
        ],
        "controls": {"model": "model", "host": "host", "effort": "high"},
        "permissions": {"mcp-only": "4" * 64},
        "catalogs": [
            {"mcp-only": dict.fromkeys(study.paired.ARMS, study.catalog_digest(["test_tool"]))}
        ],
        "products": {
            "fork": {"product_identity": "fork"},
            "official": {
                "product_identity": "official",
                "pipeline_package": "p",
                "unity_cli": "cli",
            },
        },
        "used_evidence": [],
        "cases": [case],
    }
    if three:
        plan["schema"] = 2
        plan["reader"] = {"wheel_sha256": "a" * 64, "mcp_tool": "reader"}
        plan["operators"] = {phase: [] for phase in study.PHASES}
        for run in case["runs"]:
            for phase in study.PHASES:
                operator = f"{phase}-{run['arm']}"
                run[phase + "_operator"] = operator
                plan["operators"][phase].append(operator)
        case["raw_inputs"] = {"raw": "b" * 64}
        plan["products"]["official-reader"] = dict(plan["products"]["official"])
        plan["catalogs"][0]["mcp-only"]["official-reader"] = study.catalog_digest(
            ["test_tool", "reader"]
        )
    evidence = {}
    for key, _, allocation, phase in study.allocations(plan):
        arm = allocation["arm"]
        handoff = "Use the pointer after repairing the blocker."
        host = {
            **plan["controls"],
            **plan["products"][arm],
            "trial_id": key,
            "project_revision": "3" * 40,
            "product_guid": "2" * 32,
            "unity_version": "6000.0.69f1",
            "fixture_digest": "0" * 64,
            "permission_digest": "4" * 64,
            "catalog_digest": study.catalog_digest(["test_tool"]),
            "observer_digest": "1" * 64,
            "prompt_digest": study.checksum(case["prompt"].encode()),
            "handoff_digest": study.checksum(handoff.encode()),
            "operator_id": allocation[phase + "_operator"] if three else phase,
            "session_id": key + "-session",
            "fixture_run_id": key + "-fixture",
            "execution": "native",
            "coverage": "complete",
            "elapsed_seconds": 3,
            "actor_claimed_success": True,
        }
        catalog = ["test_tool"]
        if three:
            if arm == "official-reader":
                catalog.append("reader")
            host.update(
                raw_inputs=dict(case["raw_inputs"]),
                catalog_digest=study.catalog_digest(catalog),
                reader_probe={
                    "trial_id": key,
                    "session_id": host["session_id"],
                    "wheel_sha256": plan["reader"]["wheel_sha256"],
                }
                if arm == "official-reader"
                else None,
            )
        native = {
            name: host[name]
            for name in (
                "trial_id",
                "fixture_run_id",
                "product_guid",
                "project_revision",
                "unity_version",
                "product_identity",
                "fixture_digest",
                "observer_digest",
            )
        }
        native.update(
            coverage="complete",
            capture_id=key + "-capture",
            duplicate_writes=0,
            facts={
                "input_mode": "pointer",
                "blocked_target_first": False,
                "repaired_target_first": True,
                "blocked_transitions": 0,
                "repaired_transitions": int(arm == "fork"),
            },
        )
        if arm in ("official", "official-reader"):
            host.update(provider_command="unity mcp", connected_pipeline="p")
            native.update(
                unity_cli="cli", pipeline_package="p", provider_session_id=host["session_id"]
            )
        capture = json.dumps(native)
        calls = [
            {"kind": "mcp", "dispatch": 0, "return": 2, "reply": {"hit": "Blocker"}},
            {"kind": "mcp", "dispatch": 1, "return": None},
        ]
        if three:
            calls[0].update(dispatch_seconds=0.1, return_seconds=1.5 if arm == "official" else 1)
            calls[1]["dispatch_seconds"] = 0.5
        review = {
            "observer_id": f"adjudicator-{arm}" if three else "adjudicator",
            "coverage": "complete",
            "trial_id": key,
            "artifact_digest": study.checksum(capture.encode()),
            "trace_digest": study.json_digest(calls),
            "valid_observations": [
                {"return": 2, "reply_digest": study.json_digest(calls[0]["reply"])}
            ],
        }
        evidence[key] = {
            "catalog": catalog,
            "host": host,
            "native_capture": capture,
            "handoff": handoff,
            "calls": calls,
            "review": review,
        }
    return plan, evidence


def totals(plan, evidence):
    return study.report(plan, evidence)["summary"]["mcp-only"]


def test_only_matched_native_runs_score_and_replay_is_separate():
    plan, evidence = plan_and_receipts()
    assert totals(plan, {}) == {"planned": 1, "scored": 0, "arms": None}
    assert totals(plan, {"fork-actor": evidence["fork-actor"]})["arms"] is None
    arms = totals(plan, evidence)["arms"]
    assert arms["fork"]["calls_to_first_valid_observation_median"] == 2
    assert arms["official"]["false_passes"] == 1
    assert arms["fork"]["teammate_replay_success_rate"] == 1
    assert arms["official"]["teammate_replay_success_rate"] == 0
    del evidence["official-replay"]
    arms = totals(plan, evidence)["arms"]
    assert arms["official"]["success_rate"] == 0
    assert arms["fork"]["teammate_replay_success_rate"] is None
    evidence["fork-actor"]["review"]["valid_observations"] = []
    assert totals(plan, evidence)["arms"]["fork"]["no_valid_observation"] == 1


def test_replay_requires_fresh_fixture_distinct_teammate_and_original_handoff():
    plan, evidence = plan_and_receipts()
    for field, value in (("operator_id", "actor"), ("handoff_digest", study.checksum(b"changed"))):
        changed = deepcopy(evidence)
        changed["fork-replay"]["host"][field] = value
        if field == "handoff_digest":
            changed["fork-replay"]["handoff"] = "changed"
        assert totals(plan, changed)["arms"]["fork"]["teammate_replay_samples"] == 0
    evidence["fork-replay"]["host"]["session_id"] = evidence["fork-actor"]["host"]["session_id"]
    assert totals(plan, evidence)["scored"] == 0


def test_rejects_tuning_receipts_unbound_trace_or_official_native_capture():
    plan, evidence = plan_and_receipts()
    plan["used_evidence"] = [evidence["fork-actor"]["review"]["artifact_digest"]]
    assert totals(plan, evidence)["arms"] is None
    evidence["fork-actor"]["calls"][0]["reply"] = {"hit": "Target"}
    with pytest.raises(ValueError, match="Witness/trace"):
        totals(plan, evidence)
    plan, evidence = plan_and_receipts()
    official = evidence["official-actor"]
    native = json.loads(official["native_capture"])
    native["pipeline_package"] = "different"
    official["native_capture"] = json.dumps(native)
    official["review"]["artifact_digest"] = study.checksum(official["native_capture"].encode())
    with pytest.raises(ValueError, match="provider binding"):
        totals(plan, evidence)


def test_changed_provider_tool_selection_is_inadmissible():
    plan, evidence = plan_and_receipts()
    evidence["official-actor"]["catalog"].append("unexpected_tool")
    with pytest.raises(ValueError, match="catalog differs"):
        totals(plan, evidence)


def test_reader_probe_checks_the_actual_installed_source(tmp_path, monkeypatch):
    installed = tmp_path / "site"
    metadata = installed / "pkg.dist-info"
    metadata.mkdir(parents=True)
    files = {
        "pkg.dist-info/METADATA": "Name: ykh09242-unity-mcp-evidence\n",
        "pkg.dist-info/entry_points.txt": "[console_scripts]\nunity-mcp-evidence = reader:main\n",
        "reader.py": "def main(): pass\n",
    }
    (metadata / "RECORD").write_text("".join(name + ",,\n" for name in files))
    path = tmp_path / "reader.whl"
    with ZipFile(path, "w") as wheel:
        for name, source in files.items():
            wheel.writestr(name, source)
            (installed / name).write_bytes(source.encode())
    monkeypatch.setattr(study.three_arm, "distribution", lambda name: PathDistribution(metadata))
    probe = study.three_arm.probe_reader(path, "trial", "session")
    assert probe["wheel_sha256"] == study.checksum(path.read_bytes())
    (installed / "reader.py").write_text("different")
    with pytest.raises(ValueError, match="Installed reader bytes"):
        study.three_arm.probe_reader(path, "trial", "session")


def test_three_arms_require_matched_actors_timing_and_separate_replays():
    plan, evidence = plan_and_receipts(three=True)
    report = study.report(plan, evidence)
    assert report["complete"]
    assert report["overall"] == report["by_project"][0]
    arms = report["overall"]["arms"]
    assert arms["official"]["false_successes"] == 1
    assert arms["fork"]["teammate_success_rate"] == 1
    assert arms["official-reader"]["paired_seconds_minus_official_median"] == -0.5
    del evidence["official-reader-replay"]
    evidence["official-reader-actor"]["review"]["valid_observations"] = []
    summary = study.report(plan, evidence)["overall"]
    assert summary["matched_actors"] == summary["planned_triplets"] == 1
    assert summary["matched_first_observations"] == summary["matched_replays"] == 0
    assert all(
        s["first_seconds_median"] is None and s["teammate_success_rate"] is None
        for s in summary["arms"].values()
    )
    del evidence["official-reader-actor"]
    summary = study.report(plan, evidence)["overall"]
    assert summary["arms"] is None
    assert summary["unmatched_actors"] == 1


@pytest.mark.parametrize("change", ["raw", "clock", "provider", "reuse", "reader"])
def test_third_arm_cannot_borrow_provider_data_or_observation_time(change):
    plan, evidence = plan_and_receipts(three=True)
    bundle = evidence["official-reader-actor"]
    if change == "reuse":
        bundle["host"]["fixture_run_id"] = evidence["official-actor"]["host"]["fixture_run_id"]
        witness = json.loads(bundle["native_capture"])
        witness["fixture_run_id"] = bundle["host"]["fixture_run_id"]
        bundle["native_capture"] = json.dumps(witness)
        bundle["review"]["artifact_digest"] = study.checksum(bundle["native_capture"].encode())
        assert study.report(plan, evidence)["overall"]["arms"] is None
        return
    if change == "raw":
        bundle["host"]["raw_inputs"].clear()
    elif change == "clock":
        bundle["calls"][0]["return_seconds"] = 0.2
        bundle["review"]["trace_digest"] = study.json_digest(bundle["calls"])
    elif change == "provider":
        bundle["host"]["provider_command"] = "fork"
    else:
        bundle["host"]["reader_probe"] = None
    with pytest.raises(ValueError):
        study.report(plan, evidence)


@pytest.mark.parametrize("source", ["observer", "training"])
def test_admission_blocks_private_bytes_even_in_a_pinned_packet(source):
    plan, evidence = plan_and_receipts(three=True)
    case = plan["cases"][0]
    hidden = case["observer_digest"] if source == "observer" else study.checksum(b"training")
    if source == "training":
        plan["used_evidence"].append(hidden)
    case["raw_inputs"]["renamed"] = hidden
    bundle = evidence["official-reader-actor"]
    bundle["host"]["raw_inputs"] = dict(case["raw_inputs"])
    with pytest.raises(ValueError, match="observer or tuning"):
        study.report(plan, {"official-reader-actor": bundle})


@pytest.mark.parametrize("sibling", ["fork-actor", "fork-replay"])
def test_three_arm_replay_cannot_be_a_sibling_participant(sibling):
    plan, evidence = plan_and_receipts(three=True)
    evidence["official-replay"]["host"]["operator_id"] = evidence[sibling]["host"]["operator_id"]
    with pytest.raises(ValueError, match="counterbalanced allocation"):
        study.report(plan, evidence)


@pytest.mark.parametrize("participant", ["official-actor", "fork-replay"])
def test_three_arm_adjudicator_is_independent_of_every_arm(participant):
    plan, evidence = plan_and_receipts(three=True)
    evidence["official-replay"]["review"]["observer_id"] = evidence[participant]["host"][
        "operator_id"
    ]
    with pytest.raises(ValueError, match="Adjudicator"):
        study.report(plan, evidence)


def test_three_arm_schedule_rejects_same_task_reuse_and_unbalanced_exposure():
    plan, _ = plan_and_receipts(three=True)
    another = deepcopy(plan["cases"][0])
    another["language"] = "ko"
    plan["cases"].append(another)
    with pytest.raises(ValueError, match="repeat a project/task"):
        study.report(plan, {})
    another["task"] = "asset-move"
    with pytest.raises(ValueError, match="counterbalanced across arms"):
        study.report(plan, {})


@pytest.mark.parametrize("sibling", ["fork-actor", "fork-replay"])
def test_cross_arm_adjudicator_invalidates_a_matched_case(sibling):
    plan, evidence = plan_and_receipts(three=True)
    original = evidence["official-replay"]
    original["review"]["observer_id"] = evidence[sibling]["review"]["observer_id"]
    with pytest.raises(ValueError, match="multiple arms of the same matched case"):
        study.report(plan, {"official-replay": original, sibling: evidence[sibling]})
