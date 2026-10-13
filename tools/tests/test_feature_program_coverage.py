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
