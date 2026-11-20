"""Chronological evaluation with synthetic captures and a real missing-control inventory."""

import json
from hashlib import sha256
from pathlib import Path

import pytest
from services.verification.git_draft_artifacts import pack_inputs
from services.verification.input_chunks import CHUNK_PATHS, source_revision
from tests.test_verification_test_budgets import (
    ASSET,
    INPUT,
    PRIMARY,
    SCENE,
    SUITE,
    Case,
    captured_xml,
    offline,
)

HEAD = "a" * 40
BASE = "b" * 40
EARLY = "2026-10-24T12:00:00Z"
PLAN = "2026-10-25T12:00:00Z"
LATE = "2026-10-26T12:00:00Z"
ENTRY = "Assets/Checkout.unity"


def put(folder, name, data):
    path = folder / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def features(folder):
    draft = {
        "kind": "git_candidate_draft",
        "selection": "committed",
        "candidate_commit": HEAD,
        "baseline_commit": BASE,
        "schema_version": 1,
        "candidate": {
            "source_revision": source_revision({ASSET: "a" * 64}),
            "inputs": {ASSET: "a" * 64},
            "assets": {},
        },
        "changed_paths": [{"path": ASSET + ".meta"}, {"path": "Assets/New.cs"}],
    }
    mappings = {
        "mappings": [{"path": ASSET, "tests": [PRIMARY]}, {"path": ENTRY, "tests": [SCENE]}]
    }
    closure = {
        "kind": "verification_dependency_closure",
        "candidate_commit": HEAD,
        "baseline_commit": BASE,
        "candidate_source_revision": draft["candidate"]["source_revision"],
        "root": ENTRY,
        "traversed_edges": [
            {
                "source_path": ENTRY,
                "path": ASSET,
                "edge_id": "edge-1",
                "via": None,
                "depth": 1,
                "kind": "unity_dependency",
                "provider": "unity",
            }
        ],
        "unresolved": [],
    }
    return tuple(
        put(folder, name + ".json", data)
        for name, data in (("draft", draft), ("mappings", mappings), ("closure", closure))
    )


def receipt(path, commit=HEAD, at=EARLY):
    return {
        "path": path.name,
        "commit": commit,
        "available_at": at,
        "sha256": sha256(path.read_bytes()).hexdigest(),
    }


def replay_fixture(folder):
    draft, mappings, closure = features(folder)
    past = captured_xml(folder, 1, {name: Case(seconds=10) for name in SUITE}, seconds=40)
    full_cases = {name: Case("Failed" if name == PRIMARY else "Passed") for name in SUITE}
    full = captured_xml(folder, 2, full_cases, seconds=40)
    auto = captured_xml(folder, 3, {PRIMARY: full_cases[PRIMARY]}, seconds=12)
    manual = captured_xml(folder, 4, {INPUT: full_cases[INPUT]}, seconds=8)
    ranking = put(
        folder,
        "manual.json",
        {
            "schema": "verification_test_ranking@1",
            "tests": [{"name": INPUT, "reason": "Operator input smoke"}],
        },
    )
    return {
        "schema": "verification_pr_replay@1",
        "cohort_kind": "synthetic_fixture",
        "budget_ms": 10000,
        "prs": [
            {
                "pr": "fixture-1",
                "commit": HEAD,
                "plan_at": PLAN,
                "draft": receipt(draft),
                "mappings": receipt(mappings),
                "closures": [receipt(closure)],
                "history": [receipt(past, BASE)],
                "manual_ranking": receipt(ranking),
                "full": receipt(full, at=LATE),
                "automatic": receipt(auto, at=LATE),
                "manual": receipt(manual, at=LATE),
            }
        ],
    }


def test_rank_uses_scene_mapping_preserves_unmapped_and_feeds_existing_budget(tmp_path):
    draft, mappings, closure = features(tmp_path)
    history = captured_xml(
        tmp_path,
        1,
        {name: Case("Failed" if name == INPUT else "Passed", seconds=10) for name in SUITE},
        seconds=40,
    )
    ranking = tmp_path / "rank.json"
    code, result = offline(
        "budget-rank",
        "--draft",
        draft,
        "--mappings",
        mappings,
        "--closure",
        closure,
        "--history",
        history,
        "--output",
        ranking,
    )
    assert code == 2 and result["unmapped_paths"] == ["Assets/New.cs"]
    assert [r["name"] for r in result["tests"][:3]] == [PRIMARY, SCENE, INPUT]
    assert result["tests"][1]["mappings"] == [
        {"changed": ASSET + ".meta", "mapping": ENTRY, "edge": "edge-1"}
    ]
    assert result["tests"][2]["prior_failed_runs"]
    _, plan = offline(
        "budget-plan", "--ranking", ranking, "--history", history, "--budget-ms", 10000
    )
    assert plan["test_names"] == [PRIMARY]


def test_paired_replay_shares_truth_and_reports_actual_overrun_and_miss(tmp_path):
    code, report = offline("budget-replay", put(tmp_path, "history.json", replay_fixture(tmp_path)))
    assert code == 0 and report["paired_prs"] == report["total_prs"] == 1
    assert report["summary"]["automatic"] == {
        "full_failures": 1,
        "missed_failures": 0,
        "timed_prs": 1,
        "seconds_saved": 28,
        "budget_overruns": 1,
    }
    assert report["summary"]["manual"] == {
        "full_failures": 1,
        "missed_failures": 1,
        "timed_prs": 1,
        "seconds_saved": 32,
        "budget_overruns": 0,
    }


@pytest.mark.parametrize(
    "mode", ["later_manual", "later_history", "same_control", "tampered", "missing_run"]
)
def test_invalid_archive_is_outside_paired_denominator(tmp_path, mode):
    manifest = replay_fixture(tmp_path)
    pr = manifest["prs"][0]
    if mode == "later_manual":
        pr["manual_ranking"]["available_at"] = LATE
    elif mode == "later_history":
        pr["history"][0]["available_at"] = LATE
    elif mode == "same_control":
        pr["manual"] = pr["automatic"]
    elif mode == "tampered":
        pr["full"]["sha256"] = "0" * 64
    else:
        del pr["automatic"]
    code, report = offline("budget-replay", put(tmp_path, "replay.json", manifest))
    assert code == 2 and report["paired_prs"] == 0 and report["unpaired_prs"] == 1
    assert report["summary"]["automatic"]["missed_failures"] is None


def test_historical_inventory_exposes_missing_denominators():
    inventory = (
        Path(__file__).resolve().parents[2] / "docs/development/verification-pr-inventory.json"
    )
    code, report = offline("budget-replay", inventory)
    assert code == 2 and report["paired_prs"] == 0 and report["total_prs"] == 3
    assert all("full" in row["missing"] for row in report["prs"])


def test_replay_excludes_outcome_from_later_pr_relabelled_as_history(tmp_path):
    manifest = replay_fixture(tmp_path)
    first = manifest["prs"][0]
    # An archived full outcome with the exact bytes claimed as earlier timing.
    manifest["prs"].append(
        {
            "pr": "fixture-2",
            "commit": "c" * 40,
            "archived_at": LATE,
            "full": {**first["history"][0], "available_at": LATE, "commit": "c" * 40},
        }
    )
    code, report = offline("budget-replay", put(tmp_path, "replay.json", manifest))
    assert code == 2 and report["paired_prs"] == 0
    assert "retained outcome" in report["prs"][0]["reason"]


@pytest.mark.parametrize("borrowed_field", ["history", "full"])
def test_reexported_execution_in_partial_later_pr_excludes_both_rows(tmp_path, borrowed_field):
    manifest = replay_fixture(tmp_path)
    earlier = manifest["prs"][0]
    borrowed = earlier["history"][0] if borrowed_field == "history" else earlier["full"]
    later_file = tmp_path / "later-full.xml"
    later_file.write_bytes((tmp_path / borrowed["path"]).read_bytes() + b"\n")
    later = receipt(later_file, commit="c" * 40, at=LATE)
    assert borrowed["sha256"] != later["sha256"]
    manifest["prs"].append(
        {"pr": "fixture-2", "commit": "c" * 40, "archived_at": LATE, "full": later}
    )
    code, report = offline("budget-replay", put(tmp_path, "replay.json", manifest))
    assert code == 2 and report["paired_prs"] == 0 and report["unpaired_prs"] == 2
    assert all("Conflicting execution" in row["reason"] for row in report["prs"])
    assert "manual_ranking" in report["prs"][1]["missing"]


@pytest.mark.parametrize("mode", ["match", "different_hash", "missing_chunk"])
def test_replay_verifies_chunked_candidate_bytes_against_full_capture(tmp_path, mode):
    manifest = replay_fixture(tmp_path)
    pr = manifest["prs"][0]
    draft = json.loads((tmp_path / pr["draft"]["path"]).read_bytes())
    inputs = {f"Assets/Input-{i}.asset": "1" * 64 for i in range(CHUNK_PATHS)}
    inputs[ASSET] = ("b" if mode == "different_hash" else "a") * 64
    directory = tmp_path / "input-chunks"
    directory.mkdir()
    refs = []
    for ref, content in pack_inputs(inputs):
        if mode != "missing_chunk":
            (directory / f"{ref.sha256}.json").write_bytes(content)
        refs.append(ref.model_dump())
    assert len(refs) > 1
    draft.update(
        schema_version=2,
        candidate={"input_chunks": refs, "assets": {}, "source_revision": source_revision(inputs)},
    )
    pr["draft"] = receipt(put(tmp_path, "large.json", draft))
    pr["closures"] = []
    code, report = offline("budget-replay", put(tmp_path, "replay.json", manifest))
    if mode == "match":
        assert code == 0 and report["paired_prs"] == 1
    else:
        assert code == 2 and report["paired_prs"] == 0
        assert (
            "different bytes" if mode == "different_hash" else "inventory incomplete"
        ) in report["prs"][0]["reason"]
