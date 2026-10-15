"""Real isolated CLI/HTTP integration; no native or provider acceptance is simulated."""

from copy import deepcopy
import json

import pytest

from tools.tests.test_feature_program import HEAD, fp, make_small_program, _mock_link_lstat


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(fp, "git", lambda _repo, args: "" if args[0] == "status" else HEAD)
    return make_small_program(tmp_path / "repo", "throughput-fixture", ("S01", "S02"))


@pytest.mark.parametrize("raw", [[{"value": 1}], 7, "raw", {"verification_refs": ["opaque"]}])
def test_raw_json_attachment_remains_opaque_through_submit_and_review(fixture, raw):
    # Given: a real task receipt refers to an opaque JSON artifact.
    assert fixture.claim() == 0
    artifact = fixture.write("evidence/S01/attempt-1/raw.json", raw)
    receipt_path = fixture.receipt()
    receipt = fp.read_json(fixture.program.path(receipt_path))
    receipt["checks"][0]["evidence"].append(artifact)
    fixture.write(receipt_path, receipt)
    # When: normal supported CLI submission and review consume the artifact.
    assert fixture.run("submit", "S01", "--owner", "worker", "--receipt", receipt_path) == 0
    review = fixture.review()
    assert fixture.run("complete", "S01", "--review", review) == 0
    # Then: actual source/evidence validation succeeds without rewriting raw bytes.
    assert fixture.program.task(fixture.program.load(), "S01")["status"] == "done"
    assert json.loads(fixture.program.path(artifact).read_text()) == raw


def test_real_control_with_non_json_suffix_still_rejects_unknown_reliance(fixture):
    # Given: the actual execution control has a misleading raw suffix.
    assert fixture.claim() == 0
    receipt_path = fixture.receipt()
    receipt = fp.read_json(fixture.program.path(receipt_path))
    receipt["verification_refs"] = [{"id": "missing"}]
    actual_control = fixture.write("evidence/S01/attempt-1/execution.raw", receipt)
    before = fixture.program.path("backlog.json").read_bytes()
    # When: its explicitly declared semantic reliance is submitted.
    result = fixture.run("submit", "S01", "--owner", "worker", "--receipt", actual_control)
    # Then: strict normal validation preserves the original canonical state.
    assert result == 1
    assert fixture.program.path("backlog.json").read_bytes() == before


def test_legacy_snapshot_has_stable_semantics_without_new_observations(fixture, monkeypatch):
    # Given: a legacy program has no adopted handoff/provenance/batch history.
    from tools.tests.test_feature_program_web import web

    monkeypatch.setattr(fp, "utc", lambda: "2026-10-15T01:00:00+00:00")
    first = fp.read_snapshot(fixture.program.repo, fixture.program.directory)
    monkeypatch.setattr(fp, "utc", lambda: "2026-10-15T02:00:00+00:00")
    # When: only the read's generated_at changes.
    second = fp.read_snapshot(fixture.program.repo, fixture.program.directory)
    # Then: semantic timing and existing weak ETag remain identical.
    assert first.get("timing") == second.get("timing")
    assert web.snapshot_etag(first) == web.snapshot_etag(second)


def _register_reviewer(fixture):
    receipt = fixture.write(
        "evidence/_coordination/attempt-1/reviewer.json",
        {
            "id": "reviewer",
            "role": "reviewer",
            "parent_id": None,
            "decision": "new",
            "reason": "Synthetic reviewer only",
            "provenance": "unknown",
            "model_id": None,
            "reasoning_effort": None,
            "observed_status": "unknown",
            "observed_at": None,
        },
    )
    assert fixture.run("actor-record", "--input", receipt) == 0


def _submitted_with_handoff(fixture, capsys, review_scope=None, review_refs=None):
    _register_reviewer(fixture)
    assert fixture.claim() == 0
    receipt = fixture.receipt()
    request = fixture.write(
        "evidence/S01/attempt-1/handoff.json",
        {
            "reviewer_id": "reviewer",
            "review_inputs": {
                "scope": review_scope or ["src/S01.py"],
                "evidence_refs": review_refs or [],
            },
        },
    )
    capsys.readouterr()
    assert (
        fixture.run(
            "submit", "S01", "--owner", "worker", "--receipt", receipt, "--handoff", request
        )
        == 0
    )
    return json.loads(capsys.readouterr().out)


def test_submit_handoff_returns_actual_ready_packet_without_native_action(fixture, capsys):
    # Given: an actual supported synthetic submission with explicit reviewer.
    result = _submitted_with_handoff(fixture, capsys)
    # When: the caller inspects its successful result and durable state.
    state = fixture.program.load()
    packet = result["packet_ref"]
    # Then: one exact packet/intent is ready; local tools have sent no native work.
    assert result["status"] == "submitted" and result["native_dispatched"] is False
    assert fp.sha(fixture.program.path(packet["path"]).read_bytes()) == packet["sha256"]
    assert len(state["review_handoffs"]["records"]) == 1
    assert result["submitted_observation"]["timestamp_source"] == "live_postcondition_observation"


def test_exact_handoff_replay_after_unrelated_revision_adds_no_event_or_publication(
    fixture, capsys
):
    # Given: a retained exact post-submit observation is actually published.
    result = _submitted_with_handoff(fixture, capsys)
    state = fixture.program.load()
    observed = result["submitted_observation"]
    raw = {
        "schema_version": 1,
        "operation": "review-handoff-record",
        "id": "submit-observed-1",
        "kind": "submitted",
        "handoff_id": result["handoff_id"],
        "assignment_generation": result["assignment_generation"],
        "subject": result["subject"],
        "packet_ref": result["packet_ref"],
        "actor_id": "leader",
        "expected_revision": fp.phases.revision(state),
        "reason": "Synthetic actual postcondition observation",
        "evidence_refs": [],
        **observed,
    }
    name = fixture.write("evidence/S01/attempt-1/submitted-observation.json", raw)
    assert fixture.run("review-handoff-record", "--input", name) == 0
    assert (
        fixture.run(
            "note",
            "S01",
            "--owner",
            "worker",
            "--reason",
            "Unrelated note",
            "--next-action",
            "Review exact packet",
        )
        == 0
    )
    before = fixture.program.path("backlog.json").read_bytes()
    capsys.readouterr()
    # When: the same frozen original input is retried despite its historical expected revision.
    assert fixture.run("review-handoff-record", "--input", name) == 0
    replay = json.loads(capsys.readouterr().out)
    # Then: exact recognition leaves canonical history unchanged.
    assert replay["status"] == "already_recorded"
    assert fixture.program.path("backlog.json").read_bytes() == before


def _note_batch_request(fixture):
    return {
        "schema_version": 1,
        "id": "cli-batch",
        "owner_id": "leader",
        "expected_revision": fp.phases.revision(fixture.program.load()),
        "scope": ["src/S01.py"],
        "refs": [],
        "operations": [
            {
                "id": "note-one",
                "operation": "note",
                "observed_at": "2026-10-15T01:00:00+00:00",
                "payload": {
                    "id": "S01",
                    "owner": "leader",
                    "reason": "Synthetic batch",
                    "next_action": "Inspect",
                },
            }
        ],
    }


def test_failed_batch_publication_retains_diagnostic_and_exits_nonzero(fixture, capsys):
    # Given: a valid plan is retained through the real CLI.
    name = fixture.write(
        "evidence/_coordination/attempt-1/batch-request.json", _note_batch_request(fixture)
    )
    plan_name = "evidence/_coordination/attempt-1/batch-plan.json"
    assert fixture.run("batch-prepare", "--input", name, "--result-path", plan_name) == 0
    plan = fp.read_json(fixture.program.path(plan_name))
    # When: publication receives a wrong transaction revision and retains its actual failure.
    capsys.readouterr()
    code = fixture.run(
        "batch-publish",
        "--plan",
        plan_name,
        "--owner",
        "leader",
        "--expected-revision",
        "0" * 64,
        "--result-path",
        "evidence/_coordination/attempt-1/publication-failure.json",
    )
    output = json.loads(capsys.readouterr().out)
    # Then: saving a diagnostic does not signal successful mutation.
    assert code == 1
    assert output["outcome"]["status"] == "not_committed"
    assert fp.phases.revision(fixture.program.load()) == plan["base_revision"]


def test_invalid_late_batch_prepare_retains_failure_and_exits_nonzero(fixture, capsys):
    # Given: a later task is invalid after a valid projected note.
    request = _note_batch_request(fixture)
    request["operations"].append(
        {
            **deepcopy(request["operations"][0]),
            "id": "invalid-late",
            "payload": {
                "id": "missing",
                "owner": "leader",
                "reason": "Invalid",
                "next_action": "Inspect",
            },
        }
    )
    name = fixture.write("evidence/_coordination/attempt-1/bad-request.json", request)
    before = fixture.program.path("backlog.json").read_bytes()
    capsys.readouterr()
    # When: actual preparation failure is saved.
    code = fixture.run(
        "batch-prepare",
        "--input",
        name,
        "--result-path",
        "evidence/_coordination/attempt-1/preparation-failure.json",
    )
    output = json.loads(capsys.readouterr().out)
    # Then: the valid prefix was never published and diagnostic retention remains a failure.
    assert code == 1 and output["kind"] == "batch_preparation_failure"
    assert fixture.program.path("backlog.json").read_bytes() == before


def _output(fixture, capsys, *args):
    capsys.readouterr()
    assert fixture.run(*args) == 0
    return json.loads(capsys.readouterr().out)


def test_real_counter_receipt_preparation_uses_actual_adapter_and_does_not_reexecute(
    fixture, capsys
):
    from tools.tests.test_feature_program_execution import prepare as prepare_run
    from tools.tests.test_feature_program_evidence_reuse import selection

    # Given: one actual harmless append process and its existing sealed capture.
    assert fixture.claim() == 0
    prepared = prepare_run(
        fixture, "from pathlib import Path; Path('counter-once.txt').open('a').write('x')"
    )
    actual = fp.flow_module("execution").execute_run(prepared)
    selected = selection([actual["capture_ref"]])
    plan_request = fixture.write(
        "evidence/S01/attempt-1/selection-request.json", {"kind": "plan", "selection": selected}
    )
    plan = _output(fixture, capsys, "evidence-prepare", "--input", plan_request)
    material_request = fixture.write(
        "evidence/S01/attempt-1/material-request.json", {"kind": "materialize", "plan": plan}
    )
    material = _output(fixture, capsys, "evidence-prepare", "--input", material_request)
    request = fixture.write(
        "evidence/S01/attempt-1/observation-preparation.json",
        {
            "kind": "receipt",
            "operation": "observation",
            "explicit_inputs": {
                "task_id": "S01",
                "owner": "qa",
                "capture_ref": actual["capture_ref"],
                "request_binding": prepared.binding,
                "materialization": material,
            },
        },
    )
    # When: supported CLI preparation reaches the actual ready-receipt callback twice.
    first = _output(fixture, capsys, "evidence-prepare", "--input", request)
    second = _output(fixture, capsys, "evidence-prepare", "--input", request)
    # Then: ordinary detached validation passed and the original process still ran once.
    assert first["status"] == second["status"] == "ready", (
        first["assessment"],
        second["assessment"],
    )
    assert first["receipt"] == second["receipt"] and first["receipt"]["result"] == "passed"
    assert (fixture.program.repo / "counter-once.txt").read_text() == "x"
    captured = fixture.write(
        "evidence/_verification/attempt-1/captured-finished.json", first["receipt"]
    )
    assert fixture.run("verification-record", "--input", captured) == 0
    snapshot = fp.read_snapshot(fixture.program.repo, fixture.program.directory)
    process = [
        item
        for item in snapshot["timing"]["intervals"]["items"]
        if item["kind"] == "command_process"
    ]
    assert len(process) == 1 and process[0]["closed_seconds"] is not None
    task_timing = fp.read_brief(fixture.program.repo, fixture.program.directory, "S01")["task"][
        "timing"
    ]
    if task_timing.get("status") == "omitted":
        assert task_timing["reason"] == "brief_byte_bound"
        assert task_timing["intervals"]["total"] >= 1
        assert task_timing["intervals"]["shown"] == 0
    else:
        assert (
            task_timing["intervals"]["items"][0]["closed_seconds"] == process[0]["closed_seconds"]
        )
    assert snapshot["evidence_preparation"]["status"] == "ready"
    source_path = fixture.program.repo / "src/S01.py"
    source_bytes = source_path.read_bytes()
    source_path.write_bytes(source_bytes + b"# changed actual input\n")
    changed_snapshot = fp.read_snapshot(fixture.program.repo, fixture.program.directory)
    assert changed_snapshot["evidence_preparation"]["status"] == "stale"
    source_path.write_bytes(source_bytes)
    # The immutable graph retains the actual origin, including zero-output process streams.
    original = fixture.program.path(actual["capture_ref"]["path"])
    original.write_bytes(original.read_bytes() + b" ")
    with pytest.raises(fp.ProgramError):
        fixture.program.load()


def test_mixed_verification_submit_and_complete_batch_uses_exact_projected_predecessors(
    fixture, capsys
):
    from tools.tests.test_feature_program_verification import request, observation, submission

    # Given: immutable inputs bound to each exact projected predecessor, including a real review subject.
    assert fixture.claim() == 0
    original = fixture.program.load()
    host = fp.authoring.BatchHost(fixture.program, publication_base=original)
    clock = "2026-10-15T01:00:00+00:00"
    request_path = fixture.write(
        "evidence/_verification/attempt-1/request-1.json", request(fixture)
    )
    first = host.prepare(
        original, "verification-record", {"input": request_path}, clock, owner="leader"
    )
    finished = observation(fixture, expected_revision=fp.phases.revision(first.state))
    finished_path = fixture.write("evidence/_verification/attempt-1/finished-1.json", finished)
    second = host.prepare(
        first.state, "verification-record", {"input": finished_path}, clock, owner="leader"
    )
    execution = fp.verification.submission_inputs(
        fixture.program, second.state, submission(fixture)
    )["receipt"]
    execution_path = fixture.write("evidence/S01/attempt-1/projected-execution.json", execution)
    third = host.prepare(
        second.state,
        "submit",
        {"id": "S01", "owner": "worker", "receipt": execution_path},
        clock,
        owner="leader",
    )
    submitted = fixture.program.current_attempt(fixture.program.task(third.state, "S01"))[
        "submission"
    ]
    review = {
        "task_id": "S01",
        "attempt": 1,
        "receipt_sha256": submitted["receipt"]["sha256"],
        "source_digest": submitted["source_digest"],
        "verdict": "accepted",
        "reviewer": "reviewer",
        "reviewer_role": "independent",
        "acceptance_complete": True,
        "unresolved_findings": [],
        "evidence": [fixture.evidence("S01", label="projected-independent-review")],
    }
    review_path = fixture.write("evidence/S01/attempt-1/projected-review.json", review)
    operations = [
        {
            "id": "request",
            "operation": "verification-record",
            "payload": {"input": request_path},
            "observed_at": clock,
        },
        {
            "id": "finished",
            "operation": "verification-record",
            "payload": {"input": finished_path},
            "observed_at": clock,
        },
        {
            "id": "submit",
            "operation": "submit",
            "payload": {"id": "S01", "owner": "worker", "receipt": execution_path},
            "observed_at": clock,
        },
        {
            "id": "complete",
            "operation": "complete",
            "payload": {"id": "S01", "review": review_path},
            "observed_at": clock,
        },
    ]
    batch_request = fixture.write(
        "evidence/_coordination/attempt-1/lifecycle-request.json",
        {
            "schema_version": 1,
            "id": "lifecycle-batch",
            "owner_id": "leader",
            "expected_revision": fp.phases.revision(original),
            "scope": ["src/S01.py"],
            "refs": [],
            "operations": operations,
        },
    )
    plan_name = "evidence/_coordination/attempt-1/lifecycle-plan.json"
    _output(fixture, capsys, "batch-prepare", "--input", batch_request, "--result-path", plan_name)
    # When: the actual supported CLI publishes the whole mixed lifecycle once.
    result = _output(
        fixture,
        capsys,
        "batch-publish",
        "--plan",
        plan_name,
        "--owner",
        "leader",
        "--expected-revision",
        fp.phases.revision(original),
    )
    # Then: current normal verification/review evidence passed, with one canonical marker and all ordinary events.
    state = fixture.program.load()
    assert result["status"] == "committed"
    assert fixture.program.task(state, "S01")["status"] == "done"
    assert [event["action"] for event in state["events"][-5:]] == [
        "verification-record",
        "verification-record",
        "submit",
        "complete",
        "record_batch",
    ]
    assert len(state["verification"]["records"]) == 2


def test_standard_http_retains_timing_etag_until_an_actual_observation(fixture, capsys):
    from tools.tests.test_feature_program_web import web, running, request

    # Given: the normal server reads an actual supported submit with a frozen packet.
    result = _submitted_with_handoff(fixture, capsys)
    with running(
        web.create_server(fixture.program.repo, fixture.program.directory, port=0)
    ) as server:
        first_status, first_headers, first_body = request(server)
        first = json.loads(first_body)
        assert first_status == 200
        assert request(server, headers={"If-None-Match": first_headers["ETag"]})[0] == 304
        task_brief = fp.read_brief(fixture.program.repo, fixture.program.directory, "S01")
        assert task_brief["task"]["review_handoff"]["handoff_id"] == result["handoff_id"]
        assert task_brief["task"]["review_handoff"]["next_actor_id"] == "leader"
        state = fixture.program.load()
        raw = {
            "schema_version": 1,
            "operation": "review-handoff-record",
            "id": "http-submit-observed",
            "kind": "submitted",
            "handoff_id": result["handoff_id"],
            "assignment_generation": result["assignment_generation"],
            "subject": result["subject"],
            "packet_ref": result["packet_ref"],
            "actor_id": "leader",
            "expected_revision": fp.phases.revision(state),
            "reason": "Synthetic actual boundary",
            "evidence_refs": [],
            **result["submitted_observation"],
        }
        name = fixture.write("evidence/S01/attempt-1/http-submitted.json", raw)
        # When: an actual retained boundary is published through the ordinary CLI.
        assert fixture.run("review-handoff-record", "--input", name) == 0
        status, headers, body = request(server, headers={"If-None-Match": first_headers["ETag"]})
        # Then: fresh content produces200; unchanged later reads remain304 with stable timing.
        assert status == 200 and headers["ETag"] != first_headers["ETag"]
        current = json.loads(body)
        assert current["timing"] != first["timing"]
        assert request(server, headers={"If-None-Match": headers["ETag"]})[0] == 304


def test_standard_server_explicit_selector_rejects_malformed_preparation(fixture):
    from tools.tests.test_feature_program_web import web, running, request

    # Given: a configured immutable selection is structurally invalid, rather than absent legacy data.
    name = fixture.write("evidence/S01/attempt-1/invalid-selection.json", {"kind": "unknown"})
    with running(
        web.create_server(
            fixture.program.repo, fixture.program.directory, port=0, evidence_preparation=name
        )
    ) as server:
        # When: the real standard endpoint reads its configured selector.
        status, headers, _body = request(server)
        # Then: integrity failure cannot become a fresh successful representation or304.
        assert status == 503 and "ETag" not in headers


@pytest.mark.parametrize("kind", ["reviewer_acknowledged", "review_started"])
def test_broader_declared_review_input_change_blocks_new_native_reliance(fixture, capsys, kind):
    from tools.tests.test_feature_program_review_handoffs import observation

    # Given: a packet freezes actual broader S02 bytes, while task S01 is unchanged.
    result = _submitted_with_handoff(fixture, capsys, review_scope=["src/S02.py"])
    packet = fp.read_json(fixture.program.path(result["packet_ref"]["path"]))
    assert packet["review_inputs"]["source_binding"]["source_digest"] == fp.digest(
        fixture.program.source_snapshot(["src/S02.py"])
    )

    def publish(kind, identifier):
        state = fixture.program.load()
        raw = observation(
            fixture,
            state,
            kind,
            identifier,
            actor_id="leader" if kind == "dispatch_sent" else "reviewer",
            expected_revision=fp.phases.revision(state),
            timestamp_source="native_tool_observation"
            if kind == "dispatch_sent"
            else "reviewer_observation",
        )
        for key in ("context", "review_inputs", "recorded_at"):
            raw.pop(key, None)
        name = fixture.write("evidence/S01/attempt-1/" + identifier + ".json", raw)
        return fixture.run("review-handoff-record", "--input", name)

    assert publish("dispatch_sent", "broader-dispatch") == 0
    if kind == "review_started":
        assert publish("reviewer_acknowledged", "broader-ack") == 0
    before = fixture.program.path("backlog.json").read_bytes()
    # When: only the explicitly declared broader input changes before actual ACK/start recording.
    source = fixture.program.repo / "src/S02.py"
    source.write_bytes(source.read_bytes() + b"# broader input changed\n")
    assert publish(kind, "broader-rejected") == 1
    # Then: no new actual native fact is invented and the existing packet is stale in the standard view.
    assert fixture.program.path("backlog.json").read_bytes() == before
    row = fp.read_snapshot(fixture.program.repo, fixture.program.directory)["review_handoffs"][
        "rows"
    ]["items"][0]
    assert row["currentness"] == "stale"


def test_broader_review_input_change_during_final_submit_guard_cannot_commit(
    fixture, capsys, monkeypatch
):
    # Given: a real ordinary submit reaches final save with a packet bound to broader source bytes.
    _register_reviewer(fixture)
    assert fixture.claim() == 0
    receipt = fixture.receipt()
    name = fixture.write(
        "evidence/S01/attempt-1/racing-handoff.json",
        {"reviewer_id": "reviewer", "review_inputs": {"scope": ["src/S02.py"]}},
    )
    before = fixture.program.path("backlog.json").read_bytes()
    normal = fp.Program.prepare_save

    def race(program, state):
        encoded = normal(program, state)
        if state.get("review_handoffs"):
            source = program.repo / "src/S02.py"
            source.write_bytes(source.read_bytes() + b"# source race\n")
        return encoded

    monkeypatch.setattr(fp.Program, "prepare_save", race)
    # When: broader bytes change after normal final preflight and before canonical publication.
    assert (
        fixture.run("submit", "S01", "--owner", "worker", "--receipt", receipt, "--handoff", name)
        == 1
    )
    # Then: the ordinary guarded save rejects the change and retains the original canonical state.
    assert fixture.program.path("backlog.json").read_bytes() == before


def test_broader_review_frozen_reference_is_retained_without_parsing_raw_json(fixture, capsys):
    # Given: an explicitly selected broader raw-array proof is frozen in the actual packet.
    name = fixture.write("evidence/_coordination/attempt-1/broader-proof.json", ["opaque", 1])
    reference = {"path": name, "sha256": fp.sha(fixture.program.path(name).read_bytes())}
    _submitted_with_handoff(fixture, capsys, review_refs=[reference])
    assert fixture.program.load()["review_handoffs"]["records"]
    # When: its immutable bytes change after adoption.
    fixture.program.path(name).write_bytes(b"changed raw bytes")
    # Then: canonical reliance stays strict, while the ordinary snapshot preserves stale recovery context.
    with pytest.raises(fp.ProgramError):
        fixture.program.load()
    snapshot = fp.read_snapshot(fixture.program.repo, fixture.program.directory)
    assert snapshot["tasks"][0]["status"] == "review"
    assert snapshot["review_handoffs"]["rows"]["items"][0]["currentness"] != "current"


def test_selected_failed_batch_unknown_operation_count_remains_unknown_in_brief(fixture, capsys):
    # Given: actual failed preparation retains an immutable request whose operation list is unavailable.
    assert fixture.claim() == 0
    raw = _note_batch_request(fixture)
    raw["operations"] = None
    name = fixture.write("evidence/_coordination/attempt-1/unknown-count-request.json", raw)
    result_name = "evidence/_coordination/attempt-1/unknown-count-failure.json"
    assert fixture.run("batch-prepare", "--input", name, "--result-path", result_name) == 1
    # When: the standard selected snapshot and its brief project that actual failure.
    snapshot = fp.read_snapshot(
        fixture.program.repo, fixture.program.directory, batch_plan=result_name
    )
    # Then: null counts remain unknown, with no TypeError or fabricated zero/ready fallback.
    assert snapshot["record_batch"]["requested_operation_count"] is None
    assert snapshot["brief"]["record_batch"]["operations"]["total"] is None
    assert snapshot["brief"]["record_batch"]["operations"]["omitted"] is None


def _shared_provenance_rounds(fixture, *, origin_payload="retained"):
    from tools.tests.test_feature_program_evidence_reuse import selection

    assert fixture.claim() == 0
    reuse = fp.flow_module("evidence_reuse")
    origin = fixture.write("design/shared-origin.json", {"proof": origin_payload})
    origin_ref = {"path": origin, "sha256": fp.sha(fixture.program.path(origin).read_bytes())}
    state = fixture.program.load()
    plan = reuse.plan_evidence(fixture.program, state, selection([origin_ref]))
    target = reuse.materialize_evidence(fixture.program, state, plan)["targets"][0]
    for number in (1, 2):
        receipt_path = fixture.receipt(round_number=number)
        receipt = fp.read_json(fixture.program.path(receipt_path))
        receipt["evidence_provenance_ref"] = target["manifest_ref"]
        fixture.write(receipt_path, receipt)
        assert fixture.run("submit", "S01", "--owner", "worker", "--receipt", receipt_path) == 0
        accepted = number == 2
        review_path = fixture.review(accepted=accepted, round_number=number)
        review = fp.read_json(fixture.program.path(review_path))
        review["evidence_provenance_ref"] = target["manifest_ref"]
        fixture.write(review_path, review)
        assert (
            fixture.run("complete" if accepted else "rework", "S01", "--review", review_path) == 0
        )
    return target


def test_shared_provenance_reuses_graph_and_scope_without_collapsing_consuming_facts(
    fixture, monkeypatch
):
    # Given: four real submission/review controls share one exact retained graph and source scope.
    target = _shared_provenance_rounds(fixture)
    state = fixture.program.load()
    reuse = fp.flow_module("evidence_reuse")
    normal_validate = reuse.validate_manifest
    normal_snapshot = fp.Program.source_snapshot
    graph_calls = []
    source_calls = []

    def validate(program, manifest, **kwargs):
        graph_calls.append(manifest["selection_digest"])
        return normal_validate(program, manifest, **kwargs)

    def snapshot(program, scope, budget=None):
        source_calls.append(tuple(scope))
        return normal_snapshot(program, scope, budget)

    monkeypatch.setattr(reuse, "validate_manifest", validate)
    monkeypatch.setattr(fp.Program, "source_snapshot", snapshot)
    # When: the actual provenance adapter assesses all controls in one request.
    facts = fp.verify_provenance(fixture.program, state)
    # Then: graph/source work is shared, but historical/current control facts remain distinct.
    assert len(graph_calls) == 1
    assert source_calls == [("src/S01.py",), ("src/S01.py",)]
    assert len(facts) == 4
    assert [fact["current"] for fact in facts] == [False, False, True, True]
    assert all(fact["manifest_ref"] == target["manifest_ref"] for fact in facts)
    assert len({fp.digest(fact["control_ref"]) for fact in facts}) == 4


@pytest.mark.parametrize("change", ["attempt", "manifest_hash", "control_hash"])
def test_shared_provenance_rechecks_each_consuming_subject_and_hash(fixture, change):
    # Given: the second control reuses an already valid graph, with one invalid binding.
    _shared_provenance_rounds(fixture)
    state = fixture.program.load()
    controls = [
        ref
        for ref, _identifier in fp.control_receipt_refs(state)
        if "evidence_provenance_ref" in fp.read_json(fixture.program.path(ref["path"]))
    ]
    ref = controls[1]
    receipt = fp.read_json(fixture.program.path(ref["path"]))
    match change:
        case "attempt":
            receipt["attempt"] = 2
        case "manifest_hash":
            receipt["evidence_provenance_ref"]["sha256"] = "0" * 64
        case "control_hash":
            receipt["summary"] = "Changed control bytes"
    fixture.write(ref["path"], receipt)
    if change != "control_hash":
        ref["sha256"] = fp.sha(fixture.program.path(ref["path"]).read_bytes())
    # When: a later consuming control is validated in the same request.
    # Then: successful earlier graph validation never authorizes an invalid subject or hash.
    with pytest.raises(fp.ProgramError):
        fp.verify_provenance(fixture.program, state)


@pytest.mark.parametrize("change", ["origin", "copy", "source"])
def test_shared_provenance_rechecks_fresh_request_after_changed_bytes(fixture, change):
    # Given: a complete snapshot already observed a graph shared by four controls.
    target = _shared_provenance_rounds(fixture)
    first = fp.read_snapshot(fixture.program.repo, fixture.program.directory)
    assert first["evidence_preparation"]["counts"]["retained"] > 0
    assert fp.verify_provenance(fixture.program, fixture.program.load())[-1]["current"] is True
    attachment = target["attachments"][0]
    path = (
        fixture.program.repo / "src/S01.py"
        if change == "source"
        else fixture.program.path(
            attachment["origin_ref" if change == "origin" else "attachment_ref"]["path"]
        )
    )
    path.write_bytes(path.read_bytes() + b" ")
    # When: another request captures the changed original/copy/source independently.
    # Then: cached success cannot survive across requests or keep changed inputs ready.
    if change == "source":
        second = fp.read_snapshot(fixture.program.repo, fixture.program.directory)
        assert second["evidence_preparation"]["status"] == "stale"
        assert not any(
            fact["current"]
            for fact in fp.verify_provenance(fixture.program, fixture.program.load())
        )
    else:
        second = fp.read_snapshot(fixture.program.repo, fixture.program.directory)
        assert second["evidence_preparation"]["status"] == "unavailable"
        with pytest.raises(fp.ProgramError):
            fp.verify_provenance(
                fixture.program, state=fp.read_json(fixture.program.path("backlog.json"))
            )


@pytest.mark.parametrize("change", ["origin", "copy", "source"])
def test_shared_provenance_final_capture_guard_rejects_changed_bytes(fixture, monkeypatch, change):
    # Given: the actual snapshot has verified all shared controls before a concurrent change.
    target = _shared_provenance_rounds(fixture)
    attachment = target["attachments"][0]
    path = (
        fixture.program.repo / "src/S01.py"
        if change == "source"
        else fixture.program.path(
            attachment["origin_ref" if change == "origin" else "attachment_ref"]["path"]
        )
    )
    normal = fp.verify_provenance

    def change_after_capture(*args, **kwargs):
        facts = normal(*args, **kwargs)
        path.write_bytes(path.read_bytes() + b" ")
        return facts

    monkeypatch.setattr(fp, "verify_provenance", change_after_capture)
    # When: original/copy/source bytes change after assessment but before final snapshot guards.
    # Then: request-local reuse cannot publish an internally inconsistent successful snapshot.
    with pytest.raises(fp.ProgramError):
        fp.read_snapshot(fixture.program.repo, fixture.program.directory)


def test_safe_path_rejects_outward_root_redirection_after_initial_observation(
    tmp_path, monkeypatch
):
    # Given: filesystem resolution redirects the root between its initial and final observation.
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    candidate = root / "proof.txt"
    candidate.write_bytes(b"inside")
    normal = type(root).resolve
    root_observations = []

    def redirected(path, *args, **kwargs):
        if path == root:
            root_observations.append(path)
            return root if len(root_observations) == 1 else outside
        if path == candidate:
            return outside / candidate.name
        return normal(path, *args, **kwargs)

    monkeypatch.setattr(type(root), "resolve", redirected)
    # When: the requested path resolves outside the originally admitted root.
    # Then: a second redirected root observation cannot authorize the outside path.
    with pytest.raises(fp.ProgramError, match="Path outside root"):
        fp.safe_path(root, candidate.name)


@pytest.mark.parametrize("kind", ["is_symlink", "is_junction"])
def test_lexical_path_reuse_keeps_filesystem_link_checks_fresh(tmp_path, monkeypatch, kind):
    # Given: a safe lexical name has already been admitted for an ordinary file.
    candidate = tmp_path / "proof.txt"
    candidate.write_bytes(b"inside")
    assert fp.safe_path(tmp_path, candidate.name) == candidate
    _mock_link_lstat(monkeypatch, candidate, kind)
    # When: the same lexical name now resolves through a linked component.
    # Then: lexical cache reuse cannot skip fresh symlink or junction rejection.
    with pytest.raises(fp.ProgramError, match="Linked path rejected"):
        fp.safe_path(tmp_path, candidate.name)


def test_lexical_path_cache_bypasses_long_valid_names_without_rejection(monkeypatch):
    # Given: an otherwise valid path exceeds the lexical cache key-size bound.
    name = "/".join(["segment"] * 150)
    normal = fp._safe_relative
    validations = []
    before = fp._cached_safe_relative.cache_info().currsize

    def validate(value):
        validations.append(value)
        return normal(value)

    monkeypatch.setattr(fp, "_safe_relative", validate)
    # When: repeated calls validate the long path through the ordinary uncached validator.
    assert fp.safe_relative(name) == name
    assert fp.safe_relative(name) == name
    # Then: valid input is preserved, not rejected or retained in the cache.
    assert validations == [name, name]
    assert fp._cached_safe_relative.cache_info().currsize == before


def test_lexical_path_cache_bypasses_string_subclasses_and_nonstring_inputs(monkeypatch):
    # Given: a custom string or nonstring is outside the plain-string cache contract.
    class CustomPath(str):
        pass

    normal = fp._safe_relative
    validations = []
    before = fp._cached_safe_relative.cache_info().currsize

    def validate(value):
        validations.append(value)
        return normal(value)

    monkeypatch.setattr(fp, "_safe_relative", validate)
    name = CustomPath("proof.txt")
    # When: repeated custom values and a nonstring reach the identical uncached validator.
    assert fp.safe_relative(name) == name
    assert fp.safe_relative(name) == name
    with pytest.raises(TypeError):
        fp.safe_relative(["proof.txt"])
    # Then: custom/nonstring values do not become retained cache keys or bypass validation.
    assert validations == [name, name, ["proof.txt"]]
    assert fp._cached_safe_relative.cache_info().currsize == before


@pytest.mark.parametrize("name", ["../outside.txt", ".git/config", ".env.local", "bad\\path"])
def test_lexical_path_cache_never_retains_rejected_inputs(name, monkeypatch):
    # Given: an unsafe lexical input has not passed the existing validator.
    normal = fp._safe_relative
    validations = []
    before = fp._cached_safe_relative.cache_info().currsize

    def validate(value):
        validations.append(value)
        return normal(value)

    monkeypatch.setattr(fp, "_safe_relative", validate)
    # When: that same unsafe input is requested repeatedly.
    for _attempt in range(2):
        with pytest.raises(fp.ProgramError):
            fp.safe_relative(name)
    # Then: exceptions are never retained as successful lexical admissions.
    assert validations == [name, name]
    assert fp._cached_safe_relative.cache_info().currsize == before


def test_lexical_path_cache_has_finite_entry_retention():
    # Given: more distinct short valid paths than the declared finite lexical capacity.
    capacity = fp._cached_safe_relative.cache_info().maxsize
    assert type(capacity) is int and 0 < capacity <= 4096
    # When: every exact path passes through the public lexical validator.
    for index in range(capacity + 2):
        name = f"bounded-proof-{index}.txt"
        assert fp.safe_relative(name) == name
    # Then: retained strings/results remain bounded independently of request count.
    assert fp._cached_safe_relative.cache_info().currsize == capacity


def test_verification_projection_reuses_revision_without_changing_reference_facts(
    fixture, monkeypatch
):
    from tools.tests.test_feature_program_verification import request, observation

    # Given: actual request/finished facts require several exact registry references.
    assert fixture.claim() == 0
    name = fixture.write("evidence/_verification/attempt-1/revision-request.json", request(fixture))
    assert fixture.run("verification-record", "--input", name) == 0
    name = fixture.write(
        "evidence/_verification/attempt-1/revision-finished.json", observation(fixture)
    )
    assert fixture.run("verification-record", "--input", name) == 0
    state = fixture.program.load()
    normal_reference = fp.verification._reference

    def uncached_reference(value, pointer, task_id=None, **_kwargs):
        return normal_reference(value, pointer, task_id)

    with monkeypatch.context() as baseline:
        baseline.setattr(fp.verification, "_reference", uncached_reference)
        expected = fp.verification.projection(fixture.program, state, limit=None)
    normal = fp.phases.revision
    calls = []

    def revision(value):
        calls.append(value)
        return normal(value)

    monkeypatch.setattr(fp.phases, "revision", revision)
    # When: a full projection constructs every lane, task and history reference.
    actual = fp.verification.projection(fixture.program, state, limit=None)
    # Then: one serialization preserves the entire projection and immutable input state.
    assert len(calls) == 1
    assert calls[0] is state
    assert actual == expected
    assert normal(state) == actual["tasks"]["S01"]["reference"]["revision"]


def test_verification_projection_revision_is_fresh_for_each_state(fixture, monkeypatch):
    from tools.tests.test_feature_program_verification import request

    # Given: a real request has already been projected with its current registry revision.
    assert fixture.claim() == 0
    name = fixture.write("evidence/_verification/attempt-1/fresh-request.json", request(fixture))
    assert fixture.run("verification-record", "--input", name) == 0
    first_state = fixture.program.load()
    first = fp.verification.projection(fixture.program, first_state, limit=None)
    first_revision = fp.phases.revision(first_state)
    assert first["history"]["reference"]["revision"] == first_revision
    # When: an ordinary unrelated note creates a new actual registry state.
    assert (
        fixture.run(
            "note",
            "S01",
            "--owner",
            "worker",
            "--reason",
            "New retained progress",
            "--next-action",
            "Inspect current verification",
        )
        == 0
    )
    state = fixture.program.load()
    expected_revision = fp.phases.revision(state)
    assert expected_revision != first_revision
    normal = fp.phases.revision
    calls = []

    def revision(value):
        calls.append(value)
        return normal(value)

    monkeypatch.setattr(fp.phases, "revision", revision)
    second = fp.verification.projection(fixture.program, state, limit=None)
    # Then: no prior invocation's token survives, and ordinary helper callers remain fresh.
    assert len(calls) == 1
    assert second["history"]["reference"]["revision"] == expected_revision
    assert second["tasks"]["S01"]["reference"]["revision"] == expected_revision
    assert (
        fp.verification._reference(state, "/verification/records")["revision"] == expected_revision
    )
    assert (
        fp.verification._collection(state, [], "/verification/records")["reference"]["revision"]
        == expected_revision
    )


def make_snapshot_history_fixture(repo, *, task_count=60, rounds=3):
    """Build new isolated synthetic ordinary history for an explicitly invoked scale check.

    No live program is read or copied; fake Git identity is fixture-only. Native/provider
    observations are deliberately absent, so the generated packets never claim real dispatch.
    """
    from tools.tests.test_feature_program import HEAD, make_small_program

    fp.require(not repo.exists(), "History fixture destination must not exist")
    fp.require(
        0 < task_count <= 120 and 0 < rounds <= 12 and task_count * rounds <= 240,
        "Fixture history must stay inside the ordinary evidence-file bound",
    )
    original_git = fp.git
    fp.git = lambda _repo, argv: "" if argv[0] == "status" else HEAD
    try:
        sample = make_small_program(
            repo, "snapshot-scale-synthetic", tuple(f"S{i:03d}" for i in range(task_count))
        )
        _register_reviewer(sample)
        for index in range(task_count):
            task_id = f"S{index:03d}"
            worker = f"synthetic-worker-{index}"
            assert sample.claim(task_id, owner=worker) == 0
            for number in range(1, rounds + 1):
                execution = sample.receipt(task_id, number)
                handoff = sample.write(
                    f"evidence/{task_id}/attempt-1/handoff-{number}.json",
                    {"reviewer_id": "reviewer"},
                )
                assert (
                    sample.run(
                        "submit",
                        task_id,
                        "--owner",
                        worker,
                        "--receipt",
                        execution,
                        "--handoff",
                        handoff,
                    )
                    == 0
                )
                accepted = number == rounds
                review = sample.review(task_id, accepted, number)
                assert (
                    sample.run("complete" if accepted else "rework", task_id, "--review", review)
                    == 0
                )
        sample.program.load()
        return sample
    finally:
        fp.git = original_git


def test_larger_history_factory_uses_normal_records_and_preserves_full_population(tmp_path, capsys):
    # Given a fresh isolated fixture generated through ordinary submit/review controls.
    sample = make_snapshot_history_fixture(tmp_path / "history", task_count=2, rounds=2)
    capsys.readouterr()
    # When the real snapshot captures all retained history.
    snapshot = fp.read_snapshot(sample.program.repo, sample.program.directory)
    # Then all exact handoff subjects survive, without invented native observations.
    assert snapshot["counts"]["done"] == 2
    assert snapshot["review_handoffs"]["rows"]["total"] == 4
    assert all(
        observation["kind"] in {"ready_recorded", "intent"} and observation["observed_at"] is None
        for row in snapshot["review_handoffs"]["rows"]["items"]
        for observation in row["observations"]
    )
    assert snapshot["integrity"]["status"] == "fresh"


def _enable_storage(fixture):
    with fixture.program.lock():
        return fp.storage_enable(
            fixture.program, owner="leader", reason="Synthetic storage fixture"
        )


def test_storage_enable_preserves_exact_body_backup_and_complete_snapshot(fixture, monkeypatch):
    # Given: a legacy BODY has meaningful whitespace and rendered views.
    from tools.tests.test_feature_program_web import web

    body = fixture.program.read_state_bytes()
    noncanonical = json.dumps(json.loads(body), ensure_ascii=True).encode()
    fixture.program.path("backlog.json").write_bytes(noncanonical)
    before = fp.read_snapshot(fixture.program.repo, fixture.program.directory)
    # When: explicit enable changes only the physical representation.
    result = _enable_storage(fixture)
    observation = fixture.program.read_registry_observation()
    # Then: logical bytes/history and semantic projection remain exact.
    assert observation.body_bytes == noncanonical
    assert fixture.program.path(result["backup"]["path"]).read_bytes() == noncanonical

    def unexpected_full(*_args, **_kwargs):
        raise AssertionError("The complete current model should be usable")

    monkeypatch.setattr(fp, "_capture_projection", unexpected_full)
    after = fp.read_snapshot(fixture.program.repo, fixture.program.directory)
    assert web.snapshot_etag(after) == web.snapshot_etag(before)
    assert not any(
        item["path"] == ".feature-program.lock" for item in after["brief"].get("recovery", [])
    )


def test_storage_normal_write_prepares_future_views_without_replaying_history(fixture):
    # Given: enabled state has an immutable matching initial model.
    from tools.tests.test_feature_program_web import web

    _enable_storage(fixture)
    original = fixture.program.load()
    # When: a normal supported business command commits one language event.
    assert (
        fixture.run(
            "language", "--owner", "leader", "--reason", "Synthetic language", "--value", "ko"
        )
        == 0
    )
    observation = fixture.program.read_registry_observation()
    model = fp.flow_module("storage").load_model(
        fixture.program.repo, fixture.program.directory, observation.model_id
    )
    fast = fp._read_materialized_projection(fixture.program.repo, fixture.program.directory)
    strict = fp._read_projection(fixture.program.repo, fixture.program.directory, brief_only=True)
    # Then: one new business event and future view/lock postconditions are current.
    assert len(fixture.program.load()["events"]) == len(original["events"]) + 1
    assert model["binding"]["body_sha256"] == observation.body_sha256
    assert fast is not None and model["eligible"] is True
    assert fast["brief"] == strict
    assert not any("Derived view drift:" in warning for warning in fast["integrity"]["warnings"])
    assert web.snapshot_etag(fast) == web.snapshot_etag(model["snapshot"])


def test_storage_explicit_repair_preserves_body_and_original_backup(fixture, monkeypatch):
    # Given: producer identity changes without a business state transition.
    _enable_storage(fixture)
    original = fixture.program.read_registry_observation()
    backups = {
        p: p.read_bytes()
        for p in fixture.program.directory.glob("recovery/storage/*/original-backlog.json")
    }
    module = fp.flow_module("read_model")
    actual = module.producer_identity

    def changed(repo):
        value = actual(repo)
        value["runtime"]["parser_version"] += 1
        return value

    monkeypatch.setattr(module, "producer_identity", changed)
    assert fp._read_materialized_projection(fixture.program.repo, fixture.program.directory) is None
    # When: the normal explicit storage command rebuilds a selected object.
    result = _enable_storage(fixture)
    # Then: BODY/backup are untouched and a fresh eligible model is selected.
    assert result["status"] == "repaired"
    assert fixture.program.read_state_bytes() == original.body_bytes
    assert fixture.program.read_registry_observation().model_id != original.model_id
    assert all(path.read_bytes() == content for path, content in backups.items())
    assert (
        fp._read_materialized_projection(fixture.program.repo, fixture.program.directory)
        is not None
    )


def test_storage_ineligible_normal_write_commits_without_servable_dto(fixture, monkeypatch):
    # Given: an enabled producer encounters an explicitly unrepresentable branch.
    _enable_storage(fixture)
    module = fp.flow_module("read_model")
    original = module.Collector.expect_view

    def opaque(self, root, name, content):
        self.ineligible("synthetic_unrepresentable_branch")
        return original(self, root, name, content)

    monkeypatch.setattr(module.Collector, "expect_view", opaque)
    # When: ordinary valid business state is published.
    assert (
        fixture.run(
            "language", "--owner", "leader", "--reason", "Synthetic opaque branch", "--value", "ko"
        )
        == 0
    )
    observation = fixture.program.read_registry_observation()
    model = fp.flow_module("storage").load_model(
        fixture.program.repo, fixture.program.directory, observation.model_id
    )
    # Then: a bounded unavailable model never becomes a cached passing DTO.
    assert fixture.program.load()["communication_language"] == "ko"
    assert model["eligible"] is False and "snapshot" not in model
    assert fp._read_materialized_projection(fixture.program.repo, fixture.program.directory) is None


def test_storage_initial_enable_rejects_ineligible_model_without_swapping_registry(
    fixture, monkeypatch
):
    # Given: a deterministic unsupported branch is identified during preparation.
    before = fixture.program.path("backlog.json").read_bytes()
    module = fp.flow_module("read_model")
    original = module.Collector.expect_view

    def opaque(self, root, name, content):
        self.ineligible("synthetic_unrepresentable_branch")
        original(self, root, name, content)

    monkeypatch.setattr(module.Collector, "expect_view", opaque)
    # When: explicit enable is attempted.
    with pytest.raises(fp.ProgramError):
        _enable_storage(fixture)
    # Then: the original legacy authority is preserved.
    assert fixture.program.path("backlog.json").read_bytes() == before


def test_storage_real_guard_failure_does_not_commit_normal_operation(fixture):
    # Given: enabled state has a valid proposed business transition.
    _enable_storage(fixture)
    before = fixture.program.read_registry_observation()
    state = fixture.program.load()
    state["communication_language"] = "ko"

    def fail():
        raise fp.ProgramError("Synthetic actual publication guard rejection")

    # When: the normal guard rejects after model preparation.
    with fixture.program.lock(), pytest.raises(fp.ProgramError):
        fixture.program.save(state, before_publish=fail)
    # Then: no business transition or selected-object change occurred.
    assert fixture.program.read_registry_observation() == before


def test_storage_same_body_selector_race_blocks_publication(fixture):
    # Given: an enabled state is prepared under the normal lock.
    _enable_storage(fixture)
    storage = fp.flow_module("storage")
    before = fixture.program.read_registry_observation()
    state = fixture.program.load()
    state["communication_language"] = "ko"
    raced = storage.encode_registry(before.body_bytes, "f" * 64)

    def race():
        fixture.program.path("backlog.json").write_bytes(raced)

    # When: another physical selection appears after preparation.
    with fixture.program.lock(), pytest.raises(fp.ProgramError):
        fixture.program.save(state, before_publish=race)
    # Then: raw CAS prevents this operation from overwriting the observed winner.
    assert fixture.program.path("backlog.json").read_bytes() == raced


@pytest.mark.parametrize(
    "drift", ["view", "missing_source", "changed_source", "new_publication_marker", "foreign_lock"]
)
def test_storage_changed_dependency_uses_strict_fallback(fixture, drift):
    # Given: a complete current selected model.
    _enable_storage(fixture)
    program = fixture.program
    # When: one of its actual observed inputs changes.
    if drift == "view":
        program.path("BOARD.md").write_text("Synthetic drift", encoding="utf-8")
    elif drift == "missing_source":
        program.path("source/feature-opportunities.md").unlink()
    elif drift == "changed_source":
        program.path("source/feature-opportunities.md").write_text(
            "Synthetic changed source", encoding="utf-8"
        )
    elif drift == "new_publication_marker":
        program.path(fp.planning.TRACKING).write_text("{}", encoding="utf-8")
    else:
        program.path(".feature-program.lock").write_bytes(b'{"pid":1}')
    # Then: a stale success cannot be returned from the selected object.
    assert fp._read_materialized_projection(program.repo, program.directory) is None


def test_storage_final_raw_read_barrier_rejects_selector_only_race(fixture, monkeypatch):
    # Given: a valid model's full dependency validation succeeds.
    _enable_storage(fixture)
    module = fp.flow_module("read_model")
    validate = module.validate
    original = fixture.program.read_registry_observation()

    def raced(*args, **kwargs):
        result = validate(*args, **kwargs)
        fixture.program.path("backlog.json").write_bytes(
            fp.flow_module("storage").encode_registry(original.body_bytes, "e" * 64)
        )
        return result

    monkeypatch.setattr(module, "validate", raced)
    # When: physical selection changes just before the closing registry barrier.
    with pytest.raises(fp.ProgramError):
        fp.read_snapshot(fixture.program.repo, fixture.program.directory)
    # Then: unchanged logical bytes cannot authorize a stale response.
    assert fixture.program.read_state_bytes() == original.body_bytes


def test_storage_relocation_keeps_raw_archive_and_publishes_legacy_destination(fixture):
    # Given: the enabled source has an exact immutable envelope.
    _enable_storage(fixture)
    original = fixture.program.read_registry_observation()
    # When: ordinary relocation copies evidence and republishes changed source identity.
    assert (
        fixture.run(
            "relocate",
            "--owner",
            "leader",
            "--reason",
            "Synthetic move",
            "--destination",
            "moved-storage",
        )
        == 0
    )
    target = fp.Program(fixture.program.repo, fixture.program.repo / "moved-storage")
    state = target.load()
    # Then: the archive hashes raw storage and destination selects no stale root-bound model.
    assert target.read_registry_observation().format == "json"
    event = next(item for item in state["events"] if item["action"] == "relocate")
    archived = target.path(
        f"recovery/relocations/{event['original_registry_sha256']}/original-backlog.json"
    )
    assert archived.read_bytes() == original.raw_bytes
    assert (
        fp.planning.coverage_source_identity(target, state, state["source"], fp.ReadBudget())[
            "path"
        ]
        == event["previous_source"]["path"]
    )


@pytest.mark.parametrize("failure", ["bounded", "permission", "linked_admission"])
def test_storage_caught_source_failure_is_nonservable_after_repair(fixture, monkeypatch, failure):
    # Given: actual ordinary review projection catches a source observation failure.
    from pathlib import Path
    from tools.tests.test_feature_program_web import web

    assert fixture.claim() == 0
    fixture.submit()
    program = fixture.program
    module = fp.flow_module("read_model")
    observation = program.read_registry_observation()
    collector = module.Collector(
        program.repo,
        program.directory,
        program_id=program.load()["program_id"],
        body_sha256=observation.body_sha256,
        producer=module.producer_identity(program.repo),
        query=module.QUERY,
    )
    source = program.repo / "src/S01.py"
    normal_read = fp.bounded_bytes
    normal_open = Path.open
    with monkeypatch.context() as active:
        if failure == "bounded":
            active.setattr(
                fp,
                "bounded_bytes",
                lambda path, limit: normal_read(path, 0 if path == source else limit),
            )
        elif failure == "permission":

            def unavailable(path, *args, **kwargs):
                if path == source:
                    raise PermissionError("Synthetic source admission failure")
                return normal_open(path, *args, **kwargs)

            active.setattr(Path, "open", unavailable)
        else:
            _mock_link_lstat(active, source, "is_symlink")
        with module.collect(collector):
            stale = fp.read_snapshot(program.repo, program.directory)
        model = collector.finish(stale)
    # When: that actual source becomes readable/admissible again with original bytes.
    repaired = fp.read_snapshot(program.repo, program.directory)
    # Then: no persisted stale/warning DTO can survive the repaired source as cached success.
    assert model["eligible"] is False and "snapshot" not in model
    assert (
        module.validate(
            model, program, observation=observation, observer=fp._ClosingObserver(program)
        )
        is None
    )
    assert web.snapshot_etag(stale) != web.snapshot_etag(repaired)


def test_storage_final_dependency_close_runs_after_normal_callback(fixture):
    # Given: a valid new state and a callback that changes a noncanonical model input.
    _enable_storage(fixture)
    original = fixture.program.read_registry_observation()
    state = fixture.program.load()
    state["communication_language"] = "ko"

    def changed_source():
        fixture.program.path("source/feature-opportunities.md").write_text(
            "Changed after preparation", encoding="utf-8"
        )

    # When: the actual callback changes source after the immutable object was staged.
    with fixture.program.lock(), pytest.raises(fp.ProgramError):
        fixture.program.save(state, before_publish=changed_source)
    # Then: complete final dependency closure prevents selecting a stale computed model.
    assert fixture.program.read_registry_observation().raw_bytes == original.raw_bytes


@pytest.mark.parametrize("missing", [False, True])
def test_storage_explicit_repair_renders_drifted_views_without_history_replay(fixture, missing):
    # Given: committed state has drifted or missing derived output.
    _enable_storage(fixture)
    original = fixture.program.read_state_bytes()
    if missing:
        fixture.program.path("BOARD.md").unlink()
    else:
        fixture.program.path("BOARD.md").write_text("Synthetic drift", encoding="utf-8")
    # When: explicit derived repair publishes the matching object and normal pure views.
    result = _enable_storage(fixture)
    # Then: the repaired model is usable without altering the logical body.
    assert result["status"] == "repaired"
    assert fixture.program.read_state_bytes() == original
    assert (
        fp._read_materialized_projection(fixture.program.repo, fixture.program.directory)
        is not None
    )


def test_storage_initial_enable_reclaims_only_unselected_interrupted_stages(fixture, monkeypatch):
    # Given: explicit enable is interrupted after staging but before canonical publication.
    original = fixture.program.read_registry_observation()
    close = fp._close_staged_model
    counter = iter(range(20))
    monkeypatch.setattr(fp, "utc", lambda: f"2026-10-15T01:00:{next(counter):02d}+00:00")

    def fail(_program, _staged):
        raise fp.ProgramError("Synthetic interruption before atomic publication")

    with monkeypatch.context() as active:
        active.setattr(fp, "_close_staged_model", fail)
        for _ in range(2):
            with pytest.raises(fp.ProgramError):
                _enable_storage(fixture)
    assert fixture.program.read_registry_observation() == original
    # When: the actual explicit operation resumes under a fresh normal writer lock.
    result = _enable_storage(fixture)
    # Then: orphan stages cannot exhaust retention or replay business state.
    assert result["status"] == "enabled"
    assert fixture.program.read_state_bytes() == original.body_bytes
    assert close is fp._close_staged_model


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_storage_explicit_repair_rebuilds_only_damaged_selected_model(fixture, damage):
    # Given: logical state is valid but the schema-valid derived store lost its selected object.
    import sqlite3

    _enable_storage(fixture)
    program = fixture.program
    original = program.read_registry_observation()
    storage = fp.flow_module("storage")
    with sqlite3.connect(storage._store_path(program.repo, program.directory)) as connection:
        if damage == "missing":
            connection.execute("DELETE FROM model_objects WHERE object_id=?", (original.model_id,))
        else:
            payload = b"corrupt derived bytes"
            connection.execute(
                "UPDATE model_objects SET payload=?,byte_length=? WHERE object_id=?",
                (payload, len(payload), original.model_id),
            )
    assert fp._read_materialized_projection(program.repo, program.directory) is None
    # When: the authorized explicit repair rebuilds the exact same logical BODY.
    result = _enable_storage(fixture)
    # Then: no business history is replayed and the rebuilt selection is usable.
    assert result["status"] == "repaired"
    assert program.read_state_bytes() == original.body_bytes
    assert fp._read_materialized_projection(program.repo, program.directory) is not None


def test_storage_prepared_provenance_is_assessed_once_with_exact_snapshot(fixture, monkeypatch):
    # Given: real retained controls require full graph and source currentness assessment.
    _shared_provenance_rounds(fixture)
    expected = fp.read_snapshot(fixture.program.repo, fixture.program.directory)[
        "evidence_preparation"
    ]
    normal = fp.verify_provenance
    calls = []

    def counted(program, state, budget=None):
        calls.append(state)
        return normal(program, state, budget)

    monkeypatch.setattr(fp, "verify_provenance", counted)
    # When: a normal explicit writer prepares and selects the exact complete model.
    _enable_storage(fixture)
    observation = fixture.program.read_registry_observation()
    model = fp.flow_module("storage").load_model(
        fixture.program.repo, fixture.program.directory, observation.model_id
    )
    # Then: one strict assessment supplies the same retained facts and final-current model.
    assert len(calls) == 1
    assert model["eligible"] is True
    assert model["snapshot"]["evidence_preparation"] == expected
    assert (
        fp._read_materialized_projection(fixture.program.repo, fixture.program.directory)
        is not None
    )


@pytest.mark.parametrize("change", ["origin", "copy"])
def test_storage_prepared_provenance_final_close_rejects_transitive_mutation(
    fixture, monkeypatch, change
):
    # Given: an enabled program has a strictly assessed retained origin and its immutable copy.
    target = _shared_provenance_rounds(fixture)
    _enable_storage(fixture)
    state = fixture.program.load()
    state["communication_language"] = "ko"
    original = fixture.program.read_registry_observation()
    attachment = target["attachments"][0]
    path = fixture.program.path(
        attachment["origin_ref" if change == "origin" else "attachment_ref"]["path"]
    )

    def changed_evidence():
        path.write_bytes(path.read_bytes() + b" changed after preparation")

    # When: the normal callback changes transitive proof after prepared facts were reused.
    with fixture.program.lock(), pytest.raises(fp.ProgramError):
        fixture.program.save(state, before_publish=changed_evidence)
    # Then: the fresh final model image rejects publication and leaves the exact registry intact.
    assert fixture.program.read_registry_observation().raw_bytes == original.raw_bytes


@pytest.mark.parametrize("selected", [False, True])
def test_storage_prepared_provenance_does_not_authorize_later_reads(fixture, monkeypatch, selected):
    # Given: earlier writer facts were valid, but a retained origin has since changed.
    target = _shared_provenance_rounds(fixture)
    _enable_storage(fixture)
    path = fixture.program.path(target["attachments"][0]["origin_ref"]["path"])
    path.write_bytes(path.read_bytes() + b" later changed")
    normal = fp.verify_provenance
    calls = []

    def counted(program, state, budget=None):
        calls.append(state)
        return normal(program, state, budget)

    monkeypatch.setattr(fp, "verify_provenance", counted)
    # When: an ordinary or explicitly selected read captures its own current evidence image.
    if selected:
        name = fixture.write("design/invalid-selected-evidence.json", {"kind": "unknown"})
        with pytest.raises(fp.ProgramError):
            fp.read_snapshot(
                fixture.program.repo, fixture.program.directory, evidence_preparation=name
            )
    else:
        snapshot = fp.read_snapshot(fixture.program.repo, fixture.program.directory)
        assert snapshot["evidence_preparation"]["status"] == "unavailable"
    # Then: retained writer facts cannot skip either the independent check or explicit selection.
    assert len(calls) == 1


@pytest.mark.parametrize("change", ["identity", "content"])
def test_storage_prepared_provenance_rejects_transplanted_state(fixture, monkeypatch, change):
    from dataclasses import replace

    # Given: a real writer produced checked facts for one exact proposed state.
    original = fixture.program.read_registry_observation()
    capture = fp._capture_projection

    def swapped(*args, **kwargs):
        proposed = kwargs["_proposed"]
        if change == "identity":
            prepared = replace(proposed.prepared, state=deepcopy(proposed.prepared.state))
        else:
            proposed.prepared.state["communication_language"] = "ko"
            prepared = proposed.prepared
        kwargs["_proposed"] = replace(proposed, prepared=prepared)
        return capture(*args, **kwargs)

    monkeypatch.setattr(fp, "_capture_projection", swapped)
    # When: an equal but different state, or changed bytes, tries to consume that exact proof.
    with pytest.raises(fp.ProgramError, match="Prepared state changed before projection"):
        _enable_storage(fixture)
    # Then: the original registry is never selected with another state's facts.
    assert fixture.program.read_registry_observation().raw_bytes == original.raw_bytes


def test_storage_prepared_provenance_preserves_projection_transitive_byte_budget(
    fixture, monkeypatch
):
    # Given: valid transitive origin/copy bytes exceed the projection budget, not save validation.
    _shared_provenance_rounds(fixture, origin_payload="x" * (16 * 1024))
    _enable_storage(fixture)
    original = fixture.program.read_registry_observation()
    state = fixture.program.load()
    state["communication_language"] = "ko"
    normal = fp.ReadBudget

    def reduced_budget(remaining=72 * 1024 * 1024, cached=None):
        return normal(32 * 1024 if remaining == 64 * 1024 * 1024 else remaining, cached)

    monkeypatch.setattr(fp, "ReadBudget", reduced_budget)
    strict = fp._capture_projection(
        fixture.program.repo, fixture.program.directory, fp._SourceObservations(), False, None
    )
    assert strict["evidence_preparation"]["status"] == "unavailable"
    # When: normal enabled publication builds from the larger valid save-ledger proof.
    with fixture.program.lock():
        fixture.program.save(state)
    observation = fixture.program.read_registry_observation()
    model = fp.flow_module("storage").load_model(
        fixture.program.repo, fixture.program.directory, observation.model_id
    )
    # Then: the smaller projection admission cannot become a servable ready cached result.
    assert observation.body_sha256 != original.body_sha256
    assert model["eligible"] is False
    assert "snapshot" not in model
    assert "bounded_read_unavailable" in model["reasons"]


def test_storage_prepared_provenance_readset_tracks_warm_hits_without_unrelated_bytes(
    fixture, monkeypatch
):
    # Given: the provenance graph is already warm alongside an unrelated artifact.
    target = _shared_provenance_rounds(fixture)
    state = fixture.program.load()
    unrelated = fixture.program.path("design/unrelated.bin")
    unrelated.write_bytes(b"not a provenance dependency")
    budget = fp.ReadBudget()
    budget.read(unrelated, 1024)
    fp.verify_provenance(fixture.program, state, budget)
    normal = fp.ReadBudget

    def warmed_budget(remaining=72 * 1024 * 1024, cached=None):
        return (
            budget
            if remaining == 72 * 1024 * 1024 and cached is None
            else normal(remaining, cached)
        )

    monkeypatch.setattr(fp, "ReadBudget", warmed_budget)
    # When: a strict save validation retains only its provenance call's already-warm reads.
    checked = fixture.program.validate(state)
    reads = checked.provenance_reads
    assert reads is not None
    paths = {item.path for item in reads}
    assert unrelated not in paths
    assert fixture.program.path(target["manifest_ref"]["path"]) in paths
    assert fixture.program.path(target["attachments"][0]["origin_ref"]["path"]) in paths
    # Then: replay charges warm controls and transitive bytes once, including own per-path bounds.
    receiving = normal(sum(len(item.content) for item in reads))
    receiving._charge_provenance(reads)
    assert receiving.remaining == 0
    receiving._charge_provenance(reads)
    assert receiving.remaining == 0
    changed = reads[0]
    conflicting = normal(cached={changed.path: b"different already observed image"})
    with pytest.raises(fp.ProgramError):
        conflicting._charge_provenance(reads)
