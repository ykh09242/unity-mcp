"""Reviewed presentation uses real isolated programs and preserves original authority."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from tools.tests.test_feature_program_phases import GateFixture, gate  # noqa: F401
from tools.tests.test_feature_program_authoring import preview, normalized, tree
from tools.tests.test_feature_program import fp

p = fp.presentation


def language(gate):
    assert (
        gate.fixture.run(
            "language", "--value", "ko", "--owner", "director", "--reason", "명시한 세션 언어"
        )
        == 0
    )


def inventory(gate):
    state = gate.program.load()
    return p.projection(gate.program, state, inventory=True)


def receipt(
    gate,
    *,
    identifier="translation-1",
    kind="objective",
    field="title",
    mode="translated",
    text="검증 목표",
    action="set",
) -> tuple[str, fp.Record]:
    state = gate.program.load()
    row = next(
        row
        for row in inventory(gate)["targets"]
        if row["target"]["kind"] == kind and row["target"]["field"] == field
    )
    op = {"action": action, "target": row["target"]}
    if action == "set":
        op["mode"] = mode
        if mode != "original":
            op["text"] = text
    value: fp.Record = {
        "schema_version": 1,
        "operation": "presentation-record",
        "id": identifier,
        "program_id": state["program_id"],
        "actor_id": "director",
        "locale": "ko",
        "reason": "검토한 표시 문구",
        "authors": ["writer"],
        "expected_revision": None,
        "operations": [op],
        "review": None,
    }
    binding = p.bind_input(gate.program, state, value)
    value.update({name: binding[name] for name in ("expected_revision", "operations")})
    review = {
        "schema_version": 1,
        "operation": "presentation-review",
        "program_id": state["program_id"],
        "locale": "ko",
        "payload_sha256": binding["review_payload_sha256"],
        "reviewer_id": "language-reviewer",
        "reviewer_role": "independent",
        "verdict": "accepted",
        "unresolved_findings": [],
        "summary": "원문 의미와 고정 식별자를 검토함 (격리된 합성 사례)",
    }
    review_name = gate.fixture.write(p.PREFIX + identifier + "-review.json", review)
    value = {
        **value,
        "review": {
            "path": review_name,
            "sha256": fp.sha(gate.program.path(review_name).read_bytes()),
        },
    }
    return gate.fixture.write(p.PREFIX + identifier + ".json", value), value


def record(gate, **kwargs) -> fp.Record:
    name, value = receipt(gate, **kwargs)
    assert gate.fixture.run("presentation-record", "--input", name) == 0
    return value


def verification_request(gate):
    """Publish a real isolated request; no product check execution is implied."""
    from tools.tests.test_feature_program_verification import request

    gate.ready()
    language(gate)
    assert gate.fixture.claim() == 0
    value = request(gate.fixture, verifier_id="worker")
    name = gate.fixture.write("evidence/_verification/attempt-1/request-1.json", value)
    assert gate.fixture.run("verification-record", "--input", name) == 0
    return gate.program.load()["verification"]["records"][-1]


def legacy_verification_receipt(gate):
    observed = verification_request(gate)
    name, value = receipt(gate, kind="verification", field="reason")
    context = value["operations"][0]["binding"]["context"]
    assert context.pop("verification_context_sha256") == fp.digest(observed["context"])
    context["verification_context"] = deepcopy(observed["context"])
    # Author the synthetic legacy review over its original full-context spelling.
    review_name = value["review"]["path"]
    review = fp.read_json(gate.program.path(review_name))
    review["payload_sha256"] = p.review_payload(value)
    gate.fixture.write(review_name, review)
    value["review"]["sha256"] = fp.sha(gate.program.path(review_name).read_bytes())
    gate.fixture.write(name, value)
    return name, value


def test_verification_catalog_digest_bounds_large_context_once(gate, monkeypatch):
    state = gate.program.load()
    context = {"source": [{"path": f"src/{index}.py", "sha256": "a" * 64} for index in range(1000)]}
    state["verification"] = {
        "records": [
            {
                "input": {
                    "id": "large",
                    "task_id": "S01",
                    "reason": "Review inputs",
                    "next_trigger": "Inspect result",
                },
                "context": context,
            }
        ]
    }
    actual_digest = fp.digest
    calls = []

    def digest(value):
        if value is context:
            calls.append(value)
        return actual_digest(value)

    monkeypatch.setattr(fp, "digest", digest)
    observed = p.catalog(gate.program, state, fp.ReadBudget(), "fixture-revision")
    rows = [row for row in observed.targets.values() if row["target"]["kind"] == "verification"]
    assert len(rows) == 2 and len(calls) == 1
    assert len(fp.canonical(context)) > 100_000
    for row in rows:
        binding = row["binding"]
        assert "verification_context" not in binding["context"]
        assert binding["context"]["verification_context_sha256"] == actual_digest(context)
        assert len(fp.canonical(binding)) < 700


def test_legacy_verification_publication_and_frozen_review_stay_current(gate):
    name, value = legacy_verification_receipt(gate)
    original_input = gate.program.path(name).read_bytes()
    original_review = gate.program.path(value["review"]["path"]).read_bytes()
    payload = p.review_payload(value)
    assert gate.fixture.run("presentation-record", "--input", name) == 0
    key = p.target_key(value["operations"][0]["target"])
    current = inventory(gate)
    assert current["entries"][key]["status"] == "current"
    assert current["totals"]["current_translated"] == 1
    record(gate, identifier="compact", kind="verification", field="next_trigger")
    current = inventory(gate)
    assert current["entries"][key]["status"] == "current"
    assert current["totals"]["current_translated"] == 2
    assert gate.program.path(name).read_bytes() == original_input
    assert gate.program.path(value["review"]["path"]).read_bytes() == original_review
    assert p.review_payload(value) == payload


@pytest.mark.parametrize(
    "field,value",
    [
        ("attempt", 2),
        ("source_digest", "b" * 64),
        ("definition_digest", "c" * 64),
        ("objective_binding", {"criteria": ["changed"]}),
        ("input_digest", "d" * 64),
        ("provider_context_ref", {"path": "evidence/changed.json", "sha256": "e" * 64}),
        (
            "input_capture",
            {"environment": {"facts": {"python": "changed"}}, "method": {"argv": ["changed"]}},
        ),
    ],
)
def test_legacy_verification_context_identity_changes_are_stale(gate, field, value):
    name, authored = legacy_verification_receipt(gate)
    assert gate.fixture.run("presentation-record", "--input", name) == 0
    state = gate.program.load()
    state["verification"]["records"][-1]["context"][field] = value
    # A detached observed input tests presentation applicability; no invalid state is published.
    projected = p.projection(gate.program, state)
    entry = projected["entries"][p.target_key(authored["operations"][0]["target"])]
    assert entry["status"] == "stale" and "text" not in entry


@pytest.mark.parametrize("mutation", ["extra", "both", "not_record", "invalid_hash", "bool_hash"])
def test_verification_binding_compatibility_is_closed(gate, mutation):
    observed = verification_request(gate)
    name, value = receipt(gate, kind="verification", field="reason")
    compact = value["operations"][0]["binding"]
    altered = deepcopy(compact)
    context = altered["context"]
    if mutation == "extra":
        context["extra"] = "unrecognized"
    elif mutation == "both":
        context["verification_context"] = observed["context"]
    elif mutation == "not_record":
        del context["verification_context_sha256"]
        context["verification_context"] = []
    else:
        context["verification_context_sha256"] = (
            "not-a-digest" if mutation == "invalid_hash" else True
        )
    target = value["operations"][0]["target"]
    assert not p._binding_equivalent(target, altered, compact)
    assert not p._binding_equivalent(target, altered, altered)
    value["operations"][0]["binding"] = altered
    review_name = value["review"]["path"]
    review = fp.read_json(gate.program.path(review_name))
    review["payload_sha256"] = p.review_payload(value)
    gate.fixture.write(review_name, review)
    value["review"]["sha256"] = fp.sha(gate.program.path(review_name).read_bytes())
    gate.fixture.write(name, value)
    before = gate.program.path("backlog.json").read_bytes()
    assert gate.fixture.run("presentation-record", "--input", name) == 1
    assert gate.program.path("backlog.json").read_bytes() == before


def test_legacy_equivalence_never_changes_other_operation_fields_or_families(gate):
    _, value = legacy_verification_receipt(gate)
    old = value["operations"]
    current = p.bind_input(gate.program, gate.program.load(), value)["operations"]
    assert p._operations_equivalent(old, current)
    for field, replacement in (
        ("text", "다른 의미"),
        ("mode", "original"),
        ("action", "withdraw"),
        ("supersedes", "other"),
    ):
        changed = deepcopy(current)
        changed[0][field] = replacement
        assert not p._operations_equivalent(old, changed)
    assert not p._operations_equivalent(old, [])
    assert not p._binding_equivalent(
        {"kind": "objective"}, old[0]["binding"], current[0]["binding"]
    )
    altered = deepcopy(old[0]["binding"])
    altered["context"]["verification_context"]["bool_identity"] = True
    other = deepcopy(altered)
    other["context"]["verification_context"]["bool_identity"] = 1
    assert not p._binding_equivalent(old[0]["target"], altered, other)


def test_inventory_is_readonly_and_has_exact_original_targets(gate, capsys):
    before = gate.program.path("backlog.json").read_bytes()
    capsys.readouterr()
    assert gate.fixture.run("presentation-inventory") == 0
    result = json.loads(capsys.readouterr().out)
    assert any(row["target"]["kind"] == "objective_criterion" for row in result["targets"])
    assert gate.program.path("backlog.json").read_bytes() == before
    assert not gate.program.path(".feature-program.lock").exists()


def test_record_preview_actual_identity_and_current_snapshot(gate, capsys, monkeypatch):
    gate.ready()
    language(gate)
    original = gate.program.load()
    original_binding = fp.phases.planning_binding(original)
    name, value = receipt(gate)
    result = preview(gate.fixture, ["presentation-record", "--input", name], capsys, monkeypatch)
    assert result["status"] == "validated", result
    assert gate.fixture.run("presentation-record", "--input", name) == 0
    state = gate.program.load()
    assert normalized(state) == normalized(result["predicted_state"])
    assert state["objective"] == original["objective"]
    assert state["tasks"] == original["tasks"]
    assert fp.phases.planning_binding(state) == original_binding
    snapshot = fp.read_snapshot(gate.program.repo, gate.program.directory)
    entry = snapshot["presentation"]["entries"][p.target_key(value["operations"][0]["target"])]
    assert entry["text"] == "검증 목표" and entry["status"] == "current"
    assert snapshot["objective"]["title"] == original["objective"]["title"]
    assert entry["origin"]["revision"] == snapshot["revision"]


@pytest.mark.parametrize("mode", ["original", "translated", "alias"])
def test_modes_and_withdrawal_never_revive_previous_text(gate, mode):
    language(gate)
    fields = {"kind": "actor_alias", "field": "label"} if mode == "alias" else {}
    record(gate, mode=mode, **fields)
    current = inventory(gate)
    entry = next(iter(current["entries"].values()))
    assert entry["status"] == "current"
    assert ("text" in entry) == (mode != "original")
    record(gate, identifier="withdraw", action="withdraw", **fields)
    entry = next(iter(inventory(gate)["entries"].values()))
    assert entry["status"] == "withdrawn" and "text" not in entry


def test_stale_source_and_language_switch_keep_originals_and_history(gate):
    language(gate)
    record(gate, kind="task_action", field="next_action")
    state = gate.program.load()
    state["tasks"][0]["next_action"] = "새 단계"
    gate.program.save(state)
    entry = next(iter(inventory(gate)["entries"].values()))
    assert entry["status"] == "stale" and "text" not in entry
    assert (
        gate.fixture.run(
            "language", "--value", "en", "--owner", "director", "--reason", "Explicit change"
        )
        == 0
    )
    assert inventory(gate)["entries"] == {}
    assert len(gate.program.load()["presentation"]["records"]) == 1


@pytest.mark.parametrize("damage", ["missing", "changed", "oversized"])
def test_unavailable_frozen_review_falls_back_and_rejects_writes(gate, damage):
    language(gate)
    value = record(gate)
    path = gate.program.path(value["review"]["path"])
    if damage == "missing":
        path.unlink()
    else:
        path.write_bytes(b"{}" if damage == "changed" else b"x" * (p.DOCUMENT_LIMIT + 1))
    result = fp.read_snapshot(gate.program.repo, gate.program.directory)
    entry = next(iter(result["presentation"]["entries"].values()))
    assert entry["status"] == "unavailable" and "text" not in entry
    assert result["integrity"]["status"] == "stale"
    with pytest.raises(fp.ProgramError):
        gate.program.load()


@pytest.mark.parametrize(
    "failure",
    [
        "actor",
        "reviewer",
        "review_hash",
        "revision",
        "source",
        "unknown_field",
        "unknown_target",
        "wrong_locale",
        "duplicate",
        "oversized_text",
    ],
)
def test_invalid_record_is_atomic_and_preview_keeps_actual_failure(
    gate, failure, capsys, monkeypatch
):
    language(gate)
    name, value = receipt(gate)
    if failure == "actor":
        value["actor_id"] = "other"
    elif failure == "reviewer":
        review = fp.read_json(gate.program.path(value["review"]["path"]))
        review["reviewer_id"] = "writer"
        gate.program.path(value["review"]["path"]).write_bytes(fp.canonical(review))
        value["review"]["sha256"] = fp.sha(fp.canonical(review))
    elif failure == "review_hash":
        value["review"]["sha256"] = "a" * 64
    elif failure == "revision":
        value["expected_revision"] = "a" * 64
    elif failure == "source":
        value["operations"][0]["binding"]["source_value_sha256"] = "a" * 64
    elif failure == "unknown_field":
        value["extra"] = "not allowed"
    elif failure == "unknown_target":
        value["operations"][0]["target"]["field"] = "source_head"
    elif failure == "wrong_locale":
        value["locale"] = "en"
    elif failure == "duplicate":
        value["operations"].append(deepcopy(value["operations"][0]))
    else:
        value["operations"][0]["text"] = "가" * 513
    gate.program.path(name).write_bytes(fp.canonical(value))
    before = tree(gate.program.repo)
    result = preview(gate.fixture, ["presentation-record", "--input", name], capsys, monkeypatch)
    assert result["status"] == "failed", result
    assert gate.fixture.run("presentation-record", "--input", name) == 1
    error = capsys.readouterr().err
    assert result["reasons"][0] in error
    # The request is authored by the test helper; actual failed publication changed nothing else.
    after = tree(gate.program.repo)
    assert {
        key: val for key, val in after.items() if not key.endswith("author-request.json")
    } == before


def test_read_capabilities_and_race_rejection(gate, monkeypatch):
    language(gate)
    record(gate)
    before = tree(gate.program.repo)

    def denied(*args, **kwargs):
        raise AssertionError("Unexpected effect")

    with monkeypatch.context() as patch:
        patch.setattr(fp, "git", denied)
        patch.setattr(fp.Program, "lock", denied)
        patch.setattr(fp, "atomic_write", denied)
        fp.read_snapshot(gate.program.repo, gate.program.directory)
    assert tree(gate.program.repo) == before
    original = p._projection_from_checked

    def interleave(*args, **kwargs):
        result = original(*args, **kwargs)
        gate.program.path("backlog.json").write_bytes(
            gate.program.path("backlog.json").read_bytes() + b" "
        )
        return result

    monkeypatch.setattr(p, "_projection_from_checked", interleave)
    with pytest.raises(fp.ProgramError, match="Registry changed"):
        fp.read_snapshot(gate.program.repo, gate.program.directory)


def test_protected_spans_preserved_and_original_only_disposition(gate):
    language(gate)
    state = gate.program.load()
    state["tasks"][0]["next_action"] = "Run `do_it` and [read](docs/guide.md)"
    state["tasks"][1]["next_action"] = "Unbalanced `code"
    gate.program.save(state)
    with pytest.raises(fp.ProgramError, match="Protected"):
        receipt(gate, kind="task_action", field="next_action", text="다른 명령 `changed`")
    record(
        gate, kind="task_action", field="next_action", text="`do_it` 실행 후 [읽기](docs/guide.md)"
    )
    rows = inventory(gate)["targets"]
    assert any(
        row["disposition"] == "original_only" and row["target"]["entity_id"] == "S02"
        for row in rows
    )


def test_before_publish_rechecks_raw_revision_and_review(gate):
    language(gate)
    name, _ = receipt(gate)
    prepared = p.prepare_record(gate.program, gate.program.load(), name)
    gate.program.path("backlog.json").write_bytes(
        gate.program.path("backlog.json").read_bytes() + b" "
    )
    with pytest.raises(fp.ProgramError, match="Registry changed"):
        prepared.before_publish()


def issue_question(gate, identifier="question-one"):
    value = {
        "id": identifier,
        "version": 1,
        "actor_id": "director",
        "prompt": "Which scope?",
        "why": "A choice is needed",
        "requested_decision": "Choose scope",
        "options": ["Small", "Broad"],
        "affected_task_ids": ["S01"],
        "objective_id": "goal",
        "source_head": "a" * 40,
        "scope": ["src/S01.py"],
        "source_digest": gate.program.source_digest(["src/S01.py"]),
    }
    name = gate.fixture.write("question-input.json", value)
    assert gate.fixture.run("question-issue", "--input", name) == 0


def test_question_option_binding_answer_identity_and_new_question(gate):
    language(gate)
    issue_question(gate)
    original = deepcopy(gate.program.load()["questions"])
    value = record(gate, kind="question", field="option", text="작은 범위")
    assert gate.program.load()["questions"] == original
    options = [row for row in inventory(gate)["targets"] if row["target"]["field"] == "option"]
    assert len(options) == 2 and options[0]["key"] != options[1]["key"]
    assert (
        options[0]["binding"]["context"]["options_sha256"]
        == options[1]["binding"]["context"]["options_sha256"]
    )
    answer = fp.questions.record_answer(
        gate.program.repo, gate.program.directory, "question-one", 1, "작은 범위", "answer-one"
    )
    assert answer["answer"] == "작은 범위"
    state = gate.program.load()
    assert state["questions"]["items"][0]["issued_sha256"] == original["items"][0]["issued_sha256"]
    assert state["questions"]["items"][0]["answer"]["submission_id"] == "answer-one"
    entry = inventory(gate)["entries"][p.target_key(value["operations"][0]["target"])]
    assert entry["status"] == "current"
    issue_question(gate, "question-two")
    rows = inventory(gate)["targets"]
    assert all(
        row["status"] == "missing" for row in rows if row["target"]["entity_id"] == "question-two"
    )
    assert not any(
        row["target"]["field"] in {"answer", "submission_id", "source_head"} for row in rows
    )


def test_recorded_receipt_narrative_and_retained_attempt_identity(gate):
    language(gate)
    assert gate.fixture.claim() == 0
    gate.fixture.submit()
    value = record(gate, kind="receipt_narrative", field="summary", text="구현 검증 요약")
    row = next(
        row
        for row in inventory(gate)["targets"]
        if row["key"] == p.target_key(value["operations"][0]["target"])
    )
    assert (
        row["origin"]["path"].endswith("execution-1.json")
        and row["origin"]["pointer"] == "/summary"
    )
    assert row["binding"]["context"]["attempt"] == 1
    assert (
        gate.fixture.run(
            "release",
            "S01",
            "--owner",
            "worker",
            "--reason",
            "Retain old attempt",
            "--next-action",
            "Claim again",
        )
        == 0
    )
    assert gate.fixture.claim(number=2) == 0
    gate.fixture.submit()
    rows = inventory(gate)["targets"]
    old = next(row for row in rows if row["key"] == p.target_key(value["operations"][0]["target"]))
    assert old["historical"] is True and old["status"] == "current_translated"
    new = [
        row for row in rows if row["family"] == "F11" and row["binding"]["context"]["attempt"] == 2
    ]
    assert new and all(row["status"] == "missing" for row in new)


def test_complete_family_enumeration_and_protected_leaf_exclusions(gate):
    """Typed source-shape coverage, distinct from a claim that these synthetic records executed."""
    state = gate.program.load()
    state["communication_language"] = "ko"
    state["objective"]["next_action"] = "Review goal"
    state["categories"] = [{"id": "category", "title": "Category", "reason": "Grouping"}]
    state["tasks"][0]["notes"] = [
        {"reason": "Shared note", "next_action": "Read it", "actor": "director"}
    ]
    state["actors"][0]["history"] = [{"reason": "Reuse actual worker"}]
    state["handoffs"] = [
        {
            "id": "transfer",
            "outgoing": {"goal": "Continue", "constraints": ["Stay scoped"]},
            "acknowledgements": [{"summary": "Read context"}],
        }
    ]
    state["tasks"][0]["deliveries"] = [
        {
            "metadata": {
                "cleanup": {"reason": "Cleaned locally"},
                "pr_url": "https://example.invalid/pull/1",
            }
        }
    ]
    state["phases"] = {"history": [{"receipt": {"reason": "Reviewed inventory"}}]}
    state["coverage_annotations"] = [{"annotation": {"uncovered": [{"reason": "No link yet"}]}}]
    state["checkpoint"] = {"note": "Continue", "team_state": "Worker idle", "head": "a" * 40}
    state["questions"] = {
        "items": [
            {
                "id": "q",
                "version": 1,
                "issued_sha256": "a" * 64,
                "issued": {"prompt": "Choose", "options": ["One"]},
                "outcome": {"summary": "Applied"},
                "answer": {"answer": "Private answer"},
            }
        ]
    }
    state["events"] = [{"reason": "Recorded event"}]
    artifact = gate.fixture.write(
        "evidence/S01/attempt-1/summary.json",
        {
            "summary": "Execution summary",
            "checks": [{"description": "Check detail", "command": "private command"}],
        },
    )
    state["tasks"][0]["attempts"] = [
        {
            "number": 1,
            "owner": "worker",
            "status": "active",
            "rounds": [
                {
                    "receipt": {
                        "path": artifact,
                        "sha256": fp.sha(gate.program.path(artifact).read_bytes()),
                    }
                }
            ],
        }
    ]
    catalog = p.catalog(gate.program, state, fp.ReadBudget(), fp.digest(state))
    assert {row["family"] for row in catalog.targets.values()} == set(p.FAMILIES) - {
        "F21",
        "F22",
        "F23",
    }
    assert not any(
        row["original"]
        in {"Private answer", "private command", "https://example.invalid/pull/1", "a" * 40}
        for row in catalog.targets.values()
    )


def test_aggregate_budget_and_full_inventory_overflow_reject(gate, monkeypatch):
    language(gate)
    assert gate.fixture.claim() == 0
    gate.fixture.submit()
    state = gate.program.load()
    with pytest.raises(fp.ProgramError, match="Aggregate evidence"):
        p.projection(gate.program, state, fp.ReadBudget(1))
    monkeypatch.setattr(p, "OUTPUT_LIMIT", 100)
    with pytest.raises(fp.ProgramError, match="Output exceeds"):
        p.projection(gate.program, state, inventory=True)


def test_limits_unicode_input_and_batch_are_distinct(gate):
    language(gate)
    # The narrative code-point bound permits Unicode; encoded JSON must also fit 1 MiB.
    name, value = receipt(gate, kind="task_action", field="next_action", text="가" * 65536)
    assert gate.fixture.run("presentation-record", "--input", name) == 0
    value["operations"][0]["text"] += "가"
    with pytest.raises(fp.ProgramError, match="65536"):
        p._input(value)
    value["operations"] = [value["operations"][0]] * 257
    with pytest.raises(fp.ProgramError, match="256"):
        p._input(value)


def test_compact_missing_ledger_with_2000_tasks_has_no_row_cap(gate):
    state = gate.program.load()
    template = state["tasks"][0]
    state["tasks"] = []
    for index in range(2000):
        task = deepcopy(template)
        task["id"] = f"T{index}"
        state["tasks"].append(task)
    result = p.projection(gate.program, state)
    assert result["entries"] == {} and len(fp.canonical(result)) < 10000
    assert result["by_kind"]["task_title"]["missing"] == 2000
    # Full originals plus per-field fingerprints can exceed the existing byte bound;
    # it must reject, not return an arbitrary successful prefix of the 2000 tasks.
    with pytest.raises(fp.ProgramError, match="no truncated prefix"):
        p.projection(gate.program, state, inventory=True)


def test_catalog_detaches_and_shares_only_the_same_captured_source(gate: GateFixture) -> None:
    # Given two original tasks and two tasks from a separate supplemental descriptor.
    state = gate.program.load()
    state["source"]["metadata"] = {"labels": ["Original descriptor"]}
    supplemental = deepcopy(state["source"])
    supplemental["path"] = "discovery/supplemental.md"
    supplemental["task_ids"] = ["EXTRA1", "EXTRA2"]
    supplemental["metadata"]["labels"] = ["Supplemental descriptor"]
    state["source_intakes"] = [{"id": "extra", "source": supplemental}]
    for identifier in supplemental["task_ids"]:
        task = deepcopy(state["tasks"][0])
        task["id"] = identifier
        task["source_intake_id"] = "extra"
        state["tasks"].append(task)
    before = deepcopy(state)
    # When the complete catalog is captured once without modifying the supplied state.
    observed = p.catalog(gate.program, state, fp.ReadBudget(), "a" * 64)
    # Then original/supplemental values are exact, detached and shared only by source identity.
    titles = {
        row["target"]["entity_id"]: row
        for row in observed.targets.values()
        if row["family"] == "F06"
    }
    sources = {
        identifier: row["binding"]["context"]["source"] for identifier, row in titles.items()
    }
    assert state == before
    assert sources["S01"] == before["source"]
    assert sources["EXTRA1"] == before["source_intakes"][0]["source"]
    assert sources["S01"] is sources["S02"] and sources["S01"] is not state["source"]
    assert sources["EXTRA1"] is sources["EXTRA2"] and sources["EXTRA1"] is not supplemental
    assert sources["S01"] is not sources["EXTRA1"]
    for identifier, row in titles.items():
        task = next(task for task in state["tasks"] if task["id"] == identifier)
        assert row["binding"]["context"] == {
            "source": supplemental if identifier.startswith("EXTRA") else state["source"],
            "section_sha256": task["source"]["sha256"],
        }
        assert row["original"] == task["source"]["title"]
    sources["S01"]["metadata"]["labels"].append("Local catalog mutation")
    sources["EXTRA1"]["task_ids"].append("Local catalog task")
    assert state == before
    assert sources["EXTRA1"]["metadata"]["labels"] == ["Supplemental descriptor"]


def test_inventory_nested_source_metadata_is_independent_per_returned_row(
    gate: GateFixture,
) -> None:
    # Given a captured source with nested extension metadata and a complete task ID list.
    state = gate.program.load()
    state["source"]["metadata"] = {"labels": ["Original descriptor"]}
    before = deepcopy(state)
    # When one returned inventory row's nested source metadata is edited by its consumer.
    result = p.projection(gate.program, state, inventory=True)
    unchanged_inventory = deepcopy(result)
    rows = [row for row in result["targets"] if row["family"] == "F06"]
    first = rows[0]["binding"]["context"]["source"]
    first["metadata"]["labels"].append("Consumer edit")
    first["task_ids"].append("Consumer task")
    # Then other returned rows, the captured state and future reads retain their exact source.
    assert rows[1]["binding"]["context"]["source"] == before["source"]
    assert state == before
    assert p.projection(gate.program, state, inventory=True) == unchanged_inventory


@pytest.mark.parametrize("supplemental", [False, True])
def test_catalog_observes_changed_nested_source_on_the_next_call(
    gate: GateFixture, supplemental: bool
) -> None:
    # Given a prior catalog of either the original or a supplemental captured descriptor.
    state = gate.program.load()
    source = state["source"]
    if supplemental:
        source = deepcopy(source)
        state["source_intakes"] = [{"id": "extra", "source": source}]
        state["tasks"][0]["source_intake_id"] = "extra"
    source["metadata"] = {"labels": ["Before"]}
    previous = p.catalog(gate.program, state, fp.ReadBudget(), "a" * 64)
    previous_rows = deepcopy(previous.targets)
    source["metadata"]["labels"] = ["After"]
    source["task_ids"].append("Fresh descriptor ID")
    # When a new catalog observes the changed descriptor, even under the same revision string.
    current = p.catalog(gate.program, state, fp.ReadBudget(), "a" * 64)
    # Then all keys/originals remain exact and only the affected source binding changes.
    assert previous.targets == previous_rows
    assert current.targets.keys() == previous.targets.keys()
    for key, row in current.targets.items():
        if row["family"] in {"F06", "F07"} and (
            not supplemental or row["target"]["entity_id"] == "S01"
        ):
            old = previous.targets[key]
            context = row["binding"]["context"]
            assert context["source"] == source
            assert row == {
                **old,
                "binding": {
                    **old["binding"],
                    "context": {**old["binding"]["context"], "source": source},
                },
            }
        else:
            assert row == previous.targets[key]


def test_authoring_bindings_detach_nested_sources_between_task_operations(
    gate: GateFixture,
) -> None:
    # Given one authoring request containing two titles from the same source descriptor.
    state = gate.program.load()
    state["communication_language"] = "ko"
    state["source"]["metadata"] = {"labels": ["Original descriptor"]}
    before = deepcopy(state)
    draft: fp.Record = {
        "schema_version": 1,
        "operation": "presentation-record",
        "id": "two-titles",
        "program_id": state["program_id"],
        "actor_id": "director",
        "locale": "ko",
        "reason": "Synthetic authoring isolation check",
        "authors": ["writer"],
        "expected_revision": None,
        "review": None,
        "operations": [
            {
                "action": "set",
                "target": {"kind": "task_title", "entity_id": identifier, "field": "title"},
                "mode": "original",
            }
            for identifier in ("S01", "S02")
        ],
    }
    # When the consumer edits one returned operation's nested descriptor metadata.
    bound = p.bind_input(gate.program, state, draft)
    unchanged_binding = deepcopy(bound)
    first = bound["operations"][0]["binding"]["context"]["source"]
    first["metadata"]["labels"].append("Consumer edit")
    first["task_ids"].append("Consumer task")
    # Then the other operation, supplied state and later bound request stay independent.
    assert bound["operations"][1]["binding"]["context"]["source"] == before["source"]
    assert state == before
    assert p.bind_input(gate.program, state, draft) == unchanged_binding


@pytest.mark.parametrize("field", ["kind", "entity_id", "field", "mode"])
def test_malformed_typed_fields_reject_without_typeerror(gate, field, capsys):
    language(gate)
    name, value = receipt(gate)
    item = value["operations"][0]
    if field == "mode":
        item[field] = []
    else:
        item["target"][field] = []
    gate.program.path(name).write_bytes(fp.canonical(value))
    before = gate.program.path("backlog.json").read_bytes()
    assert gate.fixture.run("presentation-record", "--input", name) == 1
    assert "Traceback" not in capsys.readouterr().err
    assert gate.program.path("backlog.json").read_bytes() == before


def test_non_task_event_identity_and_legacy_actor_alias_are_explicit(gate):
    result = inventory(gate)
    program_rows = [
        row
        for row in result["targets"]
        if row["family"] == "F10" and row["binding"]["context"].get("entity_type") == "program"
    ]
    assert program_rows and all(row["target"]["entity_id"] == "discovery" for row in program_rows)
    assert all(row["target"]["entity_id"] != "None" for row in result["targets"])
    assert any(
        row["family"] == "F14" and row["target"]["entity_id"] == "leader"
        for row in result["targets"]
    )


def test_complete_reviewed_catalog_stays_complete_after_publication(gate):
    assert (
        gate.fixture.run(
            "language", "--value", "en", "--owner", "director", "--reason", "Use English originals"
        )
        == 0
    )
    state = gate.program.load()
    rows = inventory(gate)["targets"]
    value = {
        "schema_version": 1,
        "operation": "presentation-record",
        "id": "complete-originals",
        "program_id": state["program_id"],
        "actor_id": "director",
        "locale": "en",
        "reason": "Synthetic complete English-original review",
        "authors": ["writer"],
        "expected_revision": None,
        "review": None,
        "operations": [
            {"action": "set", "target": row["target"], "mode": "original"}
            for row in rows
            if row["disposition"] == "eligible"
        ],
    }
    binding = p.bind_input(gate.program, state, value)
    value.update({key: binding[key] for key in ("operations", "expected_revision")})
    review = {
        "schema_version": 1,
        "operation": "presentation-review",
        "program_id": state["program_id"],
        "locale": "en",
        "payload_sha256": binding["review_payload_sha256"],
        "reviewer_id": "independent",
        "reviewer_role": "independent",
        "verdict": "accepted",
        "unresolved_findings": [],
        "summary": "Synthetic complete-catalog review",
    }
    review_name = gate.fixture.write(p.PREFIX + "complete-review.json", review)
    value["review"] = {
        "path": review_name,
        "sha256": fp.sha(gate.program.path(review_name).read_bytes()),
    }
    name = gate.fixture.write(p.PREFIX + "complete-input.json", value)
    assert gate.fixture.run("presentation-record", "--input", name) == 0
    after = inventory(gate)
    assert after["coverage_status"] == "complete"
    assert after["totals"]["missing"] == 0
    assert {row["key"] for row in after["targets"]} == {row["key"] for row in rows}
    assert after["technical_records"]["presentation_audit_events"] == 1
    assert after["technical_records"]["presentation_transactions"] == 1
    assert after["technical_records"]["disposition"] == "original_only"


def test_checked_projection_reuses_first_validation_and_detaches_outputs(gate, monkeypatch):
    # Given one strictly validated immutable capture and its independently expected view.
    language(gate)
    record(gate)
    state = gate.program.load()
    expected = p.projection(gate.program, state)
    checked = p.validate(state)

    def unexpected_validation(*args, **kwargs):
        raise AssertionError("Repeated structural validation of the same capture")

    # When the private projection consumes that first checked result.
    with monkeypatch.context() as patch:
        patch.setattr(p, "_input_keys", unexpected_validation)
        actual = p._projection_from_checked(gate.program, state, checked)
    # Then the complete view is preserved and nested output cannot alter later projections.
    assert actual == expected
    next(iter(actual["entries"].values()))["binding"]["context"]["consumer"] = True
    assert p._projection_from_checked(gate.program, state, checked) == expected


def test_checked_projection_rejects_a_different_captured_state(gate):
    # Given a checked ledger belonging to one exact capture.
    state = gate.program.load()
    checked = p.validate(state)
    # When a different state object is supplied with the old checked result.
    with pytest.raises(fp.ProgramError, match="another state"):
        p._projection_from_checked(gate.program, deepcopy(state), checked)


def test_public_projection_still_rejects_changed_ledger(gate):
    # Given a previously valid captured ledger.
    language(gate)
    record(gate)
    state = gate.program.load()
    p.validate(state)
    state["presentation"]["records"][0]["input"]["operations"][0]["supersedes"] = "unknown"
    # When an independent public call observes the changed state.
    with pytest.raises(fp.ProgramError, match="supersession"):
        p.projection(gate.program, state)


def _state_with_repeated_receipt_refs(gate, *, conflicting=False):
    """Retain real frozen transaction/review bytes with two source-bound operations."""
    language(gate)
    name, value = receipt(gate)
    source = gate.fixture.write("source-proof.json", {"summary": "Synthetic original proof"})
    ref = {"path": source, "sha256": fp.sha(gate.program.path(source).read_bytes())}
    first = value["operations"][0]
    first["binding"]["context"]["receipt"] = deepcopy(ref)
    second = deepcopy(first)
    second["target"]["field"] = "goal"
    if conflicting:
        second["binding"]["context"]["receipt"]["sha256"] = "0" * 64
    value["operations"].append(second)
    review_name = value["review"]["path"]
    review = json.loads(gate.program.path(review_name).read_text(encoding="utf-8"))
    review["payload_sha256"] = p.review_payload(value)
    gate.program.path(review_name).write_bytes(fp.canonical(review))
    value["review"]["sha256"] = fp.sha(gate.program.path(review_name).read_bytes())
    gate.program.path(name).write_bytes(fp.canonical(value))
    state = gate.program.load()
    state["presentation"] = {
        "schema_version": 1,
        "records": [
            {
                "input": value,
                "receipt": {"path": name, "sha256": fp.sha(gate.program.path(name).read_bytes())},
                "recorded_at": "2026-01-01T00:00:00+00:00",
                "recorded_by": value["actor_id"],
            }
        ],
    }
    return state, source


def test_exact_duplicate_source_refs_are_verified_once_per_transaction(gate, monkeypatch):
    # Given two operations relying on the same actual frozen source receipt.
    state, source = _state_with_repeated_receipt_refs(gate)
    original = fp.Program.verify_refs
    observed = []

    def verify(program, refs, budget=None):
        observed.extend(deepcopy(refs))
        return original(program, refs, budget)

    # When a complete projection checks the frozen transaction.
    with monkeypatch.context() as patch:
        patch.setattr(fp.Program, "verify_refs", verify)
        result = p.projection(gate.program, state)
    # Then one exact dependency check retains both operations without promoting stale bindings.
    assert [ref["path"] for ref in observed] == [source]
    assert len(result["entries"]) == 2
    assert all(entry["status"] == "stale" for entry in result["entries"].values())


def test_conflicting_source_hash_is_not_hidden_by_reference_dedup(gate):
    # Given two references with the same path and conflicting expected bytes.
    state, _ = _state_with_repeated_receipt_refs(gate, conflicting=True)
    # When normal projection verifies each distinct path/hash dependency.
    result = p.projection(gate.program, state)
    # Then every dependent operation remains unavailable.
    assert all(entry["status"] == "unavailable" for entry in result["entries"].values())


def test_later_projection_rechecks_source_bytes(gate):
    # Given an earlier projection with available immutable dependency bytes.
    state, source = _state_with_repeated_receipt_refs(gate)
    assert all(
        entry["status"] == "stale"
        for entry in p.projection(gate.program, state)["entries"].values()
    )
    gate.program.path(source).write_bytes(b"changed")
    # When another independent capture observes the same expected source reference.
    result = p.projection(gate.program, state)
    # Then no previous successful assessment can hide the changed bytes.
    assert all(entry["status"] == "unavailable" for entry in result["entries"].values())


def test_existing_section_hash_does_not_eagerly_hash_fallback(gate, monkeypatch):
    # Given valid task sections carrying their exact supplied source SHA.
    state = gate.program.load()
    sections = [task["source"] for task in state["tasks"]]
    original = fp.digest

    def digest(value):
        if any(value is section for section in sections):
            raise AssertionError("Unneeded eager section digest")
        return original(value)

    # When catalog binds authored source fields.
    with monkeypatch.context() as patch:
        patch.setattr(fp, "digest", digest)
        observed = p.catalog(gate.program, state, fp.ReadBudget(), fp.phases.revision(state))
    # Then every task source title/body retains its independently stored section fingerprint.
    for target in observed.targets.values():
        if target["family"] in {"F06", "F07"}:
            task = next(
                task for task in state["tasks"] if task["id"] == target["target"]["entity_id"]
            )
            assert target["binding"]["context"]["section_sha256"] == task["source"]["sha256"]


def test_inventory_cli_reuses_its_first_strict_ledger_validation(gate, capsys, monkeypatch):
    # Given one recorded presentation transaction and the real CLI inventory reader.
    language(gate)
    record(gate)
    capsys.readouterr()
    original = p._input_keys
    checked_ids = []

    def check(value):
        checked_ids.append(value["id"])
        return original(value)

    # When the CLI obtains the first validated host result and projects its inventory.
    with monkeypatch.context() as patch:
        patch.setattr(p, "_input_keys", check)
        status = gate.fixture.run("presentation-inventory")
    result = json.loads(capsys.readouterr().out)
    # Then one strict ledger pass retains the complete current inventory.
    assert status == 0
    assert checked_ids == ["translation-1"]
    assert result["totals"]["current_translated"] == 1


def test_envelope_inventory_keeps_body_revision_and_physical_registry(gate, capsys):
    # Given the same valid logical state in an explicitly enabled registry envelope.
    from tools import feature_program_storage as storage

    language(gate)
    path = gate.program.path("backlog.json")
    body = path.read_bytes()
    raw = storage.encode_registry(body, "1" * 64)
    path.write_bytes(raw)
    capsys.readouterr()
    # When the ordinary standalone inventory reads the known registry explicitly.
    assert gate.fixture.run("presentation-inventory") == 0
    result = json.loads(capsys.readouterr().out)
    # Then its semantic revision is BODY identity and the physical envelope is untouched.
    assert result["revision"] == fp.sha(body)
    assert result["revision"] != fp.sha(raw)
    assert path.read_bytes() == raw


def test_envelope_binding_reports_physical_observation_separately(gate, capsys):
    # Given an authored input bound to the unchanged logical registry state.
    from tools import feature_program_storage as storage

    language(gate)
    name, value = receipt(gate)
    path = gate.program.path("backlog.json")
    body = path.read_bytes()
    raw = storage.encode_registry(body, "2" * 64)
    path.write_bytes(raw)
    capsys.readouterr()
    # When binding runs through the real read-only command.
    status = gate.fixture.run("presentation-binding", "--input", name)
    result = json.loads(capsys.readouterr().out)
    # Then raw observation identity remains distinct from logical transaction revision.
    assert status == 0
    assert result["observed_registry_sha256"] == fp.sha(raw)
    assert result["expected_revision"] == value["expected_revision"]
    assert result["operations"] == value["operations"]
    assert path.read_bytes() == raw


def test_presentation_publish_rejects_selector_only_registry_change(gate):
    # Given a prepared transaction against one physical selection and exact BODY.
    from tools import feature_program_storage as storage

    language(gate)
    name, _ = receipt(gate)
    path = gate.program.path("backlog.json")
    body = path.read_bytes()
    path.write_bytes(storage.encode_registry(body, "1" * 64))
    prepared = p.prepare_record(gate.program, gate.program.load(), name)
    path.write_bytes(storage.encode_registry(body, "2" * 64))
    # When publication reaches its independent full-storage guard.
    with pytest.raises(fp.ProgramError, match="Registry changed"):
        prepared.before_publish()
    # Then no transaction is published over a changed physical selector.
    assert not p._records(gate.program.load())


def test_inventory_closing_guard_rejects_selector_only_race(gate, monkeypatch, capsys):
    # Given a valid envelope and a concurrent physical selection change after projection.
    from tools import feature_program_storage as storage

    language(gate)
    path = gate.program.path("backlog.json")
    body = path.read_bytes()
    path.write_bytes(storage.encode_registry(body, "1" * 64))
    original = p._projection_from_checked

    def interleave(*args, **kwargs):
        result = original(*args, **kwargs)
        path.write_bytes(storage.encode_registry(body, "2" * 64))
        return result

    monkeypatch.setattr(p, "_projection_from_checked", interleave)
    capsys.readouterr()
    # When the real inventory command runs its closing storage observation.
    status = gate.fixture.run("presentation-inventory")
    # Then equal BODY cannot conceal the changed selected model token.
    assert status == 1
    assert "registry changed" in capsys.readouterr().err.lower()


def test_presentation_input_receipt_does_not_implicitly_decode_envelope(gate):
    # Given an envelope-shaped artifact at a generic receipt path, not the live registry.
    from tools import feature_program_storage as storage

    language(gate)
    name, _ = receipt(gate)
    path = gate.program.path(name)
    path.write_bytes(storage.encode_registry(path.read_bytes(), "1" * 64))
    before = gate.program.path("backlog.json").read_bytes()
    # When normal preparation parses that generic receipt as strict raw JSON.
    with pytest.raises(fp.ProgramError, match="Invalid JSON"):
        p.prepare_record(gate.program, gate.program.load(), name)
    # Then the canonical registry remains untouched.
    assert gate.program.path("backlog.json").read_bytes() == before
