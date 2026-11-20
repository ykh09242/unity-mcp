"""Holdout admission, trace accounting, and fresh teammate replay controls."""

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

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


def plan_and_receipts():
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
            for arm in study.paired.ARMS
        ],
    }
    plan = {
        "projects": [
            {"product_guid": "2" * 32, "revision": "3" * 40, "unity_version": "6000.0.69f1"}
        ],
        "controls": {"model": "model", "host": "host", "effort": "high"},
        "permissions": {"mcp-only": "4" * 64},
        "catalogs": [
            {"mcp-only": dict.fromkeys(study.paired.ARMS, study.json_digest(["test_tool"]))}
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
            "catalog_digest": study.json_digest(["test_tool"]),
            "observer_digest": "1" * 64,
            "prompt_digest": study.checksum(case["prompt"].encode()),
            "handoff_digest": study.checksum(handoff.encode()),
            "operator_id": phase,
            "session_id": key + "-session",
            "fixture_run_id": key + "-fixture",
            "execution": "native",
            "coverage": "complete",
            "elapsed_seconds": 3,
            "actor_claimed_success": True,
        }
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
        if arm == "official":
            host.update(provider_command="unity mcp", connected_pipeline="p")
            native.update(
                unity_cli="cli", pipeline_package="p", provider_session_id=host["session_id"]
            )
        capture = json.dumps(native)
        calls = [
            {"kind": "mcp", "dispatch": 0, "return": 2, "reply": {"hit": "Blocker"}},
            {"kind": "mcp", "dispatch": 1, "return": None},
        ]
        review = {
            "observer_id": "adjudicator",
            "coverage": "complete",
            "trial_id": key,
            "artifact_digest": study.checksum(capture.encode()),
            "trace_digest": study.json_digest(calls),
            "valid_observations": [
                {"return": 2, "reply_digest": study.json_digest(calls[0]["reply"])}
            ],
        }
        evidence[key] = {
            "catalog": ["test_tool"],
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
