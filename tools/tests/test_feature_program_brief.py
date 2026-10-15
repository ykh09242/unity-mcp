"""Bounded briefs use captured canonical facts and never grant execution authority."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
import subprocess
import sys

import pytest

from tools.tests.test_feature_program import fp
from tools.tests.test_feature_program_phases import gate  # noqa: F401


def inventory(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }


def forbidden(*args, **kwargs):
    raise AssertionError("Brief reached a write or Git capability")


def test_program_and_task_brief_cli_share_snapshot_facts_without_effects(gate, capsys, monkeypatch):
    before = inventory(gate.program.repo)
    monkeypatch.setattr(fp, "git", forbidden)
    monkeypatch.setattr(fp.Program, "lock", forbidden)
    monkeypatch.setattr(fp.Program, "save", forbidden)
    monkeypatch.setattr(fp.Program, "render", forbidden)
    capsys.readouterr()
    assert gate.fixture.run("brief") == 0
    program = json.loads(capsys.readouterr().out)
    snapshot = fp.read_snapshot(gate.program.repo, gate.program.directory)
    assert program == snapshot["brief"]
    assert len(fp.canonical(program)) <= 32768
    assert gate.fixture.run("brief", "--task", "S01") == 0
    task = json.loads(capsys.readouterr().out)
    assert task["task"]["id"] == "S01"
    assert task["task"]["readiness"] == snapshot["tasks"][0]["readiness"]
    assert len(fp.canonical(task)) <= 16384
    assert inventory(gate.program.repo) == before


def test_snapshot_planned_readiness_has_canonical_dependencies_and_unknown_authority(gate):
    snapshot = fp.read_snapshot(gate.program.repo, gate.program.directory)
    state = gate.program.load()
    expected = {item["id"]: item for item in gate.program.status(state)["candidates"]}
    for task in snapshot["tasks"]:
        readiness = task["readiness"]
        assert readiness["claim"]["status"] == "not_assessed"
        assert (
            readiness["unmet_dependencies"]["items"] == expected[task["id"]]["unmet_dependencies"]
        )
        assert readiness["peer_agreement"]["status"] == "unknown"


def test_unknown_selected_task_is_explicit_not_an_empty_success(gate, capsys):
    capsys.readouterr()
    assert gate.fixture.run("brief", "--task", "missing") == 1
    assert "Unknown task: missing" in capsys.readouterr().err


def test_canonical_collision_and_dependency_are_factors_not_claim_permission(gate):
    state = gate.program.load()
    state["tasks"][1]["candidate_paths"] = ["src/S01.py"]
    state["tasks"][1]["depends_on"] = ["S01"]
    gate.program.save(state)
    planned = fp.read_snapshot(gate.program.repo, gate.program.directory)
    assert planned["tasks"][1]["readiness"]["collisions"]["total"] == 0
    assert planned["tasks"][1]["readiness"]["unmet_dependencies"]["items"] == ["S01"]
    assert gate.fixture.claim() == 0
    assert (
        gate.fixture.run(
            "block",
            "S01",
            "--owner",
            "worker",
            "--reason",
            "Authored blocker",
            "--next-action",
            "Obtain missing input",
        )
        == 0
    )
    snapshot = fp.read_snapshot(gate.program.repo, gate.program.directory)
    expected = gate.program.status(gate.program.load())["candidates"][0]
    readiness = snapshot["tasks"][1]["readiness"]
    assert readiness["collisions"]["total"] == len(expected["scope_conflicts"]) == 1
    conflict = readiness["collisions"]["items"][0]
    assert conflict["owner"] == "worker" and conflict["task_id"] == "S01"
    assert [item["reason"] for item in conflict["reasons"]["items"]] == expected["scope_conflicts"][
        0
    ]["reasons"]
    assert readiness["claim"]["status"] == "not_assessed"
    assert {item["kind"] for item in snapshot["brief"]["attention"]["items"]} >= {
        "blocked",
        "collision",
        "dependency",
        "revalidation",
        "objective",
    }


def test_legacy_absence_is_unknown_and_does_not_require_objective(gate):
    state = gate.program.load()
    state.pop("objective")
    state.pop("director")
    state.pop("communication_language", None)
    gate.program.save(state)
    result = fp.read_brief(gate.program.repo, gate.program.directory, "S01")
    assert result["objective"] is None and result["director"] is None
    assert result["communication_language"] is None
    assert result["task"]["recorded_acceptance"]["status"] == "unknown"
    assert result["task"]["readiness"]["peer_agreement"]["status"] == "unknown"


@pytest.mark.parametrize("length", [511, 512, 513])
@pytest.mark.parametrize("character", ["a", "한", "😀", "e\u0301", "\n", '"'])
def test_prose_bounds_preserve_original_and_count_codepoints(gate, length, character):
    state = gate.program.load()
    prose = (character * length)[:length]
    state["tasks"][0]["next_action"] = "x" + prose
    gate.program.save(state)
    result = fp.read_brief(gate.program.repo, gate.program.directory, "S01")
    task = result["task"]
    assert task["next_action"] == ("x" + prose)[:512]
    assert ("next_action" in task["truncated_fields"]) == (length + 1 > 512)
    assert gate.program.load()["tasks"][0]["next_action"] == "x" + prose
    assert task["reference"]["pointer"] == "/tasks/0"
    assert len(fp.canonical(result)) <= 16384


@pytest.mark.parametrize("count", [0, 1, 50, 51, 120])
def test_independent_collection_counts_and_severe_omissions(count):
    original = {"path": "backlog.json", "pointer": "", "revision": "a" * 64}
    rows = [
        {"kind": "integrity", "entity_id": str(index), "reason": "😀" * 512, "reference": original}
        for index in range(count)
    ]
    value = {
        "integrity": {"status": "stale", "warnings_total": count},
        "attention": fp.brief.collection(rows, original),
        "attention_totals_by_kind": {"integrity": count},
        "execution_authority": "not_assessed",
    }
    result = fp.brief.bounded(value, 32768)
    assert len(fp.canonical(result)) <= 32768
    counts = result["attention"]
    assert counts["total"] == count == counts["shown"] + counts["omitted"]
    assert counts["shown"] == len(counts["items"]) <= 50
    assert result["integrity"]["warnings_total"] == count
    assert result["attention_counts_by_kind"]["integrity"] == {
        key: counts[key] for key in ("total", "shown", "omitted")
    }
    assert value["attention"]["total"] == count


def test_encoded_exact_limit_and_essential_overflow():
    value = {"identity": "😀" * 20, "execution_authority": "not_assessed"}
    size = len(fp.canonical(value))
    assert fp.brief.bounded(value, size) == value
    with pytest.raises(fp.ProgramError, match="essential envelope"):
        fp.brief.bounded(value, size - 1)


@pytest.mark.parametrize("selected", [False, True])
def test_actual_subprocess_bytes_match_bounded_serialization(gate, selected):
    assert gate.fixture.claim() == 0
    for index in range(8):
        assert (
            gate.fixture.run(
                "note",
                "S01",
                "--owner",
                "worker",
                "--reason",
                str(index) + "😀" * 512,
                "--next-action",
                "Inspect original\n" + "한" * 512,
            )
            == 0
        )
    expected = fp.read_brief(gate.program.repo, gate.program.directory, "S01" if selected else None)
    before = inventory(gate.program.repo)
    process = subprocess.run(
        [
            sys.executable,
            "-B",
            str(Path(fp.__file__).resolve()),
            "--repo",
            str(gate.program.repo),
            "--program-dir",
            str(gate.program.directory),
            "brief",
            *(["--task", "S01"] if selected else []),
        ],
        capture_output=True,
        check=False,
    )
    assert process.returncode == 0, process.stderr.decode()
    assert process.stdout == fp.canonical(expected)
    assert len(process.stdout) <= (16384 if selected else 32768)
    assert b"\r\n" not in process.stdout
    assert inventory(gate.program.repo) == before


def test_authored_notes_remain_content_with_own_frozen_evidence(gate):
    contract = "Original contract locator interface/v1.md sha256=" + "b" * 64
    for identifier in ("S01", "S02"):
        owner = "worker-" + identifier
        assert gate.fixture.claim(identifier, owner=owner) == 0
        evidence = gate.fixture.evidence(identifier, label="peer-contract")
        assert (
            gate.fixture.run(
                "note",
                identifier,
                "--owner",
                owner,
                "--reason",
                "accepted: " + contract,
                "--next-action",
                "Inspect original counterpart record",
                "--evidence",
                evidence,
            )
            == 0
        )
        result = fp.read_brief(gate.program.repo, gate.program.directory, identifier)
        note = result["task"]["coordination_notes"]["items"][0]
        assert note["reason"] == "accepted: " + contract
        assert note["evidence"]["items"][0]["path"].startswith(f"evidence/{identifier}/attempt-1/")
        assert note["interpretation"] == "authored_context_only"
        assert result["task"]["readiness"]["peer_agreement"]["status"] == "unknown"


def test_review_source_drift_is_not_current_acceptance(gate):
    assert gate.fixture.claim() == 0
    gate.fixture.submit()
    (gate.program.repo / "src/S01.py").write_text("VALUE = 2\n", encoding="utf-8")
    before = inventory(gate.program.repo)
    result = fp.read_brief(gate.program.repo, gate.program.directory, "S01")
    assert result["task"]["readiness"]["submission"]["status"] == "stale"
    assert result["task"]["recorded_acceptance"]["status"] != "accepted"
    assert result["integrity"]["status"] == "stale"
    assert inventory(gate.program.repo) == before


@pytest.mark.parametrize("target", ["registry", "evidence", "source", "review_source"])
def test_changed_observation_after_projection_is_rejected(gate, monkeypatch, target):
    assert gate.fixture.claim() == 0
    gate.fixture.submit()
    paths = {
        "registry": gate.program.path("backlog.json"),
        "evidence": gate.program.path("evidence/S01/attempt-1/inspection.txt"),
        "source": gate.program.path("source.md"),
        "review_source": gate.program.repo / "src/S01.py",
    }
    original = fp.brief.project

    def mutate(*args, **kwargs):
        result = original(*args, **kwargs)
        paths[target].write_bytes(paths[target].read_bytes() + b"\n")
        return result

    monkeypatch.setattr(fp.brief, "project", mutate)
    with pytest.raises(fp.ProgramError, match="changed during snapshot"):
        fp.read_brief(gate.program.repo, gate.program.directory)


@pytest.mark.parametrize(
    "content",
    [b'{"pid": 123}', b"{", b"x" * (65536 + 1)],
    ids=["present", "partial-json", "oversized"],
)
def test_lock_observation_is_unknown_not_takeover_authority(gate, content):
    gate.program.path(".feature-program.lock").write_bytes(content)
    before = inventory(gate.program.repo)
    result = fp.read_brief(gate.program.repo, gate.program.directory)
    assert result["attention_totals_by_kind"]["recovery"] == 1
    assert result["execution_authority"] == "not_assessed"
    assert inventory(gate.program.repo) == before


def test_full_records_and_detached_projection_are_preserved(gate):
    state = gate.program.load()
    before = deepcopy(state)
    snapshot = fp.read_snapshot(gate.program.repo, gate.program.directory)
    projection = fp.brief.project(gate.program, state, snapshot, {})
    projection["tasks"]["S01"]["next_action"] = "local only"
    assert state == before
    assert snapshot["tasks"][0]["next_action"] == before["tasks"][0]["next_action"]


def test_brief_readiness_matches_original_collisions_and_refreshes_scopes(request):
    # Given captured ownership plus a planned collision spelled with a different case.
    captured_gate = request.getfixturevalue("gate")
    assert captured_gate.fixture.claim() == 0
    snapshot = fp.read_snapshot(captured_gate.program.repo, captured_gate.program.directory)
    state = captured_gate.program.load()
    state["tasks"][1]["candidate_paths"] = ["SRC/S01.py"]
    original = deepcopy(state)
    # When a brief projects this state, and a later projection sees a changed scope.
    first = fp.brief.project(captured_gate.program, state, snapshot, {})
    state["tasks"][1]["candidate_paths"] = ["src/separate.py"]
    second = fp.brief.project(captured_gate.program, state, snapshot, {})
    # Then original directional reasons survive and neither result is a cached prior read.
    expected = fp.Program.collisions(original["tasks"][1], original["tasks"][0])
    rows = first["tasks"]["S02"]["readiness"]["collisions"]["items"]
    assert [item["reason"] for item in rows[0]["reasons"]["items"]] == expected
    assert second["tasks"]["S02"]["readiness"]["collisions"]["total"] == 0
    assert state["tasks"][0] == original["tasks"][0]


def test_excluded_planned_members_do_not_get_a_revalidation_next_step(gate):
    from tools.tests.test_feature_program_planning import membership, reviewed_input

    gate.ready()
    name, _ = reviewed_input(gate, membership(gate, ["S01"]))
    assert gate.fixture.run("objective-membership", "--input", name) == 0
    result = fp.read_brief(gate.program.repo, gate.program.directory, "S02")
    readiness = result["task"]["readiness"]
    assert readiness["canonical_candidate"] is False
    assert readiness["required_revalidation"] == "not_applicable"
    assert all(row["kind"] != "revalidation" for row in result["attention"]["items"])


def test_question_saved_acknowledged_applied_and_historical_goal_are_distinct(gate):
    binding = {
        "source_head": "a" * 40,
        "scope": ["src/S01.py"],
        "source_digest": gate.program.source_digest(["src/S01.py"]),
    }
    receipt = {
        **binding,
        "id": "question-one",
        "version": 1,
        "actor_id": "director",
        "prompt": "Which scope?",
        "why": "Scope affects acceptance",
        "requested_decision": "Choose scope",
        "options": ["Small", "Large"],
        "affected_task_ids": ["S01"],
        "objective_id": "goal",
    }
    name = gate.fixture.write("question.json", receipt)
    assert gate.fixture.run("question-issue", "--input", name) == 0

    def observed():
        result = fp.read_brief(gate.program.repo, gate.program.directory)
        return result, result["questions"]["items"][0]

    _, row = observed()
    assert row["status"] == "open" and not row["saved_answer"]
    fp.questions.record_answer(
        gate.program.repo, gate.program.directory, "question-one", 1, "Small", "answer-one"
    )
    _, row = observed()
    assert (
        row["status"] == "answered" and row["saved_answer"] and not row["recorded_acknowledgement"]
    )
    for command, status in (
        ("question-acknowledge", "acknowledged"),
        ("question-resolve", "resolved"),
    ):
        name = gate.fixture.write(
            command + ".json",
            {
                **binding,
                "question_id": "question-one",
                "version": 1,
                "actor_id": "director",
                "submission_id": "answer-one",
                "summary": "Apply actual answer",
            },
        )
        assert gate.fixture.run(command, "--input", name) == 0
        result, row = observed()
        assert row["status"] == status and row["recorded_acknowledgement"]
        assert row["recorded_outcome"] == (status == "resolved")
    receipt["id"] = "old-goal-question"
    name = gate.fixture.write("question-old.json", receipt)
    assert gate.fixture.run("question-issue", "--input", name) == 0
    state = gate.program.load()
    state["objective"]["history"].append(deepcopy(state["objective"]))
    state["objective"]["id"] = "new-goal"
    gate.program.save(state)
    result = fp.read_brief(gate.program.repo, gate.program.directory)
    assert all(not row["current_goal_attention"] for row in result["questions"]["items"])
    assert "question" not in result["attention_totals_by_kind"]
    assert result["questions"]["items"][1]["objective_id"] == "goal"


def test_typed_succession_is_separate_from_authored_peer_context(gate):
    state = gate.program.load()
    for identifier in ("worker", "successor"):
        state["actors"].append(
            {
                "id": identifier,
                "role": "worker",
                "parent_id": "director",
                "model_id": None,
                "reasoning_effort": None,
                "provenance": "unknown",
                "observed_status": "unknown",
                "staffing_status": "active",
                "history": [],
            }
        )
    gate.program.save(state)
    assert gate.fixture.claim() == 0
    binding = {
        "source_head": "a" * 40,
        "scope": ["src/S01.py"],
        "source_digest": gate.program.source_digest(["src/S01.py"]),
    }
    outgoing = {
        **binding,
        "id": "transfer-one",
        "role": "worker",
        "from_actor": "worker",
        "to_actor": "successor",
        "task_id": "S01",
        "goal": "Finish scoped work",
        "constraints": ["Keep original scope"],
        "changed_paths": [],
        "decisions": [],
        "evidence": [],
        "unresolved_findings": [],
        "next_action": "Inspect exact scope",
    }
    path = gate.fixture.write("evidence/S01/attempt-1/transfer.json", outgoing)
    assert gate.fixture.run("transfer", "--input", path) == 0

    def observed():
        row = fp.read_brief(gate.program.repo, gate.program.directory)["succession"]["items"][0]
        assert row["kind"] == "succession"
        return row

    assert observed()["stage"] == "proposed"
    assert (
        gate.fixture.run(
            "release",
            "S01",
            "--owner",
            "worker",
            "--reason",
            "Authorized succession",
            "--next-action",
            "Fresh successor claim",
        )
        == 0
    )
    for command, actor, expected in (
        ("transfer-ack", "successor", "acknowledged"),
        ("transfer-accept", "director", "accepted"),
    ):
        path = gate.fixture.write(
            f"evidence/S01/attempt-1/{command}.json",
            {
                **binding,
                "transfer_id": "transfer-one",
                "actor_id": actor,
                "summary": "Inspected current source",
                "evidence": [],
            },
        )
        assert gate.fixture.run(command, "--input", path) == 0
        row = observed()
        assert row["stage"] == expected
    assert row["accept_ref"]["path"].endswith("transfer-accept.json")
    task = fp.read_brief(gate.program.repo, gate.program.directory, "S01")["task"]
    assert task["readiness"]["peer_agreement"]["status"] == "unknown"


def test_coverage_identity_and_unrelated_progress_freshness_are_preserved(gate):
    from tools.tests.test_feature_program_coverage import record_annotation

    gate.ready()
    record_annotation(gate)
    before = fp.read_brief(gate.program.repo, gate.program.directory)
    criterion = before["objective"]["criteria"]["items"][0]
    assert criterion["ordinal"] == 0
    assert criterion["text_sha256"] == fp.sha(criterion["text"].encode())
    assert criterion["contributors"]["items"] == ["S01"]
    assert before["coverage_history"]["items"][0]["freshness"] == "current"
    assert (
        gate.fixture.run(
            "note",
            "S01",
            "--owner",
            "director",
            "--reason",
            "Unrelated coordination",
            "--next-action",
            "Continue actual revalidation",
        )
        == 0
    )
    after = fp.read_brief(gate.program.repo, gate.program.directory)
    assert after["objective"]["criteria"] == criterion_collection_without_revision(
        before["objective"]["criteria"], after["revision"]
    )
    assert after["coverage_history"]["items"][0]["freshness"] == "current"
    gate.program.path("evidence/_coverage/attempt-1/observation.txt").write_text(
        "Changed captured evidence", encoding="utf-8"
    )
    stale = fp.read_brief(gate.program.repo, gate.program.directory)
    assert stale["coverage_history"]["items"][0]["freshness"] != "current"
    assert stale["objective"]["criteria"]["items"][0]["knowledge"] == "unknown"


def criterion_collection_without_revision(value, revision):
    result = deepcopy(value)

    def replace(item):
        if isinstance(item, dict):
            if "revision" in item:
                item["revision"] = revision
            for child in item.values():
                replace(child)
        elif isinstance(item, list):
            for child in item:
                replace(child)

    replace(result)
    return result


@pytest.mark.parametrize(
    "conclusion, status",
    [("success", "ci_recorded_success"), ("pending", "ci_pending"), ("failure", "ci_failed")],
)
def test_recorded_acceptance_and_delivery_facts_never_imply_remote_verification(
    gate, conclusion, status
):
    from tools.tests.test_feature_program_authoring import complete_tasks, provider_request

    complete_tasks(gate)
    request = provider_request(gate, conclusion)
    rows = fp.authoring.delivery_inputs(gate.program, gate.program.load(), request)["inputs"]
    for row in rows:
        path = gate.fixture.write(
            f"evidence/{row['task_id']}/attempt-1/delivery.json", row["receipt"]
        )
        assert gate.fixture.run("delivery", row["task_id"], "--receipt", path) == 0
    result = fp.read_brief(gate.program.repo, gate.program.directory, "S01")
    assert result["task"]["recorded_acceptance"]["status"] == "accepted"
    assert result["task"]["delivery"]["status"] == status
    assert result["task"]["delivery"]["remote_verified_by_tool"] is False
    assert result["execution_authority"] == "not_assessed"


@pytest.mark.parametrize("already_stale", [False, True])
def test_coverage_live_source_cannot_change_after_projection(gate, monkeypatch, already_stale):
    from tools.tests.test_feature_program_coverage import record_annotation

    record_annotation(gate)
    path = gate.program.repo / "src/S01.py"
    if already_stale:
        path.write_text("changed_before = True\n", encoding="utf-8")
    original = fp.brief.project

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        path.write_text("changed_after = True\n", encoding="utf-8")
        return result

    monkeypatch.setattr(fp.brief, "project", changed)
    with pytest.raises(fp.ProgramError, match="source changed during snapshot"):
        fp.read_snapshot(gate.program.repo, gate.program.directory)
    assert fp._source_observations.get() is None


def test_coverage_directory_membership_cannot_change_after_projection(gate, monkeypatch):
    from tools.tests.test_feature_program_coverage import draft, record_annotation

    state = gate.program.load()
    state["tasks"][0]["candidate_paths"] = ["src"]
    gate.program.save(state)
    value = draft(gate)
    value["links"][0]["paths"] = ["src"]
    record_annotation(gate, value)
    original = fp.brief.project

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        (gate.program.repo / "src/new.py").write_text("new = True\n", encoding="utf-8")
        return result

    monkeypatch.setattr(fp.brief, "project", changed)
    with pytest.raises(fp.ProgramError, match="source changed during snapshot"):
        fp.read_brief(gate.program.repo, gate.program.directory)


def test_completed_objective_live_source_race_rejected_without_rejecting_history(gate, monkeypatch):
    from tools.tests.test_feature_program_coverage import integrated_input

    gate.ready()
    name = integrated_input(gate)
    assert gate.fixture.run("objective-complete", "--input", name) == 0
    original = fp.brief.project
    path = gate.program.repo / "src/S01.py"

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        path.write_text("later = True\n", encoding="utf-8")
        return result

    monkeypatch.setattr(fp.brief, "project", changed)
    with pytest.raises(fp.ProgramError, match="source changed during snapshot"):
        fp.read_brief(gate.program.repo, gate.program.directory)
    monkeypatch.setattr(fp.brief, "project", original)
    result = fp.read_brief(gate.program.repo, gate.program.directory, "S01")
    assert result["task"]["status"] == "done"
    assert result["task"]["readiness"]["submission"]["status"] == "not_assessed"
    assert result["task"]["recorded_acceptance"]["status"] == "accepted"


def test_repeated_source_mismatch_cannot_be_swallowed(gate, monkeypatch):
    original = fp.brief.project

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        scope = ["src/S01.py"]
        gate.program.source_snapshot(scope)
        (gate.program.repo / scope[0]).write_text("later = True\n", encoding="utf-8")
        with pytest.raises(fp.ProgramError):
            gate.program.source_snapshot(scope)
        return result

    monkeypatch.setattr(fp.brief, "project", changed)
    with pytest.raises(fp.ProgramError, match="source changed during snapshot"):
        fp.read_brief(gate.program.repo, gate.program.directory)
    assert fp._source_observations.get() is None


def test_source_context_default_nested_exception_and_concurrent_isolation(gate, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    assert fp._source_observations.get() is None
    gate.program.source_snapshot(["src/S01.py"])
    outer = fp._SourceObservations()
    token = fp._source_observations.set(outer)
    try:
        fp.read_brief(gate.program.repo, gate.program.directory)
        assert fp._source_observations.get() is outer
        assert outer.facts == {}
        with pytest.raises(fp.ProgramError, match="Unknown task"):
            fp.read_brief(gate.program.repo, gate.program.directory, "missing")
        assert fp._source_observations.get() is outer
    finally:
        fp._source_observations.reset(token)
    barrier = Barrier(2)
    original = fp.brief.project
    identities = []

    def together(*args, **kwargs):
        identities.append(fp._source_observations.get())
        barrier.wait(timeout=15)
        return original(*args, **kwargs)

    monkeypatch.setattr(fp.brief, "project", together)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(fp.read_brief, gate.program.repo, gate.program.directory)
            for _ in range(2)
        ]
        assert all(future.result()["advisory"] for future in futures)
    assert len(identities) == 2 and identities[0] is not identities[1]
    assert fp._source_observations.get() is None


@pytest.mark.parametrize("origin,changed", [("review", True), ("review", False), ("active", True)])
def test_blocked_current_submission_matches_canonical_resume(gate, origin, changed):
    from tools.tests.test_feature_program_resume import block, resolution

    assert gate.fixture.claim() == 0
    if origin == "review":
        gate.fixture.submit()
    block(gate.fixture)
    if changed:
        (gate.program.repo / "src/S01.py").write_text("later = True\n", encoding="utf-8")
    before = inventory(gate.program.repo)
    result = fp.read_brief(gate.program.repo, gate.program.directory, "S01")
    current = result["task"]["current_attempt"]
    binding = current["block_binding"]
    assert binding["origin"] == origin and binding["status"] == "observed"
    assert binding["reference"]["pointer"] == f"/events/{binding['block']['event_index']}"
    assert binding["reason"] == "Resolve fixture blocker"
    expected = (
        "stale"
        if origin == "review" and changed
        else "observed"
        if origin == "review"
        else "not_assessed"
    )
    assert current["submission"]["status"] == expected
    assert inventory(gate.program.repo) == before
    name = resolution(gate.fixture)
    assert gate.fixture.run("resume", "S01", "--owner", "worker", "--receipt", name) == 0
    assert gate.program.load()["tasks"][0]["status"] == (
        "review" if origin == "review" and not changed else "active"
    )


@pytest.mark.parametrize("ambiguous", [False, True])
def test_legacy_block_origin_uses_retained_events_or_explicit_unknown(gate, ambiguous):
    from tools.tests.test_feature_program_resume import block

    assert gate.fixture.claim() == 0
    gate.fixture.submit()
    block(gate.fixture)
    state = gate.program.load()
    state["tasks"][0]["attempts"][-1].pop("blocks")
    if ambiguous:
        state["events"] = [event for event in state["events"] if event.get("action") != "claim"]
    gate.program.save(state)
    result = fp.read_brief(gate.program.repo, gate.program.directory, "S01")
    binding = result["task"]["current_attempt"]["block_binding"]
    if ambiguous:
        assert binding["status"] == "not_assessed"
        assert binding["reason"] == "Ambiguous block origin; use supported release and fresh claim"
        assert result["task"]["readiness"]["submission"]["status"] == "not_assessed"
    else:
        assert binding["origin"] == "review"
        assert result["task"]["readiness"]["submission"]["status"] == "observed"


def test_task_byte_bound_compacts_context_before_unavailable_verification_lane():
    reference = {"path": "backlog.json", "pointer": "/verification/records", "revision": "a" * 64}
    request_ref = {"path": "evidence/_verification/attempt-1/request.json", "sha256": "b" * 64}
    observation_ref = {"path": "evidence/_verification/attempt-1/finished.json", "sha256": "c" * 64}
    context = {"source": "Original complete context" * 2000}
    lane = {
        "request_id": "request",
        "phase": "finished",
        "result": "passed",
        "applicability": "unavailable",
        "reasons": ["Verification evidence is unavailable"],
        "next_trigger": "Restore exact frozen evidence",
        "context": context,
        "request_ref": request_ref,
        "observation_ref": observation_ref,
        "request_reference": reference,
        "selection_identity": "d" * 64,
    }
    value = {"task": {"verification": {"lanes": fp.brief.collection([lane], reference)}}}
    original = deepcopy(value)
    result = fp.brief.bounded(value, 16384)
    lanes = result["task"]["verification"]["lanes"]
    retained = lanes["items"][0]
    assert len(fp.canonical(result)) <= 16384
    assert (lanes["total"], lanes["shown"], lanes["omitted"]) == (1, 1, 0)
    assert retained["context"] == {
        "status": "omitted",
        "reason": "brief_byte_bound",
        "sha256": fp.digest(context),
    }
    for key in (
        "phase",
        "result",
        "applicability",
        "reasons",
        "next_trigger",
        "request_ref",
        "observation_ref",
        "request_reference",
        "selection_identity",
    ):
        assert retained[key] == lane[key]
    assert value == original
