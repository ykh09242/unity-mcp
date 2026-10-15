"""Atomic record batches preserve normal decisions, publication and recovery."""

from copy import deepcopy
from dataclasses import FrozenInstanceError
import ast
import json
from time import perf_counter
from pathlib import Path

import pytest

from tools.tests.test_feature_program import fixture, fp  # noqa: F401

batch = fp.flow_module("batch")


class Host(fp.authoring.ClosureHost):
    """Use real detached note validators and the real canonical save/render boundary."""

    saves: int
    preflights: int
    callbacks: int

    def __init__(self, program):
        super().__init__(program)
        # Older runtimes cannot assign subclass instrumentation through a frozen/slotted base.
        object.__setattr__(self, "saves", 0)
        object.__setattr__(self, "preflights", 0)
        object.__setattr__(self, "callbacks", 0)

    def identity(self):
        return {"schema_version": 1, "producer": "batch-real-note-host"}

    def preflight_batch(self, original, final, stages):
        object.__setattr__(self, "preflights", self.preflights + 1)
        self.program.prepare_save(final)
        original_revision = self.revision(original)

        def fresh():
            object.__setattr__(self, "callbacks", self.callbacks + 1)
            fp.require(
                fp.phases.revision(fp.read_json(self.program.path("backlog.json")))
                == original_revision,
                "Original canonical revision changed",
            )

        return fp.PreparedChange(final, fresh)

    def inspect_views(self, state):
        return all(
            self.program.path(name).is_file()
            and self.program.path(name).read_bytes() == text.encode()
            for name, text in self.program.views(state).items()
        )

    def publish_batch(self, plan, owner, expected_revision):
        with self.program.lock():
            state = self.program.load()
            fp.require(self.revision(state) == expected_revision, "Fresh revision differs")
            result = batch.inspect_batch(self.program, state, plan, self)
            if result["status"] != "not_started":
                return result
            fp.require(owner == plan["owner_id"], "Publisher differs")
            prepared = batch.reconstruct(self.program, state, plan, self)
            object.__setattr__(self, "saves", self.saves + 1)
            try:
                self.program.save(prepared.state, before_publish=prepared.before_publish)
            except fp.ProgramError:
                actual = fp.read_json(self.program.path("backlog.json"))
                if self.revision(actual) != plan["final_revision"]:
                    raise
                return {
                    "status": "committed_view_failed",
                    "revision": self.revision(actual),
                    "view_recovery_required": True,
                }
            return {"status": "committed", "revision": self.revision(prepared.state)}


def test_test_host_instrumentation_does_not_thaw_actual_host_fields():
    # Given mutable test-only counters around the actual immutable publication host.
    host = Host(None)
    assert (host.saves, host.preflights, host.callbacks) == (0, 0, 0)
    # When ordinary code tries to change an actual inherited host binding.
    with pytest.raises(FrozenInstanceError):
        host.program = "different program"
    # Then the compatibility workaround has not relaxed production host immutability.
    assert host.program is None


def request(fixture, *, count=3, owner="worker", identifier="batch-1"):
    return {
        "schema_version": 1,
        "id": identifier,
        "owner_id": owner,
        "expected_revision": fp.phases.revision(fixture.program.load()),
        "scope": ["src/S01.py"],
        "refs": [],
        "operations": [
            {
                "id": f"note-{number}",
                "operation": "note",
                "payload": {
                    "id": "S01",
                    "owner": "worker",
                    "reason": f"Observation {number}",
                    "next_action": "Inspect actual evidence",
                },
                "observed_at": f"2026-10-15T00:00:0{number}+00:00",
            }
            for number in range(1, count + 1)
        ],
    }


def prepared(fixture, host, **options):
    return batch.prepare_batch(
        fixture.program, fixture.program.load(), request(fixture, **options), host
    )


def test_python_311_syntax_is_checked_first():
    # Given the two authored files, not the runtime's newer grammar.
    paths = [Path(batch.__file__), Path(__file__)]
    # When parsed with the Python 3.11 grammar.
    for path in paths:
        ast.parse(path.read_text(encoding="utf-8"), feature_version=(3, 11))
    # Then syntax compatibility is checked without claiming actual Python 3.11 execution.


def test_invalid_late_operation_publishes_no_registry_or_views(fixture):
    # Given an ordinary valid first note and invalid last task.
    assert fixture.claim() == 0
    host = Host(fixture.program)
    original = {
        name: fixture.program.path(name).read_bytes()
        for name in ["backlog.json", "BOARD.md", "tasks/S01.md"]
    }
    supplied = request(fixture)
    supplied["operations"][-1]["payload"]["id"] = "MISSING"
    # When the entire detached sequence is validated.
    with pytest.raises(fp.ProgramError):
        batch.prepare_batch(fixture.program, fixture.program.load(), supplied, host)
    # Then neither earlier operations nor views are published.
    assert host.saves == 0
    assert all(fixture.program.path(name).read_bytes() == raw for name, raw in original.items())


def test_one_save_and_render_preserve_order_and_exact_retry(fixture, monkeypatch):
    # Given three real ordinary notes and a frozen plan.
    assert fixture.claim() == 0
    host = Host(fixture.program)
    state = fixture.program.load()
    plan = prepared(fixture, host)
    renders = []
    original_render = fp.Program.render

    def render(program, final):
        renders.append(fp.phases.revision(final))
        original_render(program, final)

    monkeypatch.setattr(fp.Program, "render", render)
    # When the batch publishes and its identical frozen plan retries.
    result = batch.publish_batch(fixture.program, plan, host, "worker", host.revision(state))
    actual = fixture.program.load()
    retry = batch.publish_batch(fixture.program, plan, host, "worker", host.revision(actual))
    # Then one publication/render contains three ordinary audits plus one noncircular marker.
    assert result["status"] == "committed"
    assert retry["status"] == "already_committed"
    assert host.saves == len(renders) == host.callbacks == 1
    assert [event["action"] for event in actual["events"][-4:]] == ["note"] * 3 + ["record_batch"]
    marker = actual["events"][-1]
    assert marker == plan["marker"]
    assert "final_revision" not in marker
    assert marker["event_span_sha256"] == fp.digest(actual["events"][-4:-1])
    assert result["actual_revision"] == plan["final_revision"]
    assert retry["committed_operation_count"] == 3


def test_committed_render_failure_requires_only_view_recovery(fixture, monkeypatch):
    # Given a failure at the real Program.save postcommit render boundary.
    assert fixture.claim() == 0
    host = Host(fixture.program)
    plan = prepared(fixture, host)

    def fail_render(program, state):
        raise OSError("Controlled postcommit render failure")

    with monkeypatch.context() as patch:
        patch.setattr(fp.Program, "render", fail_render)
        # When the canonical replacement succeeds but rendering fails.
        result = batch.publish_batch(fixture.program, plan, host, "worker", plan["base_revision"])
    # Then retry recognizes commitment and never publishes/render-replays operations.
    actual = fixture.program.load()
    retry = batch.publish_batch(fixture.program, plan, host, "worker", host.revision(actual))
    assert result["status"] == "committed_view_failed"
    assert result["actual_revision"] == plan["final_revision"]
    assert retry["status"] == "already_committed"
    assert retry["next_action"] == "render"
    assert host.saves == 1
    events = deepcopy(actual["events"])
    fixture.program.render(actual)
    recovered = batch.inspect_batch(fixture.program, actual, plan, host)
    assert recovered["view_recovery_required"] is False
    assert fixture.program.load()["events"] == events


def test_later_progress_preserves_exact_historical_commit_recognition(fixture):
    # Given a committed batch followed by an unrelated normal note.
    assert fixture.claim() == 0
    host = Host(fixture.program)
    plan = prepared(fixture, host)
    batch.publish_batch(fixture.program, plan, host, "worker", plan["base_revision"])
    assert (
        fixture.run(
            "note",
            "S01",
            "--owner",
            "worker",
            "--reason",
            "Later fact",
            "--next-action",
            "Continue",
        )
        == 0
    )
    # When the old batch is inspected against the actual newer state.
    actual = fixture.program.load()
    result = batch.inspect_batch(fixture.program, actual, plan, host)
    # Then exact history is recognized without pretending its final revision remains current.
    assert result["status"] == "already_committed"
    assert result["applicability"] == "historical"
    assert result["actual_revision"] != plan["final_revision"]
    assert host.saves == 1


@pytest.mark.parametrize("drift", ["source", "ref", "producer", "revision"])
def test_prepublication_drift_never_records_earlier_valid_operations(fixture, monkeypatch, drift):
    # Given a prepared plan and one independently changed binding.
    assert fixture.claim() == 0
    host = Host(fixture.program)
    proof = fixture.evidence("S01", label="selected-proof")
    supplied = request(fixture)
    supplied["refs"] = [{"path": proof, "sha256": fp.sha(fixture.program.path(proof).read_bytes())}]
    plan = batch.prepare_batch(fixture.program, fixture.program.load(), supplied, host)
    if drift == "source":
        (fixture.program.repo / "src/S01.py").write_text("changed = True\n")
    elif drift == "ref":
        fixture.program.path(proof).write_text("Changed immutable proof\n")
    elif drift == "producer":
        monkeypatch.setattr(Host, "identity", lambda _host: {"producer": "different"})
    else:
        assert (
            fixture.run(
                "note",
                "S01",
                "--owner",
                "worker",
                "--reason",
                "Concurrent fact",
                "--next-action",
                "Inspect",
            )
            == 0
        )
    original = fixture.program.path("backlog.json").read_bytes()
    # When publication reconstructs the exact original batch.
    with pytest.raises((batch.BatchError, fp.ProgramError)):
        batch.reconstruct(fixture.program, fixture.program.load(), plan, host)
    # Then no batch event or earlier note is appended.
    assert fixture.program.path("backlog.json").read_bytes() == original
    assert host.saves == 0


@pytest.mark.parametrize(
    "operation",
    [
        "verification-run",
        "delivery",
        "resume",
        "objective-complete",
        "coverage-record",
        "git",
        "native-send",
    ],
)
def test_closed_catalog_rejects_stored_execution_or_unproven_adapter(fixture, operation):
    # Given a stored unapproved command in a later row.
    assert fixture.claim() == 0
    host = Host(fixture.program)
    supplied = request(fixture)
    supplied["operations"][-1]["operation"] = operation
    before = fixture.program.path("backlog.json").read_bytes()
    # When preparation parses the closed operation catalog.
    with pytest.raises(batch.BatchError):
        batch.prepare_batch(fixture.program, fixture.program.load(), supplied, host)
    # Then no command or publication took place.
    assert fixture.program.path("backlog.json").read_bytes() == before
    assert host.saves == 0


def test_publisher_must_have_authority_for_every_task(fixture):
    # Given valid individual payload authors on two separately owned tasks.
    assert fixture.claim("S01", owner="worker-a") == 0
    assert fixture.claim("S02", owner="worker-b") == 0
    host = Host(fixture.program)
    supplied = request(fixture, owner="worker-a", count=2)
    for index, worker in enumerate(("worker-a", "worker-b")):
        supplied["operations"][index]["payload"].update(id=f"S0{index + 1}", owner=worker)
    before = fixture.program.path("backlog.json").read_bytes()
    # When only the first worker is the actual batch publisher.
    with pytest.raises(fp.ProgramError):
        batch.prepare_batch(fixture.program, fixture.program.load(), supplied, host)
    # Then two valid authors never impersonate a cross-task publisher.
    assert fixture.program.path("backlog.json").read_bytes() == before


def test_same_id_changed_payload_conflicts_even_with_a_resigned_envelope(fixture):
    # Given an actual committed marker.
    assert fixture.claim() == 0
    host = Host(fixture.program)
    plan = prepared(fixture, host)
    batch.publish_batch(fixture.program, plan, host, "worker", plan["base_revision"])
    changed = request(fixture)
    changed["operations"][0]["payload"]["reason"] = "Different decision"
    # When the same batch identifier is proposed with new semantic bytes.
    with pytest.raises(batch.BatchError):
        batch.prepare_batch(fixture.program, fixture.program.load(), changed, host)
    # Then no second publication is possible, and exact original inspection still succeeds.
    assert host.saves == 1
    assert (
        batch.inspect_batch(fixture.program, fixture.program.load(), plan, host)["status"]
        == "already_committed"
    )


def test_forged_marker_span_and_mutable_refs_are_not_commit_proof(fixture):
    # Given a legitimate prepared plan and a matching-looking fake marker.
    assert fixture.claim() == 0
    host = Host(fixture.program)
    plan = prepared(fixture, host)
    forged = fixture.program.load()
    forged["events"].append(deepcopy(plan["marker"]))
    # When the marker is present without its exact normal operation span.
    with pytest.raises(batch.BatchError):
        batch.inspect_batch(fixture.program, forged, plan, host)
    # Then no committed outcome is inferred from structurally invalid canonical history.
    supplied = request(fixture, identifier="mutable")
    supplied["refs"] = [
        {
            "path": "backlog.json",
            "sha256": fp.sha(fixture.program.path("backlog.json").read_bytes()),
        }
    ]
    with pytest.raises(batch.BatchError):
        batch.prepare_batch(fixture.program, fixture.program.load(), supplied, host)


def test_actual_batch_host_publishes_once_and_recognizes_retry(fixture, monkeypatch):
    # Given the actual integration host with its projected ordinary guards.
    assert fixture.claim() == 0
    state = fixture.program.load()
    host = fp.authoring.BatchHost(fixture.program, publication_base=state)
    plan = batch.prepare_batch(fixture.program, state, request(fixture), host)
    publications = []
    original = fp.Program._publish

    def publish(program, final, before_publish=None):
        publications.append(fp.phases.revision(final))
        return original(program, final, before_publish)

    monkeypatch.setattr(fp.Program, "_publish", publish)
    # When the real host publishes and the exact immutable request is retried.
    first = batch.publish_batch(fixture.program, plan, host, "worker", plan["base_revision"])
    retry = batch.publish_batch(
        fixture.program, plan, host, "worker", fp.phases.revision(fixture.program.load())
    )
    # Then only one canonical publication occurs and the normal marker is validated.
    assert first["status"] == "committed"
    assert retry["status"] == "already_committed"
    assert publications == [plan["final_revision"]]
    batch.validate_history(fixture.program.load())


def test_default_projection_uses_complete_latest_marker_and_bounded_counts(fixture):
    # Given a real committed batch with more rows than the presentation limit.
    assert fixture.claim() == 0
    host = Host(fixture.program)
    plan = prepared(fixture, host)
    batch.publish_batch(fixture.program, plan, host, "worker", plan["base_revision"])
    # When a normal snapshot projects only one display row.
    result = batch.project_latest_batch(fixture.program.load(), limit=1)
    # Then total commitment is assessed before truncation, without invented current preflight/IO.
    assert result["status"] == "committed"
    assert result["preparation"] == {"status": "recorded"}
    assert result["applicability"] == "unknown"
    assert result["committed_operation_count"] == 3
    assert result["operations"]["shown"] == 1
    assert result["operations"]["omitted"] == 2
    assert result["operations"]["items"][0]["operation"] == "note"
    assert result["actual_write_count"] is result["actual_render_count"] is None
    broken = fixture.program.load()
    broken["events"].append(deepcopy(plan["marker"]))
    with pytest.raises(batch.BatchError):
        batch.project_latest_batch(broken)


def test_extra_request_evidence_is_retained_and_verified_without_selected_plan(fixture):
    # Given extra immutable input not already referenced by a note payload.
    assert fixture.claim() == 0
    host = Host(fixture.program)
    supplied = request(fixture)
    proof = fixture.evidence("S01", label="extra-batch-input")
    ref = {"path": proof, "sha256": fp.sha(fixture.program.path(proof).read_bytes())}
    supplied["refs"] = [ref, {"path": proof.replace("/", "//"), "sha256": ref["sha256"]}]
    plan = batch.prepare_batch(fixture.program, fixture.program.load(), supplied, host)
    batch.publish_batch(fixture.program, plan, host, "worker", plan["base_revision"])
    # When canonical marker evidence is read without the selected plan.
    assert batch.evidence_references(fixture.program.load()) == [ref]
    fixture.program.path(proof).write_text("Changed frozen evidence")
    # Then the ordinary full history loader rejects changed bytes.
    with pytest.raises(fp.ProgramError):
        fixture.program.load()


@pytest.mark.parametrize("field", ["before_revision", "event_count", "refs"])
def test_resigned_inconsistent_stage_plan_is_rejected_before_host_execution(fixture, field):
    # Given a legitimate envelope deliberately resigned around inconsistent internal facts.
    assert fixture.claim() == 0
    host = Host(fixture.program)
    plan = prepared(fixture, host)
    if field == "before_revision":
        plan["stages"][1][field] = "f" * 64
    elif field == "event_count":
        plan["stages"][1][field] = 0
    else:
        plan["stages"][1][field] = [{"path": "evidence/unretained", "sha256": "f" * 64}]
    plan["marker"] = batch._marker(plan)
    plan["envelope_digest"] = fp.digest(
        {key: value for key, value in plan.items() if key != "envelope_digest"}
    )
    # When a reader receives the forged stored plan.
    with pytest.raises(batch.BatchError):
        batch.inspect_batch(fixture.program, fixture.program.load(), plan, host)
    # Then no normal publication or operation replay occurs.
    assert host.saves == 0


def test_committed_fact_survives_source_drift_without_current_applicability(fixture):
    # Given an exact committed batch and then modified source, without registry changes.
    assert fixture.claim() == 0
    host = Host(fixture.program)
    plan = prepared(fixture, host)
    batch.publish_batch(fixture.program, plan, host, "worker", plan["base_revision"])
    (fixture.program.repo / "src/S01.py").write_text("modified = True\n")
    # When inspected, commit identity is preserved but its old source is never called current.
    result = batch.inspect_batch(fixture.program, fixture.program.load(), plan, host)
    assert result["status"] == "already_committed"
    assert result["applicability"] == "stale"
    assert host.saves == 1


def test_selected_publication_results_keep_recorded_failure_and_actual_commit_separate(fixture):
    # Given a frozen plan and an actual precommit failure result referencing it.
    assert fixture.claim() == 0
    host = Host(fixture.program)
    plan = prepared(fixture, host)
    name = fixture.write("evidence/S01/attempt-1/batch-plan.json", plan)
    ref = {"path": name, "sha256": fp.sha(fixture.program.path(name).read_bytes())}
    wrapper = {
        "schema_version": 1,
        "kind": "batch_publication_result",
        "plan_ref": ref,
        "outcome": {"status": "not_committed", "reason_code": "precommit_guard_failed"},
    }
    result = batch.project_selection(fixture.program, fixture.program.load(), wrapper, host)
    assert result["publication"]["status"] == "not_committed"
    assert result["committed_operation_count"] == 0
    wrapper["outcome"]["status"] = "committed"
    # When a forged wrapper claims commit before the exact canonical marker exists.
    assert (
        batch.project_selection(fixture.program, fixture.program.load(), wrapper, host)["status"]
        == "conflict"
    )
    batch.publish_batch(fixture.program, plan, host, "worker", plan["base_revision"])
    wrapper["outcome"]["status"] = "committed_view_failed"
    # Then current derived views govern recovery, while the original failed result remains explicit.
    result = batch.project_selection(fixture.program, fixture.program.load(), wrapper, host)
    assert result["status"] == "already_committed"
    assert result["view_recovery_required"] is False
    assert result["recorded_publication"]["status"] == "committed_view_failed"
    fixture.program.path(name).write_text("{}")
    with pytest.raises(batch.BatchError):
        batch.project_selection(fixture.program, fixture.program.load(), wrapper, host)


@pytest.mark.parametrize("publication_status", ["not_committed", "unknown"])
def test_selected_actual_preparation_failure_is_never_prepared_or_published(
    fixture, publication_status
):
    # Given an exact immutable request and an actual failed preparation wrapper.
    assert fixture.claim() == 0
    host = Host(fixture.program)
    supplied = request(fixture, count=2)
    supplied["operations"][-1]["payload"]["id"] = "missing"
    name = fixture.write("evidence/S01/attempt-1/failed-request.json", supplied)
    wrapper = {
        "schema_version": 1,
        "kind": "batch_preparation_failure",
        "request_ref": {"path": name, "sha256": fp.sha(fixture.program.path(name).read_bytes())},
        "program_id": fixture.program.load()["program_id"],
        "base_revision": supplied["expected_revision"],
        "failure": {"reason": "Invalid later task", "failure_type": "ProgramError"},
        "preparation": {"status": "failed"},
        "publication": {"status": publication_status},
        "accepted": False,
    }
    # When selected for ordinary safe display.
    result = batch.project_selection(fixture.program, fixture.program.load(), wrapper, host)
    # Then requested rows remain known, while unobserved validation/publication is unknown.
    assert result["preparation"] == {"status": "failed"}
    assert result["publication"]["status"] == publication_status
    assert result["requested_operation_count"] == 2
    assert result["validated_operation_count"] is None
    assert result["committed_operation_count"] == (
        0 if publication_status == "not_committed" else None
    )
    assert result["operations"]["total"] == 2
    assert [row["id"] for row in result["operations"]["items"]] == ["note-1", "note-2"]
    assert all(row["status"] == "not_assessed" for row in result["operations"]["items"])
    assert result["next_actor_id"] == "worker"
    assert result["next_action"] == "prepare_batch"
    wrapper["publication"]["status"] = "committed"
    with pytest.raises(batch.BatchError):
        batch.project_selection(fixture.program, fixture.program.load(), wrapper, host)


def test_actual_host_retains_late_projected_guard_before_single_commit(fixture, monkeypatch):
    # Given a whole valid plan and an actual publication host with a late guard failure.
    assert fixture.claim() == 0
    state = fixture.program.load()
    host = fp.authoring.BatchHost(fixture.program, publication_base=state)
    plan = batch.prepare_batch(fixture.program, state, request(fixture), host)
    original = fixture.program.path("backlog.json").read_bytes()
    guard_factory = fp.verification.publication_guard
    called = []

    def publication_guard(program, proposal, **options):
        normal = guard_factory(program, proposal, **options)
        if "predecessor" not in options:
            return normal
        stage = len(proposal["events"])

        def recheck():
            if normal is not None:
                normal()
            called.append(stage)
            if len(called) == 3:
                raise fp.ProgramError("Actual final projected guard rejected current reliance")

        return recheck

    monkeypatch.setattr(fp.verification, "publication_guard", publication_guard)
    # When the final projected callback fails inside the actual guarded publication.
    with pytest.raises(fp.ProgramError, match="final projected guard"):
        batch.publish_batch(fixture.program, plan, host, "worker", plan["base_revision"])
    # Then all three callbacks were retained and no canonical or derived note was published.
    assert len(called) == 3
    assert fixture.program.path("backlog.json").read_bytes() == original
    assert batch.project_latest_batch(fixture.program.load()) is None
    assert host.inspect_views(fixture.program.load())


def test_actual_batch_reduces_publication_boundaries_without_timing_threshold(
    fixture, monkeypatch, capsys
):
    # Given three ordinary notes and three batch notes on the same realistic registry.
    assert fixture.claim() == 0
    counters = {name: 0 for name in ("load", "prepare_save", "_publish", "render")}

    def observe(name):
        normal = getattr(fp.Program, name)

        def counted(*arguments, **options):
            counters[name] += 1
            return normal(*arguments, **options)

        monkeypatch.setattr(fp.Program, name, counted)

    for name in counters:
        observe(name)
    started = perf_counter()
    for number in range(3):
        assert (
            fixture.run(
                "note",
                "S01",
                "--owner",
                "worker",
                "--reason",
                f"Ordinary {number}",
                "--next-action",
                "Inspect actual evidence",
            )
            == 0
        )
    ordinary_seconds = perf_counter() - started
    ordinary = dict(counters)
    state = fixture.program.load()
    host = fp.authoring.BatchHost(fixture.program, publication_base=state)
    plan = batch.prepare_batch(fixture.program, state, request(fixture), host)
    for name in counters:
        counters[name] = 0
    started = perf_counter()
    # When the real batch publication reconstructs all normal validators and publishes once.
    result = batch.publish_batch(fixture.program, plan, host, "worker", plan["base_revision"])
    batch_seconds = perf_counter() - started
    # Then structural work is deterministic; timings are observations without a speed claim.
    assert result["status"] == "committed"
    assert ordinary == {"load": 3, "prepare_save": 3, "_publish": 3, "render": 3}
    assert counters == {"load": 1, "prepare_save": 1, "_publish": 1, "render": 1}
    with capsys.disabled():
        print(
            "BATCH_PUBLICATION_MEASUREMENT "
            + json.dumps(
                {
                    "operation_count": 3,
                    "ordinary": ordinary,
                    "batch": counters,
                    "ordinary_seconds": ordinary_seconds,
                    "batch_seconds": batch_seconds,
                    "method": "same registry; ordinary CLI notes then batch publication; preparation excluded; no threshold",
                },
                sort_keys=True,
            )
        )


def test_late_noop_without_ordinary_audit_cannot_publish_earlier_valid_rows(fixture):
    # Given an adapter that accidentally treats the last operation as a no-op.
    assert fixture.claim() == 0

    class NoopHost(Host):
        def prepare(self, state, operation, payload, observed_at, *, owner=None):
            if payload["reason"] == "Observation 3":
                return fp.PreparedChange(deepcopy(state))
            return super().prepare(state, operation, payload, observed_at, owner=owner)

    host = NoopHost(fixture.program)
    before = fixture.program.path("backlog.json").read_bytes()
    # When detached preparation reaches the missing ordinary audit fact.
    with pytest.raises(batch.BatchError, match="append ordinary events"):
        prepared(fixture, host)
    # Then no marker or preceding valid operation is published.
    assert host.saves == 0
    assert fixture.program.path("backlog.json").read_bytes() == before


@pytest.mark.parametrize("raw_operations", [None, [{"operation": "git"}, {"id": "bad"}]])
def test_selected_preparation_failure_preserves_unavailable_or_unassessed_request_counts(
    fixture, raw_operations
):
    # Given a frozen request whose operations themselves caused schema rejection.
    assert fixture.claim() == 0
    host = Host(fixture.program)
    supplied = request(fixture)
    supplied["operations"] = raw_operations
    name = fixture.write("evidence/S01/attempt-1/malformed-request.json", supplied)
    wrapper = {
        "schema_version": 1,
        "kind": "batch_preparation_failure",
        "request_ref": {"path": name, "sha256": fp.sha(fixture.program.path(name).read_bytes())},
        "program_id": fixture.program.load()["program_id"],
        "base_revision": supplied["expected_revision"],
        "failure": {"reason": "Malformed requested operations", "failure_type": "BatchError"},
        "preparation": {"status": "failed"},
        "publication": {"status": "unknown"},
        "accepted": False,
    }
    # When failure projection reads those exact bytes without relaxing the operation catalog.
    result = batch.project_selection(fixture.program, fixture.program.load(), wrapper, host)
    known = None if raw_operations is None else 2
    # Then an absent list is unknown, a malformed known list still has its actual count.
    assert result["requested_operation_count"] == known
    assert result["validated_operation_count"] is result["committed_operation_count"] is None
    assert result["operations"] == {"items": [], "total": known, "shown": 0, "omitted": known}
    assert host.saves == 0
    fixture.program.path(name).write_text("{}")
    with pytest.raises(batch.BatchError):
        batch.project_selection(fixture.program, fixture.program.load(), wrapper, host)
