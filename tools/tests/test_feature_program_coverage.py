"""Explicit coverage references and real authoritative diagnostics on isolated programs."""

from copy import deepcopy
import argparse
import json
import subprocess
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.tests.test_feature_program_phases import GateFixture, gate  # noqa: E402, F401
from tools.tests.test_feature_program import fp  # noqa: E402
from tools.tests.test_feature_program_planning import membership, reviewed_input  # noqa: E402


def draft(gate: GateFixture) -> fp.Record:
    state = gate.program.load()
    criterion = fp.record(state["objective"], "objective")["criteria"][0]
    evidence = gate.fixture.write(
        "evidence/_coverage/attempt-1/observation.txt", "Actual fixture observation"
    )
    return {
        "schema_version": 1,
        "operation": "coverage-record",
        "id": "coverage-1",
        "actor_id": "director",
        "reason": "Explicit synthetic contribution, no acceptance inference",
        "links": [
            {
                "criterion": {"ordinal": 0, "text_sha256": fp.sha(criterion.encode())},
                "task_id": "S01",
                "scope_kind": "candidate",
                "paths": ["src/S01.py"],
                "evidence": [
                    {"path": evidence, "sha256": fp.sha(gate.program.path(evidence).read_bytes())}
                ],
            }
        ],
        "uncovered": [],
    }


def test_real_binding_and_annotation_preserve_original_contract(gate: GateFixture) -> None:
    # Given reviewed objective definitions and actual supplied proof bytes.
    gate.ready()
    state = gate.program.load()
    value = draft(gate)
    name = gate.fixture.write("evidence/_coverage/attempt-1/draft.json", value)
    before = gate.program.path("backlog.json").read_bytes()
    # When the real read-only CLI prepares an explicitly authored linkage.
    assert gate.fixture.run("coverage-binding", "--input", name) == 0
    assert gate.program.path("backlog.json").read_bytes() == before
    # Then recording that exact binding leaves criteria and planning acceptance intact.
    value.update(fp.coverage.bind_input(gate.program, state, value))
    name = gate.fixture.write("evidence/_coverage/attempt-1/annotation.json", value)
    assert gate.fixture.run("coverage-record", "--input", name) == 0
    current = gate.program.load()
    assert current["objective"] == state["objective"] and current["phases"] == state["phases"]
    assert gate.fixture.run("coverage") == 0
    assert gate.fixture.run("check") == 0


def record_annotation(
    gate: GateFixture, value: fp.Record | None = None, identifier: str = "coverage-1"
) -> fp.Record:
    value = value if value is not None else draft(gate)
    value["id"] = identifier
    value.update(fp.coverage.bind_input(gate.program, gate.program.load(), value))
    path = gate.fixture.write(f"evidence/_coverage/attempt-1/{identifier}.json", value)
    assert gate.fixture.run("coverage-record", "--input", path) == 0
    return {"path": path, "sha256": fp.sha(gate.program.path(path).read_bytes())}


def reliance(gate: GateFixture, ref: fp.Record) -> list[fp.Json]:
    text = gate.program.load()["objective"]["criteria"][0]
    return [{"receipt": ref, "criteria": [{"ordinal": 0, "text_sha256": fp.sha(text.encode())}]}]


def test_arbitrary_mapping_and_explicit_uncovered_not_completion_inference(
    gate: GateFixture,
) -> None:
    # Given three original synthetic criteria before any candidate acceptance.
    state = gate.program.load()
    state["objective"]["criteria"] = [
        "First exact criterion",
        "Second exact criterion",
        "Third exact criterion",
    ]
    gate.program.save(state)
    gate.ready()
    value = draft(gate)
    base = deepcopy(value["links"][0])
    second_task = {**deepcopy(base), "task_id": "S02", "paths": ["src/S02.py"]}
    second_criterion = {
        **deepcopy(base),
        "criterion": {"ordinal": 1, "text_sha256": fp.sha(b"Second exact criterion")},
    }
    value["links"] = [base, second_task, second_criterion]
    value["uncovered"] = [
        {
            "criterion": {"ordinal": 2, "text_sha256": fp.sha(b"Third exact criterion")},
            "reason": "Explicit observed zero contributors",
        }
    ]
    original = deepcopy(gate.program.load()["objective"])
    # When the exact authored many-to-many mapping is recorded and projected.
    record_annotation(gate, value)
    view = fp.coverage.projection(gate.program, gate.program.load())
    # Then no prose, planned owner or task status supplied a criterion pass.
    assert [len(row["links"]) for row in view["criteria"]] == [2, 1, 0]
    assert [row["knowledge"] for row in view["criteria"]] == [
        "contributors_declared",
        "contributors_declared",
        "uncovered",
    ]
    assert all(row["acceptance"] == "not_inferred" for row in view["criteria"])
    assert gate.program.load()["objective"] == original


@pytest.mark.parametrize(
    "fault",
    [
        "bool",
        "float",
        "text",
        "unknown-task",
        "scope",
        "unsafe",
        "revision",
        "source",
        "authority",
        "duplicate",
        "hash",
        "attempt",
        "round",
        "uncovered-conflict",
    ],
)
def test_invalid_record_rejects_without_canonical_mutation(gate: GateFixture, fault: str) -> None:
    # Given a complete actual annotation binding and preserved canonical pre-state.
    gate.ready()
    value = draft(gate)
    value.update(fp.coverage.bind_input(gate.program, gate.program.load(), value))
    link = value["links"][0]
    match fault:
        case "bool":
            link["criterion"]["ordinal"] = False
        case "float":
            link["criterion"]["ordinal"] = 0.0
        case "text":
            link["criterion"]["text_sha256"] = "0" * 64
        case "unknown-task":
            link["task_id"] = "UNKNOWN"
        case "scope":
            link["paths"] = ["src/S02.py"]
        case "unsafe":
            link["paths"] = ["../unsafe"]
        case "revision":
            value["expected_revision"] = "0" * 64
        case "source":
            link["binding"]["source_digest"] = "0" * 64
        case "authority":
            value["actor_id"] = "leader"
        case "duplicate":
            value["links"].append(deepcopy(link))
        case "hash":
            link["evidence"][0]["sha256"] = "0" * 64
        case "attempt":
            link["attempt"] = 1
        case "round":
            link["round"] = 1
        case "uncovered-conflict":
            value["uncovered"] = [
                {"criterion": link["criterion"], "reason": "Conflicts with contributor"}
            ]
        case _:
            pytest.fail("Unknown fixture fault")
    name = gate.fixture.write("evidence/_coverage/attempt-1/invalid.json", value)
    before = gate.program.path("backlog.json").read_bytes()
    # When the real CLI tries to record malformed or stale facts.
    assert gate.fixture.run("coverage-record", "--input", name) == 1
    # Then no annotation or event was published.
    assert gate.program.path("backlog.json").read_bytes() == before


@pytest.mark.parametrize(
    "drift", ["source", "definition", "evidence", "membership", "note", "actor"]
)
def test_progress_freshness_distinguishes_actual_binding_changes(
    gate: GateFixture, drift: str
) -> None:
    # Given an exact recorded mapping whose registry token is historical.
    gate.ready()
    record_annotation(gate)
    state = gate.program.load()
    match drift:
        case "source":
            (gate.program.repo / "src/S01.py").write_text("changed = True\n", encoding="utf-8")
        case "definition":
            gate.program.task(state, "S01")["planning_note"] = "Consequential definition change"
        case "evidence":
            gate.program.path("evidence/_coverage/attempt-1/observation.txt").write_text(
                "changed proof", encoding="utf-8"
            )
        case "membership":
            state["objective"]["task_ids"] = ["S02"]
        case "note":
            gate.program.task(state, "S01")["next_action"] = "Ordinary runtime observation"
        case "actor":
            state["actors"][0]["observed_status"] = "idle"
        case _:
            pytest.fail("Unknown fixture drift")
    # When the same historical annotation is observed against actual current state/bytes.
    view = fp.coverage.projection(gate.program, state)
    # Then runtime token/actor/action progress is separate from stable declared/source identity.
    assert view["history"][0]["freshness"] == ("current" if drift in {"note", "actor"} else "stale")
    assert state["coverage_annotations"][0]["annotation"]["id"] == "coverage-1"


def test_recorded_failed_check_is_not_missing_or_accepted(gate: GateFixture) -> None:
    # Given a real unsubmitted execution receipt containing an actual synthetic failure.
    gate.ready()
    assert gate.fixture.claim() == 0
    name = gate.fixture.receipt()
    execution = fp.read_json(gate.program.path(name))
    execution["checks"][0]["status"] = "failed"
    gate.fixture.write(name, execution)
    value = draft(gate)
    link = value["links"][0]
    link.update(
        {
            "scope_kind": "attempt",
            "attempt": 1,
            "evidence": [{"path": name, "sha256": fp.sha(gate.program.path(name).read_bytes())}],
            "records": [
                {
                    "kind": "execution",
                    "receipt": {
                        "path": name,
                        "sha256": fp.sha(gate.program.path(name).read_bytes()),
                    },
                }
            ],
        }
    )
    # When the source-bound artifact is linked without pretending submission passed.
    record_annotation(gate, value)
    view = fp.coverage.projection(gate.program, gate.program.load())
    # Then its actual failed check is retained and acceptance stays unknown.
    assert (
        view["criteria"][0]["recorded_observations"][0]["facts"]["checks"][0]["status"] == "failed"
    )
    assert view["tasks"][0]["recorded_acceptance"]["status"] == "unknown"


@pytest.mark.parametrize(
    "fault", ["failed-check", "missing-check", "next-action", "source", "missing-input"]
)
def test_shared_submission_diagnostics_match_real_authoritative_failure(
    gate: GateFixture, fault: str
) -> None:
    # Given actual current ownership and a supplied execution input.
    gate.ready()
    assert gate.fixture.claim() == 0
    name = gate.fixture.receipt()
    value = fp.read_json(gate.program.path(name))
    match fault:
        case "failed-check":
            value["checks"][0]["status"] = "failed"
        case "missing-check":
            value["checks"].pop()
        case "next-action":
            value["next_action"] = ""
        case "source":
            value["changed_paths"] = ["src/S01.py"]
        case "missing-input":
            name = "evidence/S01/attempt-1/absent.json"
        case _:
            pytest.fail("Unknown fixture fault")
    if fault != "missing-input":
        gate.fixture.write(name, value)
    state = gate.program.load()
    args = argparse.Namespace(operation="submit", input=name, task="S01", owner="worker")
    before = deepcopy(state)
    # When diagnostic preparation and the actual direct submission validate the same input.
    result = fp.coverage.diagnostics(
        gate.program, state, args, observed_head=gate.program.current_head()
    )
    with pytest.raises((fp.ProgramError, OSError)) as caught:
        gate.program.submit(state, argparse.Namespace(id="S01", owner="worker", receipt=name))
    # Then the exact authoritative first reason matches, with caller state preserved.
    assert result["status"] == "failed" and result["reasons"] == [str(caught.value)]
    assert state == before


def test_explicit_reliance_validates_real_submission_and_immutable_review(
    gate: GateFixture,
) -> None:
    # Given one explicit current task/criterion link and a real execution input.
    gate.ready()
    assert gate.fixture.claim() == 0
    value = draft(gate)
    link = value["links"][0]
    link.update(
        {
            "scope_kind": "attempt",
            "attempt": 1,
            "evidence": [
                {
                    "path": gate.fixture.evidence("S01"),
                    "sha256": fp.sha(
                        gate.program.path("evidence/S01/attempt-1/inspection.txt").read_bytes()
                    ),
                }
            ],
        }
    )
    ref = record_annotation(gate, value)
    name = gate.fixture.receipt()
    execution = fp.read_json(gate.program.path(name))
    execution["coverage_refs"] = reliance(gate, ref)
    gate.fixture.write(name, execution)
    assert gate.fixture.run("submit", "S01", "--owner", "worker", "--receipt", name) == 0
    execution_bytes = gate.program.path(name).read_bytes()
    # When new review reliance names the now-stale pre-submission round binding.
    review_name = gate.fixture.review()
    review = fp.read_json(gate.program.path(review_name))
    review["coverage_refs"] = reliance(gate, ref)
    gate.fixture.write(review_name, review)
    before = gate.program.path("backlog.json").read_bytes()
    assert gate.fixture.run("complete", "S01", "--review", review_name) == 1
    assert gate.program.path("backlog.json").read_bytes() == before
    # Then a fresh round-bound annotation allows review without rewriting execution history.
    value["links"][0]["round"] = 1
    ref = record_annotation(gate, value, "coverage-round-1")
    review["coverage_refs"] = reliance(gate, ref)
    gate.fixture.write(review_name, review)
    assert gate.fixture.run("complete", "S01", "--review", review_name) == 0
    assert gate.program.path(name).read_bytes() == execution_bytes
    assert gate.fixture.run("check") == 0


@pytest.mark.parametrize("fault", ["hash", "criterion", "empty", "duplicate", "task", "unsafe"])
def test_reliance_rejects_explicit_invalid_envelope(gate: GateFixture, fault: str) -> None:
    # Given a valid source-bound annotation and newly declared reliance.
    ref = record_annotation(gate)
    value = {"coverage_refs": reliance(gate, ref)}
    match fault:
        case "hash":
            value["coverage_refs"][0]["receipt"]["sha256"] = "0" * 64
        case "criterion":
            value["coverage_refs"][0]["criteria"][0]["ordinal"] = False
        case "empty":
            value["coverage_refs"][0]["criteria"] = []
        case "duplicate":
            value["coverage_refs"].append(deepcopy(value["coverage_refs"][0]))
        case "task":
            pass
        case "unsafe":
            value["coverage_refs"][0]["receipt"]["path"] = "../outside.json"
        case _:
            pytest.fail("Unknown fixture fault")
    before = gate.program.path("backlog.json").read_bytes()
    # When a real shared validator checks the explicit reference, not an inferred prose reference.
    with pytest.raises(fp.coordination.GovernanceError):
        fp.coverage.validate_receipt_refs(
            gate.program, gate.program.load(), value, "S02" if fault == "task" else "S01"
        )
    # Then no legacy criteria or receipt/state was rewritten.
    assert gate.program.path("backlog.json").read_bytes() == before


def test_snapshot_and_cli_reads_have_no_lock_git_or_filesystem_effects(
    gate: GateFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given unowned planned tasks with absent legacy linkage, then a real annotation.
    state = gate.program.load()
    assert all(task["owner"] is None for task in state["tasks"])
    assert all(
        row["knowledge"] == "unknown"
        for row in fp.coverage.projection(gate.program, state)["criteria"]
    )
    record_annotation(gate)
    before = {
        path.relative_to(gate.program.directory).as_posix(): path.read_bytes()
        for path in gate.program.directory.rglob("*")
        if path.is_file()
    }
    monkeypatch.setattr(fp, "git", lambda *_args: pytest.fail("Read observation called Git"))
    monkeypatch.setattr(
        fp.Program, "lock", lambda *_args, **_kwargs: pytest.fail("Read observation acquired lock")
    )
    # When both read-only CLI and the dashboard snapshot consume the same projection.
    assert gate.fixture.run("coverage") == 0
    snapshot = fp.read_snapshot(gate.program.repo, gate.program.directory)
    assert snapshot["coverage"]["criteria"][0]["linkage"] == "linked"
    # Then no planned-owner error or imaginary passing validation was created.
    after = {
        path.relative_to(gate.program.directory).as_posix(): path.read_bytes()
        for path in gate.program.directory.rglob("*")
        if path.is_file()
    }
    assert after == before


@pytest.mark.parametrize("boundary", ["source", "evidence", "revision", "atomic", "render"])
def test_record_publication_preserves_failure_and_truthful_recovery(
    gate: GateFixture, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    # Given a complete annotation input and a real injected publication boundary.
    value = draft(gate)
    value.update(fp.coverage.bind_input(gate.program, gate.program.load(), value))
    name = gate.fixture.write("evidence/_coverage/attempt-1/boundary.json", value)
    before = gate.program.path("backlog.json").read_bytes()
    original = fp.Program.views

    def changed_views(program: fp.Program, state: fp.Record) -> dict[str, str]:
        result = original(program, state)
        match boundary:
            case "source":
                (program.repo / "src/S01.py").write_text("raced = True\n", encoding="utf-8")
            case "evidence":
                program.path("evidence/_coverage/attempt-1/observation.txt").write_text(
                    "raced", encoding="utf-8"
                )
            case "revision":
                current = fp.read_json(program.path("backlog.json"))
                current["runtime_observation"] = "Concurrent fixture progress"
                fp.atomic_write(program.path("backlog.json"), fp.canonical(current))
            case _:
                pytest.fail("Unexpected view boundary")
        return result

    match boundary:
        case "source" | "evidence" | "revision":
            monkeypatch.setattr(fp.Program, "views", changed_views)
        case "atomic":
            monkeypatch.setattr(
                fp,
                "atomic_write",
                lambda *_args: (_ for _ in ()).throw(OSError("Actual fixture atomic failure")),
            )
        case "render":
            monkeypatch.setattr(
                fp.Program,
                "render",
                lambda *_args: (_ for _ in ()).throw(OSError("Actual fixture render failure")),
            )
        case _:
            pytest.fail("Unknown publication boundary")
    # When actual recording reaches save/precommit/render.
    assert gate.fixture.run("coverage-record", "--input", name) == 1
    persisted = fp.read_json(gate.program.path("backlog.json"))
    assert bool(persisted.get("coverage_annotations")) == (boundary == "render")
    if boundary not in {"revision", "render"}:
        assert gate.program.path("backlog.json").read_bytes() == before
    if boundary == "render":
        monkeypatch.undo()
        assert gate.fixture.run("render") == 0
        committed = gate.program.path("backlog.json").read_bytes()
        assert gate.fixture.run("coverage-record", "--input", name) == 1
        assert gate.program.path("backlog.json").read_bytes() == committed


def test_actual_membership_remove_readd_cannot_reuse_annotation(gate: GateFixture) -> None:
    # Given current explicit coverage before exact reviewed membership amendments.
    gate.ready()
    ref = record_annotation(gate)
    original = gate.program.path(ref["path"]).read_bytes()
    name, _ = reviewed_input(gate, membership(gate, ["S01"], "coverage-exclude"))
    assert gate.fixture.run("objective-membership", "--input", name) == 0
    name, _ = reviewed_input(gate, membership(gate, ["S01", "S02"], "coverage-readd"))
    # When the original task IDs return through a distinct reviewed revision.
    assert gate.fixture.run("objective-membership", "--input", name) == 0
    state = gate.program.load()
    view = fp.coverage.projection(gate.program, state)
    # Then unchanged IDs cannot conceal the membership change or rewrite old evidence.
    assert state["objective"]["membership_revision"] == 2
    assert view["history"][0]["freshness"] == "stale"
    with pytest.raises(fp.coverage.CoverageError):
        fp.coverage.validate_refs(gate.program, state, {"coverage_refs": reliance(gate, ref)})
    assert gate.program.path(ref["path"]).read_bytes() == original
    assert gate.fixture.run("check") == 0


def integrated_input(gate: GateFixture) -> str:
    # Real temporary CLI state; supplied CI/provider observations are explicitly synthetic.
    fixture = gate.fixture
    for identifier in ("S01", "S02"):
        assert fixture.claim(identifier) == 0
        fixture.submit(identifier)
        assert fixture.run("complete", identifier, "--review", fixture.review(identifier)) == 0
        delivery = gate.program.template(
            gate.program.load(), argparse.Namespace(id=identifier, kind="delivery")
        )
        delivery.update(
            {
                "branch": "synthetic/work",
                "pr_url": "https://example.invalid/pr/1",
                "required_ci": ["synthetic-tests"],
                "ci": [
                    {
                        "name": "synthetic-tests",
                        "url": "https://example.invalid/ci/1",
                        "commit": delivery["tested_commit"],
                        "conclusion": "success",
                    }
                ],
                "evidence": [fixture.evidence(identifier, label="synthetic-delivery")],
            }
        )
        path = fixture.write(f"evidence/{identifier}/attempt-1/delivery.json", delivery)
        assert fixture.run("delivery", identifier, "--receipt", path) == 0
    state = gate.program.load()
    scope = ["src/S01.py", "src/S02.py"]
    bound = {
        "source_head": state["baseline_head"],
        "scope": scope,
        "source_digest": gate.program.source_digest(scope),
        "objective_id": state["objective"]["id"],
    }
    proof = fixture.evidence("_coordination", label="synthetic-integrated-proof")
    review = {
        **bound,
        "reviewer": "integrated-reviewer",
        "reviewer_role": "independent",
        "verdict": "accepted",
        "acceptance_complete": True,
        "criteria": deepcopy(state["objective"]["criteria"]),
        "unresolved_findings": [],
        "evidence": [proof],
    }
    review_name = fixture.write("evidence/_coordination/attempt-1/integrated-review.json", review)
    acceptance = []
    for identifier in ("S01", "S02"):
        task = gate.program.task(state, identifier)
        attempt = gate.program.current_attempt(task)
        acceptance.append(
            {
                "task_id": identifier,
                "attempt": attempt["number"],
                "source_digest": attempt["submission"]["source_digest"],
                "review_sha256": attempt["review"]["sha256"],
                "delivery_sha256": gate.program.current_delivery(task)["receipt"]["sha256"],
            }
        )
    completion = {
        **bound,
        "actor_id": "director",
        "summary": "Actual isolated synthetic acceptance scenario",
        "unresolved_findings": [],
        "criteria_results": [
            {"criterion": text, "status": "passed", "evidence": [proof]}
            for text in state["objective"]["criteria"]
        ],
        "evidence": [proof],
        "remote_state_checked": True,
        "required_ci": ["synthetic-tests"],
        "task_acceptance": acceptance,
        "ci": [
            {
                "name": "synthetic-tests",
                "url": "https://example.invalid/integrated-ci",
                "commit": bound["source_head"],
                "conclusion": "success",
            }
        ],
        "integrated_review": review_name,
    }
    return fixture.write("evidence/_coordination/attempt-1/completion.json", completion)


def test_recorded_integrated_acceptance_is_current_without_invented_check_mapping(gate):
    # Given actual ordinary terminal/delivery records and a recorded independent aggregate.
    gate.ready()
    name = integrated_input(gate)
    assert gate.fixture.run("objective-complete", "--input", name) == 0
    before = gate.program.path("backlog.json").read_bytes()
    # When the recorded proof is assessed through the same ordinary guards.
    result = fp.coverage.assess_integrated_completion(
        gate.program, gate.program.load(), observed_head=gate.program.current_head()
    )
    # Then member acceptance is current, while absent check mappings remain unassessed.
    assert result["status"] == "accepted" and result["currentness"] == "current"
    assert result["counts"]["accepted_members"] == 2
    assert all(row["status"] == "unmapped" for row in result["obligations"])
    assert result["counts"]["fulfilled"] == 0
    assert gate.program.path("backlog.json").read_bytes() == before


def test_unfinished_work_without_aggregate_does_not_create_missing_lane_actions(gate):
    gate.ready()
    assert gate.fixture.claim() == 0
    result = fp.coverage.assess_integrated_completion(gate.program, gate.program.load())
    assert result["status"] == "not_assessed"
    assert result["reason_codes"] == ["completion_absent"]
    assert result["counts"]["members"] == 2
    assert result["counts"]["obligations"] == 6
    assert result["counts"]["actionable"] == 0
    assert all(
        row["status"] == "unmapped" and not row["actionable"] for row in result["obligations"]
    )


def recorded_checks(gate, **request_fields):
    """Record synthetic facts using normal requests/observations on terminal fixture members."""
    from tools.tests.test_feature_program_verification import request, observation, record

    result = []
    for identifier in ("S01", "S02"):
        scope = [f"src/{identifier}.py"]
        request_id = f"request-{identifier}"
        record(
            gate.fixture,
            request(
                gate.fixture,
                request_id,
                task_id=identifier,
                actor_id="director",
                input_scope=scope,
                evidence_destination=f"evidence/{identifier}/attempt-1/",
                **request_fields,
            ),
        )
        digest = fp.digest(gate.program.source_snapshot(scope))
        result.append(
            record(
                gate.fixture,
                observation(
                    gate.fixture,
                    f"finished-{identifier}",
                    task_id=identifier,
                    request_id=request_id,
                    input_before=digest,
                    input_after=digest,
                    artifacts=[gate.fixture.evidence(identifier)],
                ),
            )
        )
    return result


def mapped_review(gate, name, observation):
    state = gate.program.load()
    completion = fp.read_json(gate.program.path(name))
    review_name = completion["integrated_review"]
    review = fp.read_json(gate.program.path(review_name))
    request = next(
        item
        for item in state["verification"]["records"]
        if item["input"]["id"] == observation["request_id"]
    )
    submission = gate.program.current_attempt(gate.program.task(state, "S01"))["submission"]
    review["obligation_coverage"] = {
        "schema_version": 1,
        "items": [
            {
                "criterion": {
                    "ordinal": 0,
                    "text_sha256": fp.sha(state["objective"]["criteria"][0].encode()),
                },
                "task_id": "S01",
                "attempt": 1,
                "check_id": "acceptance",
                "submission": {
                    key: submission[key] for key in ("round", "receipt", "source_digest")
                },
                "request_ref": request["receipt"],
                "observation_ref": observation["receipt"],
                "context_digest": fp.digest(request["context"]),
            }
        ],
    }
    gate.fixture.write(review_name, review)
    return review_name, review


def test_integrated_mapping_fulfills_only_named_obligation_and_retains_exact_refs(gate):
    gate.ready()
    name = integrated_input(gate)
    observations = recorded_checks(gate)
    mapped_review(gate, name, observations[0])
    assert gate.fixture.run("objective-complete", "--input", name) == 0
    state = gate.program.load()
    result = fp.coverage.assess_integrated_completion(
        gate.program, state, observed_head=gate.program.current_head()
    )
    fulfilled = [row for row in result["obligations"] if row["status"] == "fulfilled"]
    assert [(row["task_id"], row["check_id"]) for row in fulfilled] == [("S01", "acceptance")]
    assert observations[0]["receipt"] in state["objective"]["evidence"]
    assert all(
        row["basis"] != "integrated_mapping"
        for row in result["obligations"]
        if row["task_id"] == "S02"
    )


@pytest.mark.parametrize(
    "fault",
    [
        "criterion",
        "attempt",
        "check",
        "submission",
        "context",
        "environment",
        "undeclared_environment",
        "incomplete_inputs",
    ],
)
def test_invalid_integrated_mapping_cannot_authorize_completion(gate, fault):
    gate.ready()
    name = integrated_input(gate)
    request_fields = (
        {"environment": None}
        if fault == "undeclared_environment"
        else {"input_complete": False}
        if fault == "incomplete_inputs"
        else {}
    )
    observations = recorded_checks(gate, **request_fields)
    review_name, review = mapped_review(gate, name, observations[0])
    mapping = review["obligation_coverage"]["items"][0]
    match fault:
        case "criterion":
            mapping["criterion"]["text_sha256"] = "0" * 64
        case "attempt":
            mapping["attempt"] = 2
        case "check":
            mapping["check_id"] = "native_editor"
        case "submission":
            mapping["submission"]["source_digest"] = "0" * 64
        case "context":
            mapping["context_digest"] = "0" * 64
        case "environment":
            from tools.tests.test_feature_program_verification import observation, record

            record(
                gate.fixture,
                observation(
                    gate.fixture,
                    "incompatible-environment",
                    request_id="request-S01",
                    environment="different-runtime",
                ),
            )
        case "undeclared_environment" | "incomplete_inputs":
            pass
        case _:
            pytest.fail("Unknown fault")
    gate.fixture.write(review_name, review)
    before = gate.program.path("backlog.json").read_bytes()
    assert gate.fixture.run("objective-complete", "--input", name) == 1
    assert gate.program.path("backlog.json").read_bytes() == before


def test_historical_execution_is_contextualized_but_later_request_stays_actionable(gate):
    from tools.tests.test_feature_program_verification import request, record

    gate.ready()
    name = integrated_input(gate)
    recorded_checks(gate)
    # The aggregate actually reviews changed source, while the old executed records stay immutable.
    (gate.program.repo / "src/S01.py").write_text("updated = True\n", encoding="utf-8")
    receipt = fp.read_json(gate.program.path(name))
    receipt["source_digest"] = gate.program.source_digest(receipt["scope"])
    gate.fixture.write(name, receipt)
    review = fp.read_json(gate.program.path(receipt["integrated_review"]))
    review["source_digest"] = receipt["source_digest"]
    gate.fixture.write(receipt["integrated_review"], review)
    assert gate.fixture.run("objective-complete", "--input", name) == 0
    state = gate.program.load()
    facts = fp.verification.projection(gate.program, state, limit=None)
    result = fp.coverage.assess_integrated_completion(gate.program, state, verification_facts=facts)
    assert result["status"] == "accepted"
    assert result["counts"]["historical"] == 6
    assert facts["tasks"]["S01"]["lanes"]["items"][0]["applicability"] == "stale"
    assert facts["tasks"]["S01"]["lanes"]["items"][0]["result"] == "passed"
    identity = dict(fp.record(result["basis"], "actual basis"))["identity"]
    # Unrelated canonical bookkeeping does not change semantic acceptance identity.
    state["tasks"][0]["next_action"] = "A later note"
    result = fp.coverage.assess_integrated_completion(gate.program, state)
    assert dict(fp.record(result["basis"], "actual basis"))["identity"] == identity
    record(
        gate.fixture,
        request(gate.fixture, "fresh-request", actor_id="director", supersedes="request-S01"),
    )
    result = fp.coverage.assess_integrated_completion(gate.program, gate.program.load())
    assert result["status"] != "accepted"
    assert any(row["status"] == "pending" and row["actionable"] for row in result["obligations"])
    assert result["next_actor_id"] == "qa"
    assert next(row for row in result["members"] if row["task_id"] == "S02")["status"] == "accepted"


@pytest.mark.parametrize(
    "fault", ["source", "head", "membership", "criteria", "attempt", "review", "evidence"]
)
def test_recorded_aggregate_never_covers_changed_subject_or_corrupt_proof(gate, fault):
    gate.ready()
    name = integrated_input(gate)
    assert gate.fixture.run("objective-complete", "--input", name) == 0
    state = gate.program.load()
    head = gate.program.current_head()
    match fault:
        case "source":
            (gate.program.repo / "src/S01.py").write_text("source = 2\n", encoding="utf-8")
        case "head":
            head = "b" * 40
        case "membership":
            state["objective"]["membership_revision"] = 1
        case "criteria":
            state["objective"]["criteria"].append("A new actual obligation")
        case "attempt":
            state["tasks"][0]["attempts"][-1]["number"] = 2
        case "review":
            gate.program.path(state["objective"]["integrated_review_ref"]["path"]).write_text(
                "{}", encoding="utf-8"
            )
        case "evidence":
            proof = state["objective"]["completion"]["evidence"][0]
            gate.program.path(proof).write_text("changed proof", encoding="utf-8")
        case _:
            pytest.fail("Unknown fault")
    result = fp.coverage.assess_integrated_completion(gate.program, state, observed_head=head)
    assert result["status"] != "accepted"
    assert result["counts"]["actionable"] > 0
    assert result["counts"]["fulfilled"] == 0


@pytest.mark.parametrize("fault", ["source", "nested_evidence", "head"])
def test_integrated_assessment_closes_source_and_evidence_observation(gate, monkeypatch, fault):
    gate.ready()
    name = integrated_input(gate)
    assert gate.fixture.run("objective-complete", "--input", name) == 0
    state = gate.program.load()
    original = fp.coordination.assess_objective_completion

    def mutate_after_actual_proof(*args, **kwargs):
        proof = original(*args, **kwargs)
        if fault == "head":
            monkeypatch.setattr(fp.Program, "current_head", lambda self: "b" * 40)
            return proof
        target = (
            gate.program.repo / "src/S01.py"
            if fault == "source"
            else gate.program.path(state["objective"]["completion"]["evidence"][0])
        )
        target.write_bytes(b"changed during observation\n")
        return proof

    monkeypatch.setattr(fp.coordination, "assess_objective_completion", mutate_after_actual_proof)
    with pytest.raises((fp.ProgramError, fp.coverage.CoverageError)):
        fp.coverage.assess_integrated_completion(gate.program, state)


@pytest.mark.parametrize("kind", ["failed", "blocked", "canceled", "unavailable"])
def test_later_terminal_member_obligation_is_actionable_despite_accepted_aggregate(gate, kind):
    from tools.tests.test_feature_program_verification import request, observation, record

    gate.ready()
    name = integrated_input(gate)
    assert gate.fixture.run("objective-complete", "--input", name) == 0
    record(gate.fixture, request(gate.fixture, actor_id="director"))
    fields = {"result": "failed"} if kind == "failed" else {}
    if kind in {"blocked", "canceled"}:
        fields.update(kind=kind)
        if kind == "blocked":
            fields["blocker"] = {
                "reason": "Actual synthetic resource block",
                "resolver_id": "qa",
                "next_trigger": "Resource available",
            }
        else:
            fields["next_trigger"] = "Explicit new check"
    value = observation(gate.fixture, **fields)
    if kind in {"blocked", "canceled"}:
        for key in ("result", "method", "environment", "input_before", "input_after"):
            value.pop(key)
    event = record(gate.fixture, value)
    state = gate.program.load()
    if kind == "unavailable":
        gate.program.path(event["input"]["artifacts"][0]).write_bytes(b"changed artifact")
        with pytest.raises(fp.ProgramError, match="Frozen evidence changed"):
            gate.program.load()
    result = fp.coverage.assess_integrated_completion(gate.program, state)
    assert result["status"] != "accepted"
    assert any(row["task_id"] == "S01" and row["actionable"] for row in result["obligations"])
    assert result["next_actor_id"] == "qa"
    assert result["counts"]["accepted_members"] == 1


@pytest.mark.parametrize("legacy_recorded", [False, True])
def test_required_provider_pending_blocks_completion_and_historical_coverage(gate, legacy_recorded):
    from tools.tests.test_feature_program_verification import (
        request,
        observation,
        record,
        provider_pair,
    )

    gate.ready()
    name = integrated_input(gate)
    context_path, _provider_ref, _expected = provider_pair(gate.fixture)
    record(
        gate.fixture,
        request(
            gate.fixture, actor_id="director", lane="pr_ci", provider_context_path=context_path
        ),
    )
    record(gate.fixture, observation(gate.fixture))
    before = gate.program.path("backlog.json").read_bytes()
    state = gate.program.load()
    if not legacy_recorded:
        assert gate.fixture.run("objective-complete", "--input", name) == 1
        assert gate.program.path("backlog.json").read_bytes() == before
        return
    # Model the previously permitted persisted shape on detached fixture state only.
    # The current ordinary publisher must not create this legacy acceptance.
    receipt = fp.read_json(gate.program.path(name))
    proof = fp.coordination.assess_objective_completion(
        gate.program,
        state,
        receipt,
        name,
        fp.sha(gate.program.path(name).read_bytes()),
        observed_head=gate.program.current_head(),
    )
    state["objective"].update(
        status="awaiting_next_goal",
        completion=deepcopy(receipt),
        completed_at=fp.utc(),
        next_action_owner="user",
        evidence=proof["evidence_refs"] + proof["integrated_review_refs"],
        integrated_review_ref=proof["integrated_review_refs"][0],
    )
    gate.program.event(state, "objective-complete", {"receipt": deepcopy(receipt)})
    result = fp.coverage.assess_integrated_completion(gate.program, state)
    assert result["status"] == "not_accepted" and result["currentness"] == "current"
    rows = [row for row in result["obligations"] if row["task_id"] == "S01"]
    assert all(row["status"] == "pending" and row["actionable"] for row in rows)
    assert all(row["reason_codes"] == ["provider_execution_pending"] for row in rows)
    assert result["next_actor_id"] == "qa"
    # Missing capture must not hide a separate later integrity failure either.
    artifact = state["verification"]["records"][-1]["input"]["artifacts"][0]
    gate.program.path(artifact).write_bytes(b"changed local evidence")
    result = fp.coverage.assess_integrated_completion(gate.program, state)
    rows = [row for row in result["obligations"] if row["task_id"] == "S01"]
    assert all(row["status"] == "unavailable" for row in rows)
    assert all(row["reason_codes"] == ["verification_unavailable"] for row in rows)
    assert gate.program.path("backlog.json").read_bytes() == before


def test_captured_provider_pass_keeps_qualified_historical_member_basis(gate):
    from tools.tests.test_feature_program_verification import (
        request,
        observation,
        record,
        provider_pair,
    )

    gate.ready()
    name = integrated_input(gate)
    context_path, provider_ref, _expected = provider_pair(gate.fixture)
    record(
        gate.fixture,
        request(
            gate.fixture, actor_id="director", lane="pr_ci", provider_context_path=context_path
        ),
    )
    record(gate.fixture, observation(gate.fixture, provider_ref=provider_ref))
    (gate.program.repo / "src/S01.py").write_text("reviewed_later = True\n", encoding="utf-8")
    receipt = fp.read_json(gate.program.path(name))
    receipt["source_digest"] = gate.program.source_digest(receipt["scope"])
    gate.fixture.write(name, receipt)
    review = fp.read_json(gate.program.path(receipt["integrated_review"]))
    review["source_digest"] = receipt["source_digest"]
    gate.fixture.write(receipt["integrated_review"], review)
    assert gate.fixture.run("objective-complete", "--input", name) == 0
    state = gate.program.load()
    facts = fp.verification.projection(gate.program, state, limit=None)
    provider = facts["tasks"]["S01"]["lanes"]["items"][0]["provider_execution"]
    assert provider["status"] == "not_assessed" and provider["recorded_status"] == "passed"
    assert provider["current"] is False
    result = fp.coverage.assess_integrated_completion(gate.program, state, verification_facts=facts)
    assert result["status"] == "accepted"
    assert all(
        row["status"] == "historical_only" and not row["actionable"]
        for row in result["obligations"]
        if row["task_id"] == "S01"
    )


@pytest.mark.parametrize("fault", [None, "reviewer", "criteria", "source", "coverage", "delivery"])
def test_integrated_diagnostics_share_current_acceptance_and_independence(
    gate: GateFixture, fault: str | None
) -> None:
    # Given actual reviewed member attempts/delivery and an independent integrated input.
    gate.ready()
    name = integrated_input(gate)
    receipt = fp.read_json(gate.program.path(name))
    review_name = receipt["integrated_review"]
    review = fp.read_json(gate.program.path(review_name))
    match fault:
        case None:
            ref = record_annotation(gate)
            receipt["coverage_refs"] = reliance(gate, ref)
            review["coverage_refs"] = reliance(gate, ref)
        case "reviewer":
            review["reviewer"] = "worker"
        case "criteria":
            review["criteria"] = []
        case "source":
            review["source_digest"] = "0" * 64
        case "coverage":
            receipt["coverage_refs"] = [
                {
                    "receipt": {
                        "path": "evidence/_coverage/attempt-1/unknown.json",
                        "sha256": "0" * 64,
                    },
                    "criteria": [
                        {
                            "ordinal": 0,
                            "text_sha256": fp.sha(
                                gate.program.load()["objective"]["criteria"][0].encode()
                            ),
                        }
                    ],
                }
            ]
        case "delivery":
            receipt["task_acceptance"][0]["delivery_sha256"] = "0" * 64
        case _:
            pytest.fail("Unknown aggregate fault")
    gate.fixture.write(name, receipt)
    gate.fixture.write(review_name, review)
    before = gate.program.path("backlog.json").read_bytes()
    args = argparse.Namespace(operation="objective-complete", input=name, task=None, owner=None)
    # When pure shared preparation predicts the actual completion validator's result.
    diagnostic = fp.coverage.diagnostics(
        gate.program, gate.program.load(), args, observed_head=gate.program.current_head()
    )
    assert gate.program.path("backlog.json").read_bytes() == before
    if fault is None:
        assert diagnostic["status"] == "validated"
        assert gate.fixture.run("objective-complete", "--input", name) == 0
        recorded = fp.coverage.projection(gate.program, gate.program.load())[
            "recorded_objective_acceptance"
        ]
        assert (
            recorded["status"] == "recorded"
            and recorded["criteria_results"][0]["status"] == "passed"
        )
        assert recorded["head_freshness"] == "not_assessed"
    else:
        assert diagnostic["status"] == "failed"
        captured, parsed_hash = fp.coordination._load(gate.program, name)
        with pytest.raises((fp.ProgramError, fp.coordination.GovernanceError)) as caught:
            fp.coordination.prepare_objective_completion(
                gate.program,
                gate.program.load(),
                captured,
                name,
                parsed_hash,
                observed_head=gate.program.current_head(),
            )
        assert diagnostic["reasons"] == [str(caught.value)]
        assert gate.fixture.run("objective-complete", "--input", name) == 1
        assert gate.program.path("backlog.json").read_bytes() == before


def test_fresh_interpreter_cli_records_reads_and_rejects_stale_binding(gate: GateFixture) -> None:
    # Given a real authored input and a fresh interpreter launched from CI's Server directory.
    gate.ready()
    value = draft(gate)
    name = gate.fixture.write("evidence/_coverage/attempt-1/standalone.json", value)
    launcher = f"import sys; sys.path.insert(0, {str(fp.ROOT / 'tools')!r}); import feature_program as fp; fp.git=lambda root,args: '' if args[0]=='status' else {'a' * 40!r}; raise SystemExit(fp.main(sys.argv[1:]))"

    def invoke(*args: str) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "-c",
                launcher,
                "--repo",
                str(gate.program.repo),
                "--program-dir",
                gate.program.directory.relative_to(gate.program.repo).as_posix(),
                *args,
            ],
            cwd=fp.ROOT / "Server",
            capture_output=True,
            text=True,
            check=False,
        )
        print("CLI", args, "exit", result.returncode, result.stdout, result.stderr)
        return result

    result = invoke("coverage-binding", "--input", name)
    assert result.returncode == 0
    value.update(json.loads(result.stdout))
    gate.fixture.write(name, value)
    # When the public standalone command records then projects the source-bound annotation.
    assert invoke("coverage-record", "--input", name).returncode == 0
    result = invoke("coverage")
    assert (
        result.returncode == 0 and json.loads(result.stdout)["criteria"][0]["linkage"] == "linked"
    )
    before = gate.program.path("backlog.json").read_bytes()
    # Then exact old pre-state reuse cannot publish another annotation.
    assert invoke("coverage-record", "--input", name).returncode == 1
    assert gate.program.path("backlog.json").read_bytes() == before


@pytest.mark.parametrize("drift", ["source", "receipt", "revision"])
def test_explicit_reliance_rechecks_after_preflight_before_write(
    gate: GateFixture, monkeypatch: pytest.MonkeyPatch, drift: str
) -> None:
    # Given actual active source and a complete new submission relying on a current annotation.
    gate.ready()
    assert gate.fixture.claim() == 0
    ref = record_annotation(gate)
    name = gate.fixture.receipt()
    value = fp.read_json(gate.program.path(name))
    value["coverage_refs"] = reliance(gate, ref)
    gate.fixture.write(name, value)
    before = gate.program.path("backlog.json").read_bytes()
    original = fp.Program.views

    def changed(program: fp.Program, state: fp.Record) -> dict[str, str]:
        result = original(program, state)
        match drift:
            case "source":
                (program.repo / "src/S01.py").write_text("actual raced source\n", encoding="utf-8")
            case "receipt":
                receipt = fp.read_json(program.path(name))
                receipt["summary"] = "Changed during preflight"
                program.path(name).write_bytes(fp.canonical(receipt))
            case "revision":
                existing = fp.read_json(program.path("backlog.json"))
                existing["runtime_observation"] = "Actual concurrent update"
                fp.atomic_write(program.path("backlog.json"), fp.canonical(existing))
            case _:
                pytest.fail("Unknown preflight drift")
        return result

    monkeypatch.setattr(fp.Program, "views", changed)
    # When the real CLI reaches publication after actual derived preparation changed a binding.
    assert gate.fixture.run("submit", "S01", "--owner", "worker", "--receipt", name) == 1
    persisted = fp.read_json(gate.program.path("backlog.json"))
    # Then no submission was persisted; any injected concurrent observation remains intact.
    assert gate.program.task(persisted, "S01")["status"] == "active"
    if drift != "revision":
        assert gate.program.path("backlog.json").read_bytes() == before
    else:
        assert persisted["runtime_observation"] == "Actual concurrent update"


def test_pure_preparation_uses_explicit_head_without_git_or_publication(
    gate: GateFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given actual owned work and a genuinely captured metadata observation.
    gate.ready()
    assert gate.fixture.claim() == 0
    name = gate.fixture.receipt()
    state = gate.program.load()
    observed = gate.program.current_head()
    before = {
        path.relative_to(gate.program.directory).as_posix(): path.read_bytes()
        for path in gate.program.directory.rglob("*")
        if path.is_file()
    }
    monkeypatch.setattr(fp, "git", lambda *_args: pytest.fail("Pure preparation read Git"))
    monkeypatch.setattr(
        fp.Program, "lock", lambda *_args, **_kwargs: pytest.fail("Pure preparation locked")
    )
    monkeypatch.setattr(
        fp.Program, "save", lambda *_args, **_kwargs: pytest.fail("Pure preparation published")
    )
    args = argparse.Namespace(operation="submit", input=name, task="S01", owner="worker")
    # When pure diagnostics reuse actual semantic/save preflight with explicit observed metadata.
    result = fp.coverage.diagnostics(gate.program, state, args, observed_head=observed)
    assert result["status"] == "validated"
    unavailable = fp.coverage.diagnostics(gate.program, state, args)
    # Then missing metadata is not_assessed, and there are no filesystem changes.
    assert unavailable["status"] == "not_assessed"
    after = {
        path.relative_to(gate.program.directory).as_posix(): path.read_bytes()
        for path in gate.program.directory.rglob("*")
        if path.is_file()
    }
    assert before == after


def test_missing_current_action_is_actual_validator_failure_without_owner_heuristic(
    gate: GateFixture,
) -> None:
    # Given valid intentionally unassigned planned work, then an actually missing required field.
    state = gate.program.load()
    args = argparse.Namespace(operation=None, input=None, task=None, owner=None)
    assert (
        fp.coverage.diagnostics(gate.program, state, args)["structural_validation"]["status"]
        == "validated"
    )
    gate.program.task(state, "S01")["next_action"] = ""
    # When current structure is observed without a hypothetical execution actor/input.
    result = fp.coverage.diagnostics(gate.program, state, args)
    with pytest.raises(fp.ProgramError) as caught:
        gate.program.validate(state)
    # Then the actual required-field error is reported separately from unassessed execution.
    assert result["status"] == "not_assessed" and result["structural_validation"]["reasons"] == [
        str(caught.value)
    ]
    assert result["structural_validation"]["failure_type"] == "ProgramError"


def test_completed_relocation_preserves_annotation_identity_and_bytes(gate: GateFixture) -> None:
    # Given an annotation bound to a frozen source originally located inside its program.
    ref = record_annotation(gate)
    original = gate.program.path(ref["path"]).read_bytes()
    assert (
        gate.fixture.run(
            "relocate",
            "--destination",
            ".work/programs/coverage-moved",
            "--owner",
            "director",
            "--reason",
            "Explicit fixture relocation",
        )
        == 0
    )
    target = fp.Program(gate.program.repo, gate.program.repo / ".work/programs/coverage-moved")
    # When the copied program resolves current safe locators and historical provenance.
    state = target.load()
    view = fp.coverage.projection(target, state)
    # Then content identity stays current and historical receipt bytes were not rewritten.
    assert view["history"][0]["freshness"] == "current"
    assert target.path(ref["path"]).read_bytes() == original
    assert gate.fixture.run("coverage") == 1


@pytest.mark.parametrize(
    "fault",
    ["missing-source", "changed-source", "missing-lineage", "ambiguous-lineage", "changed-archive"],
)
def test_relocation_never_falls_back_from_invalid_provenance(gate: GateFixture, fault: str) -> None:
    # Given a genuinely completed relocation with untouched annotation history.
    ref = record_annotation(gate)
    assert (
        gate.fixture.run(
            "relocate",
            "--destination",
            ".work/programs/coverage-moved",
            "--owner",
            "director",
            "--reason",
            "Explicit fixture relocation",
        )
        == 0
    )
    target = fp.Program(gate.program.repo, gate.program.repo / ".work/programs/coverage-moved")
    state = target.load()
    event = next(event for event in reversed(state["events"]) if event["action"] == "relocate")
    original = target.path(ref["path"]).read_bytes()
    match fault:
        case "missing-source":
            fp.safe_path(target.repo, state["source"]["path"]).unlink()
        case "changed-source":
            fp.safe_path(target.repo, state["source"]["path"]).write_bytes(
                b"Actual changed original report copy"
            )
        case "missing-lineage":
            state["events"] = [entry for entry in state["events"] if entry["action"] != "relocate"]
        case "ambiguous-lineage":
            state["events"].append(deepcopy(event))
        case "changed-archive":
            target.path(
                f"recovery/relocations/{event['original_registry_sha256']}/original-backlog.json"
            ).write_bytes(b"Actual changed archived origin")
        case _:
            pytest.fail("Unknown relocation fault")
    # When coverage observes missing/changed bytes or unprovable historical association.
    view = fp.coverage.projection(target, state)
    # Then it remains explicit stale/unavailable, preserving immutable annotations without guesses.
    assert view["history"][0]["freshness"] in {"stale", "unavailable"}
    assert view["history"][0]["reasons"]
    rows = [
        {
            "receipt": ref,
            "criteria": [
                {"ordinal": 0, "text_sha256": fp.sha(state["objective"]["criteria"][0].encode())}
            ],
        }
    ]
    with pytest.raises(
        (fp.coverage.CoverageError, fp.ProgramError, fp.planning.PlanningError, OSError)
    ):
        fp.coverage.validate_refs(target, state, {"coverage_refs": rows})
    assert target.path(ref["path"]).read_bytes() == original


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "attempt-bool",
        "attempt-float",
        "attempt-stale",
        "round-bool",
        "round-float",
        "round-stale",
    ],
)
def test_actual_attempt_round_binding_is_type_sensitive(
    gate: GateFixture, fault: str | None
) -> None:
    # Given actual claimed/submitted work and exact owned-scope proof.
    gate.ready()
    assert gate.fixture.claim() == 0
    gate.fixture.submit()
    value = draft(gate)
    link = value["links"][0]
    path = gate.fixture.evidence("S01", label="linked-round-proof")
    link.update(
        {
            "scope_kind": "attempt",
            "attempt": 1,
            "round": 1,
            "evidence": [{"path": path, "sha256": fp.sha(gate.program.path(path).read_bytes())}],
        }
    )
    value.update(fp.coverage.bind_input(gate.program, gate.program.load(), value))
    match fault:
        case None:
            pass
        case "attempt-bool":
            link["attempt"] = True
        case "attempt-float":
            link["attempt"] = 1.0
        case "attempt-stale":
            link["attempt"] = 2
        case "round-bool":
            link["round"] = True
        case "round-float":
            link["round"] = 1.0
        case "round-stale":
            link["round"] = 2
        case _:
            pytest.fail("Unknown binding fault")
    # bind_input returns detached links; mutate the actually submitted row explicitly.
    value["links"][0].update({"attempt": link["attempt"], "round": link["round"]})
    name = gate.fixture.write("evidence/_coverage/attempt-1/typed-round.json", value)
    before = gate.program.path("backlog.json").read_bytes()
    # When the real CLI records an exact or invalid JSON-type attempt/round reference.
    assert gate.fixture.run("coverage-record", "--input", name) == (0 if fault is None else 1)
    # Then only the exact integer current identity can publish.
    if fault is not None:
        assert gate.program.path("backlog.json").read_bytes() == before


def test_new_attempt_cannot_inherit_previous_coverage(gate: GateFixture) -> None:
    # Given a frozen attempt-specific link before an explicit release and fresh claim.
    gate.ready()
    assert gate.fixture.claim() == 0
    value = draft(gate)
    path = gate.fixture.evidence("S01")
    value["links"][0].update(
        {
            "scope_kind": "attempt",
            "attempt": 1,
            "evidence": [{"path": path, "sha256": fp.sha(gate.program.path(path).read_bytes())}],
        }
    )
    ref = record_annotation(gate, value)
    original = gate.program.path(ref["path"]).read_bytes()
    assert (
        gate.fixture.run(
            "release",
            "S01",
            "--owner",
            "worker",
            "--reason",
            "Actual fixture replacement",
            "--next-action",
            "Fresh claim",
        )
        == 0
    )
    # When the current source is freshly claimed as attempt two.
    assert gate.fixture.claim(number=2) == 0
    view = fp.coverage.projection(gate.program, gate.program.load())
    # Then the old link stays historical even with unchanged source bytes and owner.
    assert view["history"][0]["freshness"] == "stale"
    assert gate.program.path(ref["path"]).read_bytes() == original


@pytest.mark.parametrize("drift", ["changed", "missing"])
def test_explicit_uncovered_inventory_verifies_actual_frozen_report(
    gate: GateFixture, drift: str
) -> None:
    # Given an explicit zero-contributor declaration with no task link reading the report.
    gate.ready()
    value = draft(gate)
    value["uncovered"] = [
        {"criterion": value["links"][0]["criterion"], "reason": "No contributors"}
    ]
    value["links"] = []
    record_annotation(gate, value)
    state = gate.program.load()
    report = fp.safe_path(gate.program.repo, state["source"]["path"])
    before = gate.program.path("backlog.json").read_bytes()
    # When actual source bytes become different or unavailable without rewriting their descriptor.
    if drift == "changed":
        report.write_bytes(report.read_bytes() + b"\nActual frozen report drift\n")
    else:
        report.unlink()
    view = fp.coverage.projection(gate.program, state)
    # Then a stale/unavailable observation cannot still declare current uncovered knowledge.
    assert view["history"][0]["freshness"] == ("stale" if drift == "changed" else "unavailable")
    assert view["criteria"][0]["knowledge"] == "unknown"
    assert gate.program.path("backlog.json").read_bytes() == before


def sized_receipt(gate: GateFixture, name: str, size: int, empty_reliance: bool) -> str:
    value = fp.read_json(gate.program.path(name))
    if empty_reliance:
        value["coverage_refs"] = []
    value["limitations"] = [""]
    value["limitations"] = ["x" * (size - len(fp.canonical(value)))]
    gate.fixture.write(name, value)
    assert gate.program.path(name).stat().st_size == size
    return name


@pytest.mark.parametrize("operation", ["submit", "complete"])
@pytest.mark.parametrize("empty_reliance", [False, True], ids=["absent", "empty"])
@pytest.mark.parametrize("size", [1024 * 1024 + 4096, 8 * 1024 * 1024], ids=["above-1MiB", "8MiB"])
def test_legacy_receipt_sizes_agree_between_diagnostics_and_real_transition(
    gate: GateFixture, operation: str, empty_reliance: bool, size: int
) -> None:
    # Given a valid actual receipt within the existing evidence bound and no coverage reliance.
    gate.ready()
    assert gate.fixture.claim() == 0
    if operation == "complete":
        gate.fixture.submit()
        name = gate.fixture.review()
    else:
        name = gate.fixture.receipt()
    sized_receipt(gate, name, size, empty_reliance)
    state = gate.program.load()
    before = gate.program.path("backlog.json").read_bytes()
    args = argparse.Namespace(operation=operation, input=name, task="S01", owner="worker")
    # When shared diagnostic preflight and the actual CLI validate/publish the same input.
    diagnostic = fp.coverage.diagnostics(
        gate.program, state, args, observed_head=gate.program.current_head()
    )
    assert gate.program.path("backlog.json").read_bytes() == before
    result = (
        gate.fixture.run("submit", "S01", "--owner", "worker", "--receipt", name)
        if operation == "submit"
        else gate.fixture.run("complete", "S01", "--review", name)
    )
    # Then permitted legacy bytes reach the authoritative transition without inventing reliance.
    assert (diagnostic["status"], result) == ("validated", 0)
    current = gate.program.load()
    assert gate.program.task(current, "S01")["status"] == (
        "review" if operation == "submit" else "done"
    )
    assert "coverage_annotations" not in current


@pytest.mark.parametrize("operation", ["submit", "complete"])
@pytest.mark.parametrize("empty_reliance", [False, True], ids=["absent", "empty"])
def test_oversized_receipt_rejection_preserves_real_state_and_diagnostic_reason(
    gate: GateFixture, operation: str, empty_reliance: bool
) -> None:
    # Given the same real acceptance surface with evidence one byte beyond its inherited bound.
    gate.ready()
    assert gate.fixture.claim() == 0
    if operation == "complete":
        gate.fixture.submit()
        name = gate.fixture.review()
    else:
        name = gate.fixture.receipt()
    sized_receipt(gate, name, 8 * 1024 * 1024 + 1, empty_reliance)
    state = gate.program.load()
    before = gate.program.path("backlog.json").read_bytes()
    args = argparse.Namespace(operation=operation, input=name, task="S01", owner="worker")
    # When both diagnostic and actual transition encounter the authoritative bounded reader.
    diagnostic = fp.coverage.diagnostics(
        gate.program, state, args, observed_head=gate.program.current_head()
    )
    with pytest.raises(fp.ProgramError) as caught:
        if operation == "submit":
            gate.program.submit(state, argparse.Namespace(id="S01", owner="worker", receipt=name))
        else:
            gate.program.review(
                state, argparse.Namespace(id="S01", command="complete", review=name)
            )
    # Then the actual first failure matches and no canonical/caller state is changed.
    assert diagnostic["status"] == "failed" and diagnostic["reasons"] == [str(caught.value)]
    assert state == gate.program.load()
    assert gate.program.path("backlog.json").read_bytes() == before


def test_large_registry_uses_existing_bound_through_reliance_publication(gate: GateFixture) -> None:
    # Given a valid >1MiB registry and a new receipt that explicitly relies on current coverage.
    gate.ready()
    assert gate.fixture.claim() == 0
    ref = record_annotation(gate)
    state = gate.program.load()
    state["runtime_observation"] = "x" * (1024 * 1024 + 4096)
    gate.program.save(state)
    before = gate.program.path("backlog.json").read_bytes()
    assert 1024 * 1024 < len(before) < 8 * 1024 * 1024
    name = gate.fixture.receipt()
    value = fp.read_json(gate.program.path(name))
    value["coverage_refs"] = reliance(gate, ref)
    gate.fixture.write(name, value)
    args = argparse.Namespace(operation="submit", input=name, task="S01", owner="worker")
    # When the guard reads both initial registry and final revision at actual publication.
    diagnostic = fp.coverage.diagnostics(
        gate.program, state, args, observed_head=gate.program.current_head()
    )
    assert gate.program.path("backlog.json").read_bytes() == before
    assert gate.fixture.run("submit", "S01", "--owner", "worker", "--receipt", name) == 0
    # Then the existing registry capacity remains available without skipping reliance validation.
    assert diagnostic["status"] == "validated"
    current = gate.program.load()
    assert current["runtime_observation"] == state["runtime_observation"]
    assert gate.program.task(current, "S01")["status"] == "review"


@pytest.mark.parametrize("operation", ["submit", "complete", "rework", "objective-complete"])
@pytest.mark.parametrize(
    "fault",
    [
        "next-action",
        "owner",
        "frozen-source",
        "questions",
        "criteria",
        "objective-id",
        "task-id",
        "task-source",
        "required-checks",
    ],
)
def test_supplied_diagnostics_match_cli_load_of_original_invalid_state(
    gate: GateFixture, capsys: pytest.CaptureFixture[str], operation: str, fault: str
) -> None:
    # Given valid actual inputs followed by a required-state fault in their original registry.
    gate.ready()
    if operation == "objective-complete":
        name = integrated_input(gate)
    else:
        assert gate.fixture.claim() == 0
        if operation == "submit":
            name = gate.fixture.receipt()
        else:
            gate.fixture.submit()
            name = gate.fixture.review(accepted=operation == "complete")
    state = gate.program.load()
    task = gate.program.task(state, "S01")
    match fault:
        case "next-action":
            task["next_action"] = ""
        case "owner":
            task["owner"] = ""
        case "frozen-source":
            report = fp.safe_path(gate.program.repo, state["source"]["path"])
            report.write_bytes(report.read_bytes() + b"\nChanged frozen report\n")
        case "questions":
            state["questions"] = {"schema_version": 1, "items": "Invalid inbox array"}
        case "criteria":
            del state["objective"]["criteria"]
        case "objective-id":
            del state["objective"]["id"]
        case "task-id":
            del task["id"]
        case "task-source":
            del task["source"]
        case "required-checks":
            del task["required_checks"]
        case _:
            pytest.fail("Unknown original-state fault")
    gate.program.path("backlog.json").write_bytes(fp.canonical(state))
    before = {
        path: fp.sha(path.read_bytes())
        for path in gate.program.directory.rglob("*")
        if path.is_file()
    }
    with pytest.raises(
        (fp.ProgramError, fp.questions.QuestionError, fp.coordination.GovernanceError)
    ) as expected:
        gate.program.load()
    args = argparse.Namespace(operation=operation, input=name, task="S01", owner="worker")
    capsys.readouterr()
    # When pure diagnostics and the real mutation CLI observe the same captured original state.
    diagnostic = fp.coverage.diagnostics(
        gate.program, state, args, observed_head=gate.program.current_head()
    )
    assert (
        gate.fixture.run(
            "coverage",
            "--operation",
            operation,
            "--input",
            name,
            "--task",
            "S01",
            "--owner",
            "worker",
        )
        == 0
    )
    observed = capsys.readouterr()
    assert not observed.err
    report = json.loads(observed.out)
    assert report["diagnostics"] == diagnostic
    assert report["projection"]["status"] == "unavailable"
    assert report["projection"]["reasons"] == [str(expected.value)]
    assert "criteria" not in report and "tasks" not in report
    if operation == "submit":
        result = gate.fixture.run("submit", "S01", "--owner", "worker", "--receipt", name)
    elif operation == "objective-complete":
        result = gate.fixture.run("objective-complete", "--input", name)
    else:
        result = gate.fixture.run(operation, "S01", "--review", name)
    output = capsys.readouterr()
    # Then the original authoritative error wins before any preparation can repair or mask it.
    assert result == 1 and output.err.strip() == f"feature-program: {expected.value}"
    assert diagnostic["status"] == "failed" and diagnostic["reasons"] == [str(expected.value)]
    assert diagnostic["failure_type"] == type(expected.value).__name__
    after = {
        path: fp.sha(path.read_bytes())
        for path in gate.program.directory.rglob("*")
        if path.is_file()
    }
    assert before == after


@pytest.mark.parametrize("operation", ["submit", "complete", "objective-complete"])
@pytest.mark.parametrize("fault", ["next-action", "criteria"])
def test_invalid_original_state_precedes_unavailable_head_in_cli_diagnostics(
    gate: GateFixture,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    fault: str,
) -> None:
    # Given a required original-state failure and unavailable external metadata.
    gate.ready()
    if operation == "objective-complete":
        name = integrated_input(gate)
    else:
        assert gate.fixture.claim() == 0
        if operation == "submit":
            name = gate.fixture.receipt()
        else:
            gate.fixture.submit()
            name = gate.fixture.review()
    state = gate.program.load()
    if fault == "criteria":
        del state["objective"]["criteria"]
    else:
        gate.program.task(state, "S01")["next_action"] = ""
    gate.program.path("backlog.json").write_bytes(fp.canonical(state))
    before = gate.program.path("backlog.json").read_bytes()
    with pytest.raises((fp.ProgramError, fp.coordination.GovernanceError)) as expected:
        gate.program.load()

    def unavailable(_program: fp.Program) -> str:
        raise fp.ProgramError("Actual fixture HEAD unavailable")

    monkeypatch.setattr(fp.Program, "current_head", unavailable)
    capsys.readouterr()
    # When the actual read-only CLI cannot acquire HEAD before shared semantic assessment.
    assert (
        gate.fixture.run(
            "coverage",
            "--operation",
            operation,
            "--input",
            name,
            "--task",
            "S01",
            "--owner",
            "worker",
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)["diagnostics"]
    # Then unavailable metadata does not hide the original state error or publish a repair.
    assert result["status"] == "failed" and result["reasons"] == [str(expected.value)]
    assert gate.program.path("backlog.json").read_bytes() == before


@pytest.mark.parametrize("surface", ["api", "cli"])
def test_snapshot_rejects_registry_update_during_coverage_source_read(
    gate: GateFixture,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    surface: str,
) -> None:
    # Given actual source-bound coverage whose projection performs scoped-source I/O.
    gate.ready()
    record_annotation(gate)
    original = fp.Program.source_snapshot
    before = gate.program.path("backlog.json").read_bytes()
    concurrent = fp.read_json(gate.program.path("backlog.json"))
    concurrent["runtime_observation"] = "Actual concurrent snapshot-boundary update"
    concurrent_bytes = fp.canonical(concurrent)
    calls = []

    def changed(program: fp.Program, scopes: list[str], budget=None) -> fp.Record:
        observed = original(program, scopes, budget)
        calls.append(scopes)
        # Deterministic external update after the projection's real source read, no timing sleeps.
        fp.atomic_write(program.path("backlog.json"), concurrent_bytes)
        return observed

    def forbidden(*_args, **_kwargs):
        pytest.fail("Snapshot must not acquire Git metadata or a program lock")

    monkeypatch.setattr(fp.Program, "source_snapshot", changed)
    monkeypatch.setattr(fp.Program, "lock", forbidden)
    monkeypatch.setattr(fp, "git", forbidden)
    capsys.readouterr()
    # When the actual API/CLI constructs coverage after its earlier registry observation.
    if surface == "api":
        with pytest.raises(fp.ProgramError, match="Registry changed during snapshot; refresh"):
            fp.read_snapshot(gate.program.repo, gate.program.directory)
    else:
        assert gate.fixture.run("snapshot") == 1
        assert (
            capsys.readouterr().err.strip()
            == "feature-program: Registry changed during snapshot; refresh"
        )
    # Then no stale snapshot is returned and no concurrent registry update is overwritten.
    assert len(calls) == 1 and before != concurrent_bytes
    assert gate.program.path("backlog.json").read_bytes() == concurrent_bytes


def test_valid_original_state_with_unavailable_head_remains_unassessed(
    gate: GateFixture, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given valid owned state and an actual input, but no verified current HEAD observation.
    gate.ready()
    assert gate.fixture.claim() == 0
    name = gate.fixture.receipt()
    before = gate.program.path("backlog.json").read_bytes()

    def unavailable(_program: fp.Program) -> str:
        raise fp.ProgramError("Actual fixture HEAD unavailable")

    monkeypatch.setattr(fp.Program, "current_head", unavailable)
    capsys.readouterr()
    # When the real read-only diagnostic cannot acquire metadata outside its pure validator.
    assert (
        gate.fixture.run(
            "coverage",
            "--operation",
            "submit",
            "--input",
            name,
            "--task",
            "S01",
            "--owner",
            "worker",
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)["diagnostics"]
    # Then valid structure cannot supply a fictional HEAD or acceptance result.
    assert result["status"] == "not_assessed" and result["failure_type"] == "ProgramError"
    assert result["reasons"] == ["Actual fixture HEAD unavailable"]
    assert gate.program.path("backlog.json").read_bytes() == before


@pytest.mark.parametrize("fault", ["criteria", "task-source", "next-action"])
def test_no_input_cli_reports_invalid_original_state_without_projection(
    gate: GateFixture,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    state = gate.program.load()
    if fault == "criteria":
        del state["objective"]["criteria"]
    else:
        del gate.program.task(state, "S01")["source" if fault == "task-source" else "next_action"]
    gate.program.path("backlog.json").write_bytes(fp.canonical(state))
    before = {path: path.read_bytes() for path in gate.program.repo.rglob("*") if path.is_file()}
    with pytest.raises((fp.ProgramError, fp.coordination.GovernanceError)) as expected:
        gate.program.load()

    def forbidden(*_args, **_kwargs):
        pytest.fail("No-input coverage must not read Git, project invalid state, lock or write")

    monkeypatch.setattr(fp, "git", forbidden)
    monkeypatch.setattr(fp.Program, "lock", forbidden)
    monkeypatch.setattr(fp.Program, "save", forbidden)
    monkeypatch.setattr(fp.coverage, "projection", forbidden)
    capsys.readouterr()
    assert gate.fixture.run("coverage") == 0
    output = capsys.readouterr()
    report = json.loads(output.out)
    assert not output.err
    assert report["diagnostics"]["status"] == "not_assessed"
    assert report["diagnostics"]["structural_validation"] == {
        "status": "failed",
        "reasons": [str(expected.value)],
        "failure_type": type(expected.value).__name__,
    }
    assert report["projection"]["status"] == "unavailable"
    assert report["projection"]["reasons"] == [str(expected.value)]
    assert "criteria" not in report and "tasks" not in report
    assert before == {
        path: path.read_bytes() for path in gate.program.repo.rglob("*") if path.is_file()
    }


@pytest.mark.parametrize("drift", ["scoped-source", "frozen-source", "frozen-evidence"])
def test_cli_preserves_binding_drift_or_authoritative_integrity_failure(
    gate: GateFixture,
    capsys: pytest.CaptureFixture[str],
    drift: str,
) -> None:
    gate.ready()
    record_annotation(gate)
    state = gate.program.load()
    if drift == "scoped-source":
        path = gate.program.repo / "src/S01.py"
    elif drift == "frozen-source":
        path = fp.safe_path(gate.program.repo, state["source"]["path"])
    else:
        path = gate.program.path("evidence/_coverage/attempt-1/observation.txt")
    path.write_bytes(path.read_bytes() + b"\nControlled drift\n")
    before = {path: path.read_bytes() for path in gate.program.repo.rglob("*") if path.is_file()}
    capsys.readouterr()
    assert gate.fixture.run("coverage") == 0
    output = capsys.readouterr()
    assert not output.err
    report = json.loads(output.out)
    structural = report["diagnostics"]["structural_validation"]
    if drift == "scoped-source":
        assert structural["status"] == "validated"
        assert report["history"][0]["freshness"] == "stale"
        assert report["criteria"][0]["linkage"] == "unknown"
        assert "projection" not in report
    else:
        with pytest.raises(fp.ProgramError) as expected:
            gate.program.load()
        assert structural["status"] == "failed"
        assert structural["reasons"] == [str(expected.value)]
        assert report["projection"]["reasons"] == [str(expected.value)]
        assert "history" not in report
    assert before == {
        path: path.read_bytes() for path in gate.program.repo.rglob("*") if path.is_file()
    }


def test_transition_rejection_retains_valid_legacy_projection(
    gate: GateFixture,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate.ready()
    assert gate.fixture.claim() == 0
    name = gate.fixture.receipt()
    receipt = fp.read_json(gate.program.path(name))
    receipt["acceptance_complete"] = False
    gate.fixture.write(name, receipt)
    before = {path: path.read_bytes() for path in gate.program.repo.rglob("*") if path.is_file()}
    capsys.readouterr()
    assert gate.fixture.run("submit", "S01", "--owner", "worker", "--receipt", name) == 1
    actual = capsys.readouterr().err.strip().removeprefix("feature-program: ")

    def forbidden(*_args, **_kwargs):
        pytest.fail("Coverage observation must not lock or publish")

    monkeypatch.setattr(fp.Program, "lock", forbidden)
    monkeypatch.setattr(fp.Program, "save", forbidden)
    assert (
        gate.fixture.run(
            "coverage",
            "--operation",
            "submit",
            "--input",
            name,
            "--task",
            "S01",
            "--owner",
            "worker",
        )
        == 0
    )
    output = capsys.readouterr()
    assert not output.err
    report = json.loads(output.out)
    assert report["diagnostics"]["status"] == "failed"
    assert report["diagnostics"]["reasons"] == [actual]
    assert report["diagnostics"]["structural_validation"]["status"] == "validated"
    assert report["legacy_absence"] is True
    assert report["criteria"][0]["linkage"] == "unknown"
    assert "projection" not in report
    assert before == {
        path: path.read_bytes() for path in gate.program.repo.rglob("*") if path.is_file()
    }


def test_many_coverage_links_share_source_derivation_without_losing_exact_criteria(
    gate, monkeypatch
):
    # Given several criteria linking one exact frozen task source.
    gate.ready()
    state = gate.program.load()
    value = draft(gate)
    template = value["links"][0]
    value["links"] = [
        {
            **deepcopy(template),
            "criterion": {"ordinal": ordinal, "text_sha256": fp.sha(text.encode())},
        }
        for ordinal, text in enumerate(state["objective"]["criteria"])
    ]
    original = fp.extract_sections
    parsed = []

    def observe(raw):
        parsed.append(raw)
        return original(raw)

    monkeypatch.setattr(fp, "extract_sections", observe)
    # When all bindings are computed in one immutable read context.
    result = fp.coverage.bind_input(gate.program, state, value)
    # Then each exact criterion survives while shared source sections are parsed once.
    assert len(result["links"]) == len(state["objective"]["criteria"])
    assert len(parsed) == 1
    assert all(item["binding"] == result["links"][0]["binding"] for item in result["links"])


def test_coverage_metadata_rechecks_expected_hash_on_same_epoch_hit(gate):
    # Given an admitted original JSON and a second consumer claiming a conflicting hash.
    name = gate.fixture.write("evidence/_coverage/attempt-1/metadata.json", {"task_id": "S01"})
    ref = {"path": name, "sha256": fp.sha(gate.program.path(name).read_bytes())}
    reader = fp.ReadBudget()
    first = fp.coverage._metadata(gate.program, ref, reader)
    first["task_id"] = "changed consumer copy"
    # When the same epoch is consumed with another expected hash.
    with pytest.raises(fp.ProgramError, match="Frozen evidence changed"):
        fp.coverage._metadata(gate.program, {**ref, "sha256": "0" * 64}, reader)
    # Then the original decoded facts remain detached and its actual identity is unchanged.
    assert fp.coverage._metadata(gate.program, ref, reader) == {"task_id": "S01"}


def test_duplicate_exact_coverage_annotation_is_not_hidden_by_reference_index(gate):
    # Given conflicting history entries for the same immutable annotation reference.
    gate.ready()
    ref = record_annotation(gate)
    state = gate.program.load()
    state["coverage_annotations"].append(deepcopy(state["coverage_annotations"][0]))
    # When an explicit consumer relies on that exact reference.
    with pytest.raises(fp.coverage.CoverageError, match="exact recorded receipt"):
        fp.coverage.validate_refs(gate.program, state, {"coverage_refs": reliance(gate, ref)})
    # Then no indexed last-winner selection can turn ambiguous history into authority.


@pytest.mark.parametrize("guard_kind", ["record", "reliance"])
@pytest.mark.parametrize("drift", [None, "selector", "body"])
def test_envelope_coverage_guards_use_body_and_recheck_physical_storage(
    gate: GateFixture, guard_kind: str, drift: str | None
) -> None:
    gate.ready()
    assert gate.fixture.claim() == 0
    ref = record_annotation(gate)
    state = gate.program.load()
    storage = fp.flow_module("storage")
    body = gate.program.read_state_bytes()
    path = gate.program.path("backlog.json")
    original = storage.encode_registry(body, "1" * 64)
    path.write_bytes(original)
    if guard_kind == "record":
        value = draft(gate)
        value["id"] = "coverage-envelope"
        value.update(fp.coverage.bind_input(gate.program, state, value))
        name = gate.fixture.write("evidence/_coverage/attempt-1/envelope.json", value)
        guard = fp.coverage.prepare_record(gate.program, state, name).before_publish
    else:
        name = gate.fixture.receipt()
        receipt = fp.read_json(gate.program.path(name))
        receipt["coverage_refs"] = reliance(gate, ref)
        gate.fixture.write(name, receipt)
        future = gate.program.prepare_submit(
            state,
            argparse.Namespace(id="S01", owner="worker", receipt=name),
            observed_head=gate.program.current_head(),
        )
        guard = fp.coverage.publication_guard(gate.program, future)
    assert guard is not None
    if drift is None:
        guard()
        assert path.read_bytes() == original
    else:
        changed = deepcopy(state)
        if drift == "body":
            changed["runtime_observation"] = "Concurrent logical update"
        replacement = storage.encode_registry(
            body if drift == "selector" else fp.canonical(changed), "2" * 64
        )
        path.write_bytes(replacement)
        with pytest.raises(fp.coverage.CoverageError, match="[Rr]egistry changed"):
            guard()
        assert path.read_bytes() == replacement


def test_coverage_receipt_remains_raw_even_when_registry_uses_envelope(gate: GateFixture) -> None:
    gate.ready()
    state = gate.program.load()
    value = draft(gate)
    value.update(fp.coverage.bind_input(gate.program, state, value))
    storage = fp.flow_module("storage")
    gate.program.path("backlog.json").write_bytes(
        storage.encode_registry(fp.canonical(state), "1" * 64)
    )
    name = gate.fixture.write("evidence/_coverage/attempt-1/encoded-artifact.json", value)
    gate.program.path(name).write_bytes(storage.encode_registry(fp.canonical(value), "2" * 64))
    with pytest.raises(fp.ProgramError, match="JSON"):
        fp.coverage.prepare_record(gate.program, state, name)
