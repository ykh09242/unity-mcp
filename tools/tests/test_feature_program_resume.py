"""Reasoned same-attempt resumption through the guarded offline CLI."""

from copy import deepcopy
import argparse
from pathlib import Path
import subprocess
import sys

import pytest

# Collection must work from Server with CI's tools-only PYTHONPATH, independently of other tests.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.tests.test_feature_program import Fixture, HEAD, fp  # noqa: E402
from tools.tests.test_feature_program_phases import create_program, gate as gate  # noqa: E402


@pytest.fixture
def fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Fixture:
    # Two reviewed units exercise the real local registry without the unrelated legacy inventory.
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    for task_id in ("S01", "S02"):
        (repo / f"src/{task_id}.py").write_text("original = True\n", encoding="utf-8")
    monkeypatch.setattr(fp, "git", lambda _root, args: "" if args[0] == "status" else HEAD)
    return create_program(repo, "resume-program")


def block(fixture: Fixture, reason: str = "Resolve fixture blocker") -> None:
    assert (
        fixture.run(
            "block",
            "S01",
            "--owner",
            "worker",
            "--reason",
            reason,
            "--next-action",
            "Inspect and resolve",
        )
        == 0
    )


def resolution(fixture: Fixture, identifier: str = "resolution-1") -> str:
    state = fixture.program.load()
    return fixture.write(
        f"evidence/S01/attempt-1/{identifier}.json",
        {
            "schema_version": 1,
            "operation": "resume",
            "id": identifier,
            **fixture.program.resume_binding(state, "S01"),
            "actor_id": "worker",
            "reason": "Actual synthetic blocker resolution",
            "next_action": "Continue verified work",
            "evidence": [fixture.evidence("S01", label=identifier)],
        },
    )


def standalone(fixture: Fixture, *args: str) -> subprocess.CompletedProcess[str]:
    # Fresh interpreter uses the actual public parser/transaction with clearly synthetic HEAD only.
    launcher = (
        "import sys; from tools import feature_program as fp; "
        f"fp.git=lambda root,args: '' if args[0]=='status' else '{HEAD}'; "
        "raise SystemExit(fp.main(sys.argv[1:]))"
    )
    command = [
        sys.executable,
        "-B",
        "-c",
        launcher,
        "--repo",
        str(fixture.program.repo),
        "--program-dir",
        fixture.program.directory.relative_to(fixture.program.repo).as_posix(),
        *args,
    ]
    result = subprocess.run(
        command,
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        check=False,
    )
    print("CLI", args, "exit", result.returncode, result.stdout, result.stderr)
    return result


@pytest.mark.parametrize("use_server", [True, False])
def test_standalone_cli_uses_checkout_from_different_parent_directory(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, use_server: bool
) -> None:
    # Given actual blocked ownership and the CI import path without the repository parent.
    assert fixture.claim() == 0
    block(fixture)
    receipt = resolution(fixture)
    monkeypatch.setenv("PYTHONPATH", str(fp.ROOT / "tools"))
    monkeypatch.chdir(fp.ROOT / "Server" if use_server else fixture.program.repo)
    # When a fresh CLI interpreter starts independently of the parent's current directory.
    result = standalone(fixture, "resume", "S01", "--owner", "worker", "--receipt", receipt)
    # Then it performs the actual guarded resolution rather than failing its package import.
    assert result.returncode == 0, result.stderr
    task = fixture.program.task(fixture.program.load(), "S01")
    assert task["status"] == "active" and fixture.program.current_attempt(task)["number"] == 1


def test_active_origin_resume_keeps_attempt_and_frozen_history(fixture: Fixture) -> None:
    # Given an active attempt blocked after implementation, with retained frozen settings.
    assert fixture.claim() == 0
    block(fixture)
    receipt = resolution(fixture)
    before = deepcopy(fixture.program.load())
    task_before = fixture.program.task(before, "S01")
    attempt_before = fixture.program.current_attempt(task_before)
    fixture.program.repo.joinpath("src/S01.py").write_text("resolved = True\n", encoding="utf-8")
    receipt = resolution(fixture)
    # When the owner resolves the blocker through the public command.
    assert fixture.run("resume", "S01", "--owner", "worker", "--receipt", receipt) == 0
    # Then the same active owner/attempt keeps its baseline, history and actor snapshot.
    after = fixture.program.load()
    task = fixture.program.task(after, "S01")
    attempt = fixture.program.current_attempt(task)
    assert task["status"] == attempt["status"] == "active"
    assert task["owner"] == "worker" and len(task["attempts"]) == 1
    for key in (
        "owner",
        "number",
        "scope",
        "baseline",
        "baseline_digest",
        "claimed_head",
        "assignment",
    ):
        assert attempt[key] == attempt_before[key]
    assert task["notes"][: len(task_before["notes"])] == task_before["notes"]
    assert after["events"][: len(before["events"])] == before["events"]
    assert fixture.run("check") == 0


@pytest.mark.parametrize("changed", [False, True])
def test_review_origin_cli_preserves_round_or_requires_fresh_review(
    fixture: Fixture, changed: bool
) -> None:
    # Given a frozen submitted round and its review, blocked before acceptance.
    assert fixture.claim() == 0
    execution = fixture.submit()
    old_review = fixture.review()
    block(fixture)
    before = fixture.program.load()
    attempt_before = deepcopy(fixture.program.current_attempt(fixture.program.task(before, "S01")))
    frozen = {name: fixture.program.path(name).read_bytes() for name in (execution, old_review)}
    if changed:
        (fixture.program.repo / "src/S01.py").write_text("corrected = True\n", encoding="utf-8")
    receipt = resolution(fixture)
    # When actual standalone CLI resolves the blocked review attempt.
    assert (
        standalone(fixture, "resume", "S01", "--owner", "worker", "--receipt", receipt).returncode
        == 0
    )
    # Then unchanged bytes restore review; changed bytes require a new submission and review.
    state = fixture.program.load()
    task = fixture.program.task(state, "S01")
    attempt = fixture.program.current_attempt(task)
    assert attempt["rounds"] == attempt_before["rounds"]
    assert attempt["submission"] == (None if changed else attempt_before["submission"])
    assert task["status"] == ("active" if changed else "review")
    assert frozen == {name: fixture.program.path(name).read_bytes() for name in frozen}
    before_complete = fixture.program.path("backlog.json").read_bytes()
    if changed:
        assert fixture.run("complete", "S01", "--review", old_review) == 1
        assert before_complete == fixture.program.path("backlog.json").read_bytes()
        fixture.submit(round_number=2)
        assert fixture.run("complete", "S01", "--review", old_review) == 1
        assert fixture.run("complete", "S01", "--review", fixture.review(round_number=2)) == 0
    else:
        assert fixture.run("complete", "S01", "--review", old_review) == 0
    assert fixture.run("check") == 0


@pytest.mark.parametrize("legacy", [False, True])
def test_repeated_blocks_keep_authoritative_review_origin(fixture: Fixture, legacy: bool) -> None:
    # Given repeated blocks, optionally an authentic older claim/event schema without snapshots.
    assert fixture.claim() == 0
    fixture.submit()
    block(fixture)
    if legacy:
        state = fixture.program.load()
        attempt = fixture.program.current_attempt(fixture.program.task(state, "S01"))
        del attempt["blocks"]
        for event in state["events"]:
            event.pop("scope", None)
            if event.get("action") == "block":
                event.pop("attempt", None)
        fixture.program.save(state)
    block(fixture, "Repeated observation retains first origin")
    receipt = resolution(fixture)
    before = fixture.program.load()
    # When repeated blocked status is resolved on the same attempt.
    assert fixture.run("resume", "S01", "--owner", "worker", "--receipt", receipt) == 0
    # Then review origin survives, all old event bytes remain, and replay is denied.
    after = fixture.program.load()
    assert fixture.program.task(after, "S01")["status"] == "review"
    assert after["events"][: len(before["events"])] == before["events"]
    committed = fixture.program.path("backlog.json").read_bytes()
    assert fixture.run("resume", "S01", "--owner", "worker", "--receipt", receipt) == 1
    assert committed == fixture.program.path("backlog.json").read_bytes()


@pytest.mark.parametrize(
    "field",
    [
        "attempt",
        "owner",
        "expected_revision",
        "definition_digest",
        "block",
        "origin",
        "scope",
        "head",
        "source_digest",
        "actor_id",
        "destination",
        "evidence",
    ],
)
def test_stale_resolution_cli_rejects_without_canonical_change(
    fixture: Fixture, field: str
) -> None:
    # Given a current authored resolution deliberately asserting a stale or unsafe fact.
    assert fixture.claim() == 0
    block(fixture)
    receipt = resolution(fixture)
    value = fp.read_json(fixture.program.path(receipt))
    value[field] = {
        "attempt": True,
        "owner": "old-worker",
        "expected_revision": "0" * 64,
        "definition_digest": "0" * 64,
        "block": {"event_index": 0, "event_digest": "0" * 64},
        "origin": "review",
        "scope": ["src/S02.py"],
        "head": "b" * 40,
        "source_digest": "0" * 64,
        "actor_id": "imposter",
        "destination": "done",
        "evidence": ["evidence/S01/attempt-1/missing.txt"],
    }[field]
    fixture.write(receipt, value)
    before = fixture.program.path("backlog.json").read_bytes()
    # When the standalone public command validates the input.
    result = standalone(fixture, "resume", "S01", "--owner", "worker", "--receipt", receipt)
    # Then rejection creates no canonical change or false success.
    assert result.returncode == 1 and "Traceback" not in result.stderr
    assert before == fixture.program.path("backlog.json").read_bytes()


@pytest.mark.parametrize(
    "fault",
    [
        "scope",
        "unmet",
        "source-report",
        "old-evidence",
        "retired-owner",
        "ambiguous-origin",
        "predecessor",
        "incomplete",
        "claimed-scope-alias",
    ],
)
def test_current_resume_guards_reject_direct_api_without_mutation(
    fixture: Fixture, fault: str
) -> None:
    # Given actual blocked ownership and a subsequently changed prerequisite or frozen binding.
    assert fixture.claim() == 0
    block(fixture)
    receipt = resolution(fixture)
    state = fixture.program.load()
    task = fixture.program.task(state, "S01")
    attempt = fixture.program.current_attempt(task)
    if fault in {"scope", "claimed-scope-alias"}:
        attempt["scope"] = (
            ["src/S02.py"] if fault == "scope" else ["src/S01.py", "src/S01.py/other"]
        )
    elif fault == "unmet":
        task["depends_on"] = ["S02"]
    elif fault == "retired-owner":
        state["actors"] = [
            {
                "id": "worker",
                "role": "worker",
                "parent_id": None,
                "model_id": None,
                "reasoning_effort": None,
                "provenance": "unknown",
                "observed_status": "unknown",
                "staffing_status": "retired",
                "history": [],
            }
        ]
    elif fault == "ambiguous-origin":
        attempt.pop("blocks")  # Older schema has no modern event-index observations to rewrite.
        state["events"] = [event for event in state["events"] if event.get("action") != "claim"]
    fixture.program.save(state)
    value = fp.read_json(fixture.program.path(receipt))
    value.update(
        {
            "expected_revision": fp.phases.revision(state),
            "scope": attempt["scope"],
            "definition_digest": fp.digest(
                {
                    key: data
                    for key, data in task.items()
                    if key not in fp.phases.TASK_PROGRESS_FIELDS
                }
            ),
        }
    )
    fixture.write(receipt, value)
    if fault == "source-report":
        fixture.program.path("source.md").write_bytes(b"changed frozen source")
    elif fault == "old-evidence":
        fixture.program.path("evidence/S01/attempt-1/inspection.txt").write_text(
            "changed", encoding="utf-8"
        )
    elif fault == "predecessor":
        fixture.write("RELOCATED.json", {"destination": "new-program"})
    elif fault == "incomplete":
        fixture.write("RELOCATION_INCOMPLETE.json", {"phase": "copying"})
    before = fixture.program.path("backlog.json").read_bytes()
    input_state = deepcopy(state)
    # When a direct Program caller attempts the same mutation instead of using parser dispatch.
    with pytest.raises(
        (
            fp.ProgramError,
            fp.coordination.GovernanceError,
            fp.phases.PhaseError,
            fp.planning.PlanningError,
        )
    ):
        fixture.program.resume(state, argparse.Namespace(id="S01", owner="worker", receipt=receipt))
    # Then durable and caller state both remain unchanged.
    assert fixture.program.path("backlog.json").read_bytes() == before and state == input_state


@pytest.mark.parametrize("boundary", ["source", "evidence", "revision", "render", "atomic"])
def test_resume_publication_boundary_is_truthful(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    # Given a valid resolution and an injected actual save/publication boundary failure.
    assert fixture.claim() == 0
    block(fixture)
    receipt = resolution(fixture)
    before = fixture.program.path("backlog.json").read_bytes()
    original_views = fp.Program.views

    def changed_views(program: fp.Program, state: fp.Record) -> dict[str, str]:
        result = original_views(program, state)
        if boundary == "source":
            (program.repo / "src/S01.py").write_text("raced = True\n", encoding="utf-8")
        elif boundary == "evidence":
            program.path("evidence/S01/attempt-1/resolution-1.txt").write_text(
                "raced", encoding="utf-8"
            )
        elif boundary == "revision":
            old = fp.read_json(program.path("backlog.json"))
            old["runtime_observation"] = "Actual concurrent fixture observation"
            fp.atomic_write(program.path("backlog.json"), fp.canonical(old))
        return result

    if boundary in {"source", "evidence", "revision"}:
        monkeypatch.setattr(fp.Program, "views", changed_views)
    elif boundary == "render":
        monkeypatch.setattr(
            fp.Program,
            "render",
            lambda *_args: (_ for _ in ()).throw(OSError("Injected render failure")),
        )
    else:
        monkeypatch.setattr(
            fp,
            "atomic_write",
            lambda *_args: (_ for _ in ()).throw(OSError("Injected atomic failure")),
        )
    # When the resolution crosses the guarded canonical publication boundary.
    assert fixture.run("resume", "S01", "--owner", "worker", "--receipt", receipt) == 1
    # Then only render failure has committed a resolution; all precommit failures have none.
    persisted = fp.read_json(fixture.program.path("backlog.json"))
    task = fixture.program.task(persisted, "S01")
    assert task["status"] == ("active" if boundary == "render" else "blocked")
    if boundary not in {"revision", "render"}:
        assert fixture.program.path("backlog.json").read_bytes() == before
    monkeypatch.undo()
    monkeypatch.setattr(fp, "git", lambda _root, args: "" if args[0] == "status" else HEAD)
    if boundary == "render":
        assert fixture.run("render") == 0 and fixture.run("check") == 0
        committed = fixture.program.path("backlog.json").read_bytes()
        assert fixture.run("resume", "S01", "--owner", "worker", "--receipt", receipt) == 1
        assert committed == fixture.program.path("backlog.json").read_bytes()


@pytest.mark.parametrize(
    "command", ["note", "block", "release", "reopen", "complete", "rework", "resume"]
)
def test_current_director_controls_new_overrides_not_magic_leader(gate, command: str) -> None:
    # Given an appointed director and actual owned/reviewed state for the requested override.
    fixture = gate.fixture
    assert fixture.claim() == 0
    if command in {"complete", "rework", "reopen"}:
        fixture.submit()
    if command == "reopen":
        assert fixture.run("complete", "S01", "--review", fixture.review()) == 0
    if command == "resume":
        block(fixture)
    before = fixture.program.path("backlog.json").read_bytes()

    def override(actor: str) -> int:
        if command in {"complete", "rework"}:
            name = fixture.review(accepted=command == "complete")
            value = fp.read_json(fixture.program.path(name))
            value.update(
                {"reviewer": actor, "reviewer_role": "leader", "reason": "Explicit director review"}
            )
            fixture.write(name, value)
            return fixture.run(command, "S01", "--review", name)
        if command == "resume":
            name = resolution(fixture, "director-resolution")
            value = fp.read_json(fixture.program.path(name))
            value["actor_id"] = actor
            fixture.write(name, value)
            return fixture.run(command, "S01", "--owner", actor, "--receipt", name)
        return fixture.run(
            command,
            "S01",
            "--owner",
            actor,
            "--reason",
            "Explicit current authority",
            "--next-action",
            "Retain history",
        )

    # When a label or historical magic leader first tries to override, then the real director acts.
    assert override("leader") == 1
    assert fixture.program.path("backlog.json").read_bytes() == before
    assert override("director") == 0
    # Then ownership/settings/round history remain truthful and legacy fallback was not used.
    assert fixture.run("check") == 0
    attempt = fixture.program.current_attempt(fixture.program.task(fixture.program.load(), "S01"))
    assert attempt["owner"] == "worker" and attempt["assignment"]["actor_id"] == "worker"


def test_director_succession_preserves_old_authority_and_rejects_it_now(gate) -> None:
    # Given current director authority and actual acknowledged director transfer.
    fixture = gate.fixture
    assert fixture.claim() == 0
    block(fixture)
    scope = ["src/S01.py", "src/S02.py"]
    binding = {
        "source_head": HEAD,
        "scope": scope,
        "source_digest": fixture.program.source_digest(scope),
    }
    actor = {
        "id": "director-next",
        "role": "director",
        "parent_id": None,
        "model_id": None,
        "reasoning_effort": None,
        "provenance": "unknown",
        "observed_status": "unknown",
        "observed_at": None,
        "decision": "new",
        "reason": "Explicit synthetic successor",
    }

    def govern(command: str, value: fp.Record) -> None:
        name = fixture.write(f"evidence/_coordination/attempt-1/{command}-resume.json", value)
        assert fixture.run(command, "--input", name) == 0

    govern("actor-record", actor)
    govern(
        "transfer",
        {
            **binding,
            "id": "director-transfer",
            "role": "director",
            "from_actor": "director",
            "to_actor": "director-next",
            "goal": "Continue accepted objective",
            "constraints": [],
            "changed_paths": [],
            "decisions": [],
            "unresolved_findings": [],
            "next_action": "Inspect handoff",
            "authority": {
                "authorized_by": "synthetic-human",
                "basis": "Explicit successor authority",
            },
            "evidence": [],
        },
    )
    govern(
        "transfer-ack",
        {
            **binding,
            "transfer_id": "director-transfer",
            "actor_id": "director-next",
            "summary": "Actual fixture successor inspection",
            "evidence": [],
        },
    )
    govern(
        "transfer-accept",
        {
            **binding,
            "transfer_id": "director-transfer",
            "actor_id": "director",
            "summary": "Accept exact successor inspection",
            "evidence": [],
        },
    )
    before_state = fixture.program.load()
    old_history = deepcopy(before_state["director"]["history"])
    receipt = resolution(fixture)
    value = fp.read_json(fixture.program.path(receipt))
    value["actor_id"] = "director"
    fixture.write(receipt, value)
    before = fixture.program.path("backlog.json").read_bytes()
    # When historical director tries to resume, only the current accepted successor is authorized.
    assert fixture.run("resume", "S01", "--owner", "director", "--receipt", receipt) == 1
    assert fixture.program.path("backlog.json").read_bytes() == before
    value["actor_id"] = "director-next"
    fixture.write(receipt, value)
    assert fixture.run("resume", "S01", "--owner", "director-next", "--receipt", receipt) == 0
    # Then old acknowledgement/history bytes remain intact.
    assert fixture.program.load()["director"]["history"] == old_history
    assert fixture.run("check") == 0


def test_pending_director_transfer_prevents_same_attempt_resume(gate) -> None:
    # Given a recorded outgoing director transfer that has not been accepted.
    fixture = gate.fixture
    assert fixture.claim() == 0
    block(fixture)
    state = fixture.program.load()
    state["actors"].append(
        {
            "id": "successor",
            "role": "director",
            "parent_id": None,
            "model_id": None,
            "reasoning_effort": None,
            "provenance": "unknown",
            "observed_status": "unknown",
            "history": [],
        }
    )
    fixture.program.save(state)
    outgoing = {
        "source_head": HEAD,
        "scope": ["src/S01.py"],
        "source_digest": fixture.program.source_digest(["src/S01.py"]),
        "id": "pending",
        "role": "director",
        "from_actor": "director",
        "to_actor": "successor",
        "goal": "Continue objective",
        "constraints": [],
        "changed_paths": [],
        "decisions": [],
        "unresolved_findings": [],
        "next_action": "Acknowledge",
        "evidence": [],
        "authority": {"authorized_by": "synthetic-human", "basis": "Explicit pending transfer"},
    }
    name = fixture.write("evidence/_coordination/attempt-1/pending.json", outgoing)
    assert fixture.run("transfer", "--input", name) == 0
    receipt = resolution(fixture)
    before = fixture.program.path("backlog.json").read_bytes()
    # When an owner tries to bypass the relevant pending succession with a resolution.
    assert fixture.run("resume", "S01", "--owner", "worker", "--receipt", receipt) == 1
    # Then the attempt and transfer remain blocked and unchanged.
    assert before == fixture.program.path("backlog.json").read_bytes()


def test_accepted_current_metadata_not_historical_guesses_controls_resume(gate) -> None:
    # Given a blocked owner and an accepted renewed current planning definition.
    gate.ready()
    fixture = gate.fixture
    assert fixture.claim() == 0
    block(fixture)
    state = fixture.program.load()
    gate.program.task(state, "S01")["planning_note"] = (
        "Accepted actual current definition evolution"
    )
    gate.program.save(state)  # Isolated fixture mutation; never historical metadata fabrication.
    assert gate.freeze("renew-current-freeze") == 0
    assert gate.review(label="renew-current-review") == 0
    assert gate.accept(label="renew-current-accept") == 0
    binding = fp.phases.planning_binding(gate.program.load())
    receipt = resolution(fixture)
    # When the current owner resumes against the fresh accepted definition/revision.
    assert fixture.run("resume", "S01", "--owner", "worker", "--receipt", receipt) == 0
    # Then ordinary runtime resolution retains the accepted stable binding.
    assert fp.phases.planning_binding(gate.program.load()) == binding
    assert fixture.run("check") == 0


def test_legacy_leader_resume_and_self_review_requires_authority(fixture: Fixture) -> None:
    # Given blocked legacy ownership without any director appointment.
    assert fixture.claim() == 0
    block(fixture)
    name = resolution(fixture)
    receipt = fp.read_json(fixture.program.path(name))
    receipt["actor_id"] = "leader"
    fixture.write(name, receipt)
    # When the legacy leader resolves and submits an honestly labeled leader review.
    assert fixture.run("resume", "S01", "--owner", "leader", "--receipt", name) == 0
    fixture.submit()
    review_name = fixture.review()
    review = fp.read_json(fixture.program.path(review_name))
    review.update(
        {"reviewer": "worker", "reviewer_role": "leader", "reason": "Label alone is insufficient"}
    )
    fixture.write(review_name, review)
    before = fixture.program.path("backlog.json").read_bytes()
    assert fixture.run("complete", "S01", "--review", review_name) == 1
    assert before == fixture.program.path("backlog.json").read_bytes()
    review["reviewer"] = "leader"
    fixture.write(review_name, review)
    assert fixture.run("complete", "S01", "--review", review_name) == 0
    # Then historical unknown launch settings and exact worker identity survive.
    task = fixture.program.task(fixture.program.load(), "S01")
    assert task["attempts"][0]["owner"] == "worker"
    assert task["attempts"][0]["assignment"]["provenance"] == "unknown"
    assert fixture.run("check") == 0


def test_phase_adoption_holds_resume_until_current_acceptance(gate) -> None:
    # Given a legitimate legacy claim blocked before explicit phase adoption.
    fixture = gate.fixture
    assert fixture.claim() == 0
    block(fixture)
    assert gate.adopt() == 0
    receipt = resolution(fixture)
    before = fixture.program.path("backlog.json").read_bytes()
    # When adoption has no current accepted inventory yet, resume cannot evade the gate.
    assert fixture.run("resume", "S01", "--owner", "worker", "--receipt", receipt) == 1
    assert before == fixture.program.path("backlog.json").read_bytes()
    assert gate.freeze() == gate.review() == gate.accept() == 0
    receipt = resolution(fixture, "accepted-resolution")
    assert fixture.run("resume", "S01", "--owner", "worker", "--receipt", receipt) == 0
    # Then the same legitimate attempt proceeds under the accepted current definitions.
    assert len(fixture.program.task(fixture.program.load(), "S01")["attempts"]) == 1


def test_collision_rejects_even_direct_caller_with_prevalidated_receipt(fixture: Fixture) -> None:
    # Given two actual claimed scopes, then a synthetic conflicting current runtime state.
    assert fixture.claim() == 0 and fixture.claim("S02", owner="other") == 0
    block(fixture)
    name = resolution(fixture)
    state = fixture.program.load()
    fixture.program.current_attempt(fixture.program.task(state, "S02"))["scope"] = ["src/S01.py"]
    before = fixture.program.path("backlog.json").read_bytes()
    original = deepcopy(state)
    # When a direct caller presents a formerly valid receipt against the collision.
    with pytest.raises(fp.ProgramError, match="collision"):
        fixture.program.resume(state, argparse.Namespace(id="S01", owner="worker", receipt=name))
    # Then neither the actual registry nor the direct caller's state is rewritten.
    assert before == fixture.program.path("backlog.json").read_bytes() and state == original


@pytest.mark.parametrize(
    "fault", ["block-origin", "block-index", "resolution-reason", "resolution-event"]
)
def test_resolution_history_is_checked_without_reauthorizing_old_actors(
    fixture: Fixture, fault: str
) -> None:
    # Given a completed historical resolution, retained independently of later actor staffing.
    assert fixture.claim() == 0
    block(fixture)
    name = resolution(fixture)
    assert fixture.run("resume", "S01", "--owner", "worker", "--receipt", name) == 0
    state = fixture.program.load()
    attempt = fixture.program.current_attempt(fixture.program.task(state, "S01"))
    if fault == "block-origin":
        attempt["blocks"][0]["origin"] = "review"
    elif fault == "block-index":
        attempt["blocks"][0]["event_index"] = True
    elif fault == "resolution-reason":
        attempt["resolutions"][0]["reason"] = "Invented old reason"
    else:
        attempt["resolutions"][0]["event_digest"] = "0" * 64
    before = fixture.program.path("backlog.json").read_bytes()
    # When a historical field is altered independently of its frozen receipt/events.
    with pytest.raises(fp.ProgramError):
        fixture.program.validate(state)
    # Then the mismatch is rejected and original historical bytes remain.
    assert before == fixture.program.path("backlog.json").read_bytes()


def test_source_change_between_destination_capture_and_validation_rejects(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given changed review bytes and a receipt binding the actual current implementation.
    assert fixture.claim() == 0
    fixture.submit()
    block(fixture)
    before_change = fixture.program.resume_binding(fixture.program.load(), "S01")
    (fixture.program.repo / "src/S01.py").write_text("changed_review = True\n", encoding="utf-8")
    receipt = resolution(fixture)
    before = fixture.program.path("backlog.json").read_bytes()
    original_binding = fp.Program.resume_binding
    calls = 0

    def raced_binding(program: fp.Program, state: fp.Record, task_id: str) -> fp.Record:
        nonlocal calls
        calls += 1
        # The first observation precedes the source write; subsequent reads are actual current bytes.
        return before_change if calls == 1 else original_binding(program, state, task_id)

    monkeypatch.setattr(fp.Program, "resume_binding", raced_binding)
    # When destination derivation and validation observe different scoped bytes.
    assert fixture.run("resume", "S01", "--owner", "worker", "--receipt", receipt) == 1
    # Then a stale first observation cannot restore review against the fresh changed bytes.
    assert before == fixture.program.path("backlog.json").read_bytes()
