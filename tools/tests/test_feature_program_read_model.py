"""Fresh closing images, source membership and executing-producer identity."""

from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path

import pytest

from tools.tests.test_feature_program import fp
import feature_program_read_model as models


@dataclass(frozen=True)
class Observation:
    body_bytes: bytes
    body_sha256: str


@pytest.fixture
def image(tmp_path):
    repo = tmp_path / "canonical"
    repo.mkdir()
    directory = repo / ".work" / "programs" / "fixture"
    directory.mkdir(parents=True)
    program = fp.Program(repo, directory)
    body = fp.canonical({"program_id": "fixture"})
    observation = Observation(body, fp.sha(body))
    collector = models.Collector(
        repo,
        directory,
        program_id="fixture",
        body_sha256=observation.body_sha256,
        producer=models.producer_identity(repo),
        query=deepcopy(models.QUERY),
    )
    return program, observation, collector


def check(image, model):
    program, observation, _ = image
    return models.validate(
        model, program, observation=observation, observer=fp._ClosingObserver(program)
    )


def test_closing_image_detects_changed_evidence_and_returns_detached_snapshot(image):
    # Given an actual admitted, bounded frozen artifact.
    program, _, collector = image
    (program.repo / "proof.txt").write_bytes(b"initial")
    with models.collect(collector):
        captured = fp.ReadBudget(100).capture(program.repo, "proof.txt", 100)
    model = collector.finish({"proof": captured.sha256, "nested": {"value": "original"}})
    # When the same model is assessed before and after a real evidence mutation.
    first = check(image, model)
    first["nested"]["value"] = "caller overlay"
    unchanged = check(image, model)
    (program.repo / "proof.txt").write_bytes(b"changed")
    changed = check(image, model)
    # Then overlays are isolated and changed bytes cannot serve the stored DTO.
    assert unchanged["nested"]["value"] == "original"
    assert changed is None


@pytest.mark.parametrize("operation", ["exists", "is_file", "is_dir", "kind"])
def test_missing_path_becoming_present_invalidates_negative_branch(image, operation):
    # Given a negative branch with its actual missing kind.
    program, _, collector = image
    with models.collect(collector):
        path = fp.safe_path(program.repo, "later.txt")
        fp.observe_path_probe(path, operation, "missing" if operation == "kind" else "false")
        fp.observe_path_probe(path, "kind", "missing")
    model = collector.finish({"status": "missing"})
    # When a regular file appears.
    path.write_bytes(b"now present")
    # Then the negative observation cannot authorize the old snapshot.
    assert check(image, model) is None


@pytest.mark.parametrize("change", ["add", "remove", "type"])
def test_fresh_source_scope_replays_membership_and_type(image, change):
    # Given one recursive scope whose full membership was measured.
    program, _, collector = image
    directory = program.repo / "src"
    directory.mkdir()
    item = directory / "one.py"
    item.write_bytes(b"one")
    with models.collect(collector):
        program.source_snapshot(["src"])
    model = collector.finish({"files": 1})
    # When discovery changes after the initial capture.
    match change:
        case "add":
            (directory / "two.py").write_bytes(b"two")
        case "remove":
            item.unlink()
        case "type":
            item.unlink()
            item.mkdir()
    # Then closing membership/type, rather than the old positive file census, rejects it.
    assert check(image, model) is None


def test_zero_byte_capture_at_zero_limit_remains_valid(image):
    # Given a legitimate empty raw artifact and its exact zero remaining limit.
    program, _, collector = image
    (program.repo / "empty.bin").write_bytes(b"")
    with models.collect(collector):
        fp.ReadBudget(0).capture(program.repo, "empty.bin", 0)
    model = collector.finish({"empty": True})
    # When the closing image enforces that same limit.
    result = check(image, model)
    # Then raw identity survives without relaxing consumer-owned nonempty evidence rules.
    assert result == {"empty": True}


def test_alias_admissions_and_each_consumer_limit_are_retained(image):
    # Given independently admitted aliases with two bounded consumers.
    program, _, collector = image
    (program.repo / "file.txt").write_bytes(b"abc")
    with models.collect(collector):
        fp.ReadBudget(10).capture(program.repo, "file.txt", 3)
        fp.ReadBudget(10).capture(program.repo, "./file.txt", 9)
    model = collector.finish({"status": "current"})
    # When the complete frozen dependency graph is inspected and closed.
    deps = model["dependencies"]
    result = check(image, model)
    # Then logical aliases and consumer bounds were not collapsed into a weaker fact.
    assert {row["name"] for row in deps["admissions"]} == {"file.txt", "./file.txt"}
    assert {row["limit"] for row in deps["files"]} >= {3, 9}
    assert result == {"status": "current"}


def test_future_view_is_validated_against_real_rendered_bytes(image):
    # Given a proposed projection that used future rendered bytes.
    program, _, collector = image
    collector.expect_view(program.directory, "BOARD.md", b"new view")
    model = collector.finish({"warnings": []})
    (program.directory / "BOARD.md").write_bytes(b"old view")
    # When the real rendered view is stale.
    result = check(image, model)
    # Then a proposed expected view cannot hide a failed actual render.
    assert result is None


def test_future_view_matches_after_real_render(image):
    # Given proposed bytes and a completed actual render.
    program, _, collector = image
    collector.expect_view(program.directory, "BOARD.md", b"new view")
    model = collector.finish({"warnings": []})
    (program.directory / "BOARD.md").write_bytes(b"new view")
    # When the real view closes.
    result = check(image, model)
    # Then the exact DTO is available.
    assert result == {"warnings": []}


@pytest.mark.parametrize(
    "change", ["body", "root", "query", "producer", "runtime", "query_bool", "producer_bool"]
)
def test_binding_changes_cannot_authorize_old_snapshot(image, change):
    # Given an otherwise current model.
    program, observation, collector = image
    model = collector.finish({"status": "old"})
    # When one semantic identity changes.
    match change:
        case "body":
            body = fp.canonical({"program_id": "fixture", "new": True})
            observation = Observation(body, fp.sha(body))
        case "root":
            model["binding"]["program_dir"] += "-other"
        case "query":
            model["query"]["kind"] = "selected_snapshot"
        case "producer":
            model["producer"]["root"] = str(program.repo)
        case "runtime":
            model["producer"]["runtime"]["version"] = [0]
        case "query_bool":
            model["query"]["version"] = True
        case "producer_bool":
            model["producer"]["runtime"]["parser_version"] = True
    result = models.validate(
        model, program, observation=observation, observer=fp._ClosingObserver(program)
    )
    # Then no different binding receives the stored result.
    assert result is None


def test_producer_uses_executing_checkout_not_canonical_data_repository(image):
    # Given code executing from a checkout different from Program.repo.
    program, _, collector = image
    fake_tools = program.repo / "tools"
    fake_tools.mkdir()
    (fake_tools / "feature_program.py").write_bytes(b"unrelated canonical checkout")
    # When identity is computed against that canonical data location.
    identity = models.producer_identity(program.repo)
    # Then only the actual executing module root and source bytes authorize reuse.
    expected = Path(models.__file__).resolve().parents[1]
    assert Path(identity["root"]) == expected
    assert identity == collector.producer
    assert identity["files"]["tools/feature_program_read_model.py"]["sha256"] == fp.sha(
        Path(models.__file__).read_bytes()
    )


def test_new_producer_membership_invalidates_old_image(image, monkeypatch, tmp_path):
    # Given an old model and a changed executing producer source image.
    _, _, collector = image
    model = collector.finish({"status": "old"})
    producer = tmp_path / "producer" / "tools"
    producer.mkdir(parents=True)
    module_path = producer / "feature_program_read_model.py"
    module_path.write_bytes(b"producer copy")
    monkeypatch.setattr(models, "__file__", str(module_path))
    # When the current executing producer identity is observed from its own checkout.
    result = check(image, model)
    # Then the canonical data repository cannot conceal a producer change.
    assert result is None


def test_ineligible_marker_is_body_bound_and_has_no_servable_dto(image):
    # Given an explicitly unrepresentable adverse branch.
    _, observation, collector = image
    collector.ineligible("permission_unknown")
    # When the proposed result is frozen.
    model = collector.finish({"would_be_unsafe": True})
    # Then ordinary publication can retain an honest miss without any cached success.
    assert model["binding"]["body_sha256"] == observation.body_sha256
    assert model["eligible"] is False
    assert "snapshot" not in model
    assert check(image, model) is None


def test_conflicting_initial_images_are_ineligible(image):
    # Given two observations from the same initial computation that disagree.
    program, _, collector = image
    collector.observe_file(
        program.repo, "file", kind="file", sha256="a" * 64, size=1, limit=1, budget_tag=None
    )
    collector.observe_file(
        program.repo, "file", kind="file", sha256="b" * 64, size=1, limit=1, budget_tag=None
    )
    # When the model is finalized.
    model = collector.finish({"unsafe": True})
    # Then no last-write-wins identity becomes a successful model.
    assert model["eligible"] is False
    assert "snapshot" not in model


def test_collector_context_restores_on_exception(image):
    # Given an active observation.
    _, _, collector = image
    # When an inner observation fails.
    with models.collect(collector):
        with pytest.raises(RuntimeError), models.collect(None):
            raise RuntimeError("synthetic")
        restored = models.current()
    # Then both nested and outer activation lifetimes are restored.
    assert restored is collector
    assert models.current() is None


@pytest.mark.parametrize(
    "change", ["unknown_field", "invalid_limit", "path_escape", "bool_version", "nonfinite"]
)
def test_closed_model_rejects_malformed_dependencies_before_reading(image, change):
    # Given a valid captured file dependency.
    program, _, collector = image
    (program.repo / "proof").write_bytes(b"proof")
    with models.collect(collector):
        fp.ReadBudget(100).capture(program.repo, "proof", 100)
    model = collector.finish({"status": "old"})
    # When a stored derived object violates the closed schema.
    match change:
        case "unknown_field":
            model["skip_validation"] = True
        case "invalid_limit":
            model["dependencies"]["files"][0]["limit"] = -1
        case "path_escape":
            model["dependencies"]["files"][0]["name"] = "../secret"
        case "bool_version":
            model["schema_version"] = True
        case "nonfinite":
            model["snapshot"]["bad"] = float("nan")
    # Then the malformed derived object is a miss, never a servable DTO.
    assert check(image, model) is None


def test_ordinary_observer_integrity_error_is_not_downgraded(image):
    # Given an actually captured file whose closing admission now fails.
    program, _, collector = image
    (program.repo / "proof").write_bytes(b"proof")
    with models.collect(collector):
        fp.ReadBudget(100).capture(program.repo, "proof", 100)
    model = collector.finish({"status": "old"})

    class RejectedObserver(fp._ClosingObserver):
        def admit(self, root, name):
            raise fp.ProgramError("ordinary integrity failure")

    # When the normal observer rejects its authority/integrity boundary.
    with pytest.raises(fp.ProgramError, match="ordinary integrity"):
        models.validate(model, program, observation=image[1], observer=RejectedObserver(program))


def test_entry_overflow_never_truncates_an_eligible_graph(image, monkeypatch):
    # Given a smaller derived-model bound independent of normal source validity.
    program, _, collector = image
    monkeypatch.setattr(models, "ENTRY_LIMIT", 1)
    collector.observe_admission(program.repo, "one")
    collector.observe_admission(program.repo, "two")
    # When the full result is finalized.
    model = collector.finish({"unsafe": True})
    # Then overflow is explicitly nonservable rather than an eligible prefix.
    assert model["eligible"] is False
    assert model["reasons"] == ["dependency_entry_bound"]
    assert "snapshot" not in model


def test_model_byte_overflow_retains_small_body_bound_miss(image, monkeypatch):
    # Given a derived DTO exceeding its separate serialization budget.
    _, observation, collector = image
    monkeypatch.setattr(models, "MODEL_LIMIT", 100)
    # When it is finalized.
    model = collector.finish({"large": "x" * 200})
    # Then no eligible truncated output is stored.
    assert model["eligible"] is False
    assert model["binding"]["body_sha256"] == observation.body_sha256
    assert "snapshot" not in model


def test_query_and_snapshot_are_finite_json(image):
    # Given a complete finite model.
    _, _, collector = image
    # When serialized through the documented store representation.
    model = json.loads(json.dumps(collector.finish({"status": "current"}), allow_nan=False))
    # Then a detached strict JSON roundtrip retains its actual usable payload.
    assert check(image, model) == {"status": "current"}


def test_producer_membership_overflow_is_nonservable_without_aborting_publication(image):
    # Given an executing-source image that exceeds the derived-model membership bound.
    program, observation, original = image
    producer = {**original.producer, "complete": False, "files": {}}
    collector = models.Collector(
        program.repo,
        program.directory,
        program_id="fixture",
        body_sha256=observation.body_sha256,
        producer=producer,
        query=models.QUERY,
    )
    # When normal publication stages its derived result.
    model = collector.finish({"unsafe": True})
    # Then it retains a bounded BODY-bound miss, never an eligible truncated graph.
    assert model["eligible"] is False
    assert model["reasons"] == ["producer_membership_bound"]
    assert "snapshot" not in model


def test_scope_membership_overflow_does_not_store_a_truncated_success(image):
    # Given a normal scope result too large for the supplied derived entry limit.
    program, _, collector = image
    # When the complete observation exceeds that limit.
    collector.observe_scope(
        program.repo,
        ["src"],
        {"src/a": "missing", "src/b": "missing"},
        [],
        byte_budget=100,
        entry_limit=1,
    )
    model = collector.finish({"unsafe": True})
    # Then ordinary state can remain available through strict fallback.
    assert model["eligible"] is False
    assert model["reasons"] == ["scope_entry_bound"]
    assert model["dependencies"]["scopes"] == []


def test_large_future_view_is_a_nonservable_marker_instead_of_publication_failure(image):
    # Given a valid normal view too large for this derived reader's file limit.
    program, _, collector = image
    # When the proposed model observes those future bytes.
    collector.expect_view(program.directory, "BOARD.md", b"x" * (8 * 1024 * 1024 + 1))
    model = collector.finish({"unsafe": True})
    # Then enabled mutation can preserve availability with a strict-reader fallback.
    assert model["eligible"] is False
    assert model["reasons"] == ["derived_view_byte_bound"]
    assert "snapshot" not in model


def test_file_image_ledger_remains_bounded_after_overflow(image, monkeypatch):
    # Given a small derived dependency bound.
    program, _, collector = image
    monkeypatch.setattr(models, "ENTRY_LIMIT", 1)
    collector.observe_file(
        program.repo, "one", kind="file", sha256="a" * 64, size=0, limit=0, budget_tag=None
    )
    # When further distinct physical images arrive after overflow.
    collector.observe_file(
        program.repo, "two", kind="file", sha256="a" * 64, size=0, limit=0, budget_tag=None
    )
    # Then neither a hidden unbounded ledger nor an eligible prefix survives.
    assert len(collector._images) == 1
    assert collector.finish({"unsafe": True})["eligible"] is False


class CountedClosingObserver(fp._ClosingObserver):
    """Record actual fresh observer calls without substituting filesystem behavior."""

    def __init__(self, program):
        super().__init__(program)
        self.file_calls = []
        self.probe_calls = []
        self.admission_calls = []

    def admit(self, root, name):
        self.admission_calls.append((root, name))
        return super().admit(root, name)

    def file(self, root, name, *, limit, budget_tag):
        self.file_calls.append((root, name, limit, budget_tag))
        return super().file(root, name, limit=limit, budget_tag=budget_tag)

    def probe(self, root, name, *, operation):
        self.probe_calls.append((root, name, operation))
        return super().probe(root, name, operation=operation)


def repeated_file_model(image):
    program, _, collector = image
    (program.repo / "proof").write_bytes(b"abc")
    with models.collect(collector):
        reader = fp.ReadBudget(100)
        for limit in (100, 3, 10):
            reader.capture(program.repo, "proof", limit)
        fp.observe_path_probe(program.repo / "proof", "kind", "file")
    return collector.finish({"status": "current"})


def test_repeated_consumers_close_once_under_strongest_limit(image):
    # Given multiple real consumer limits over exactly the same captured image.
    program, observation, _ = image
    model = repeated_file_model(image)
    observer = CountedClosingObserver(program)
    # When one fresh closing observation validates the whole stored graph.
    result = models.validate(model, program, observation=observation, observer=observer)
    # Then the strongest limit proves every row, without repeated file/kind observations.
    assert result == {"status": "current"}
    assert [call[2] for call in observer.file_calls] == [3]
    assert observer.probe_calls.count((program.repo, "proof", "kind")) == 1


@pytest.mark.parametrize("change", ["limit", "hash", "size", "kind", "duplicate", "order", "field"])
def test_grouping_validates_every_original_consumer_before_replay(image, change):
    # Given repeated consumers whose strongest valid row could otherwise hide a bad row.
    program, observation, _ = image
    model = repeated_file_model(image)
    rows = model["dependencies"]["files"]
    weak = next(row for row in rows if row["limit"] == 100)
    match change:
        case "limit":
            weak["limit"] = 2
        case "hash":
            weak["sha256"] = "0" * 64
        case "size":
            weak["size"] = 2
        case "kind":
            weak.update(kind="missing", sha256=None, size=None)
        case "duplicate":
            rows.insert(0, deepcopy(rows[0]))
        case "order":
            rows.reverse()
        case "field":
            weak["skip"] = True
    observer = CountedClosingObserver(program)
    # When the malformed whole graph is assessed.
    result = models.validate(model, program, observation=observation, observer=observer)
    # Then no consumer is discarded before its constraints are checked.
    assert result is None
    assert observer.file_calls == []


def test_logical_aliases_remain_distinct_fresh_admissions_and_reads(image):
    # Given two separately admitted logical aliases for one physical file.
    program, observation, collector = image
    (program.repo / "proof").write_bytes(b"abc")
    with models.collect(collector):
        fp.ReadBudget(10).capture(program.repo, "proof", 3)
        alias = fp.ReadBudget(10).capture(program.repo, "./proof", 5)
        collector.observe_file(
            program.repo,
            "./proof",
            kind="file",
            sha256=alias.sha256,
            size=len(alias.content),
            limit=5,
            budget_tag="explicit_alias",
        )
    model = collector.finish({"status": "current"})
    observer = CountedClosingObserver(program)
    # When the closing ledger groups only exact logical names.
    result = models.validate(model, program, observation=observation, observer=observer)
    # Then original aliases still cross their own fresh admission boundary.
    assert result == {"status": "current"}
    assert {call[1] for call in observer.file_calls} == {"proof", "./proof"}
    assert {(program.repo, "proof"), (program.repo, "./proof")} <= set(observer.admission_calls)


def test_conflicting_alias_image_is_rejected_before_grouped_replay(image):
    # Given independently named aliases that claim conflicting immutable images.
    program, observation, collector = image
    (program.repo / "proof").write_bytes(b"abc")
    with models.collect(collector):
        fp.ReadBudget(10).capture(program.repo, "proof", 3)
        alias = fp.ReadBudget(10).capture(program.repo, "./proof", 5)
        collector.observe_file(
            program.repo,
            "./proof",
            kind="file",
            sha256=alias.sha256,
            size=len(alias.content),
            limit=5,
            budget_tag="explicit_alias",
        )
    model = collector.finish({"status": "current"})
    next(row for row in model["dependencies"]["files"] if row["name"] == "./proof")["sha256"] = (
        "0" * 64
    )
    observer = CountedClosingObserver(program)
    # When the original alias constraints disagree.
    result = models.validate(model, program, observation=observation, observer=observer)
    # Then logical grouping cannot conceal the original physical-image conflict.
    assert result is None
    assert observer.file_calls == []


def test_grouped_observation_never_survives_into_next_validation_call(image):
    # Given a model closed successfully once.
    program, observation, _ = image
    model = repeated_file_model(image)
    assert models.validate(
        model, program, observation=observation, observer=CountedClosingObserver(program)
    ) == {"status": "current"}
    # When a new observation sees changed bytes of the same size.
    (program.repo / "proof").write_bytes(b"xyz")
    observer = CountedClosingObserver(program)
    result = models.validate(model, program, observation=observation, observer=observer)
    # Then fresh bytes, not any preceding call's success, control currentness.
    assert result is None
    assert [call[2] for call in observer.file_calls] == [3]


def test_collector_reuses_only_pure_normalization_for_exact_descriptors(image, monkeypatch):
    # Given repeated successful hooks with identical immutable lexical descriptors.
    program, _, collector = image
    counts = {"root": 0, "json": 0}
    original_root, original_bytes = models._root, models._bytes

    def counted_root(path):
        counts["root"] += 1
        return original_root(path)

    def counted_bytes(value):
        counts["json"] += 1
        return original_bytes(value)

    monkeypatch.setattr(models, "_root", counted_root)
    monkeypatch.setattr(models, "_bytes", counted_bytes)
    # When the same initial observation receives repeated reader metadata.
    for _ in range(20):
        collector.observe_admission(program.repo, "proof")
        collector.observe_file(
            program.repo,
            "proof",
            kind="file",
            sha256="a" * 64,
            size=0,
            limit=0,
            budget_tag="reader",
        )
        collector.observe_probe(program.repo, "proof", operation="kind", outcome="file")
    # Then only pure lexical work is reduced; all three distinct records are retained.
    assert counts == {"root": 1, "json": 3}
    assert {kind: len(rows) for kind, rows in collector._facts.items()} == {
        "admissions": 1,
        "files": 1,
        "probes": 1,
        "scopes": 0,
        "exclusions": 0,
    }


@pytest.mark.parametrize("kind", ["file", "probe", "scope"])
def test_collector_memo_never_hides_changed_observation_values(image, kind):
    # Given a descriptor already observed within the same collection context.
    program, _, collector = image
    match kind:
        case "file":
            collector.observe_file(
                program.repo,
                "proof",
                kind="file",
                sha256="a" * 64,
                size=0,
                limit=0,
                budget_tag="reader",
            )
            collector.observe_file(
                program.repo,
                "proof",
                kind="file",
                sha256="b" * 64,
                size=0,
                limit=0,
                budget_tag="reader",
            )
        case "probe":
            collector.observe_probe(program.repo, "proof", operation="exists", outcome="true")
            collector.observe_probe(program.repo, "proof", operation="exists", outcome="false")
        case "scope":
            snapshot = {"proof": "missing"}
            collector.observe_scope(
                program.repo, ["proof"], snapshot, [], byte_budget=10, entry_limit=10
            )
            snapshot["proof"] = "a" * 64
            collector.observe_scope(
                program.repo, ["proof"], snapshot, [], byte_budget=10, entry_limit=10
            )
    # When the model is frozen after conflicting actual observations.
    result = collector.finish({"unsafe": True})
    # Then cached descriptors cannot authorize a last-observation success.
    assert result["eligible"] is False
    assert "conflicting_observation" in result["reasons"]
    assert "snapshot" not in result


def test_collector_pure_memo_cannot_cross_relative_root_context(image, monkeypatch, tmp_path):
    # Given a relative root initially resolving inside the bound data repository.
    program, _, collector = image
    monkeypatch.chdir(program.repo.parent)
    collector.observe_admission(Path(program.repo.name), "proof")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    # When that relative root would denote another location in a changed process context.
    monkeypatch.chdir(elsewhere)
    collector.observe_admission(Path(program.repo.name), "proof")
    result = collector.finish({"unsafe": True})
    # Then no cached absolute identity is silently applied to a different root.
    assert result["eligible"] is False
    assert "dependency_outside_bound_root" in result["reasons"]


def test_collector_pure_memos_are_bounded_independently_of_overflow(image, monkeypatch):
    # Given a small derived-model entry limit.
    program, _, collector = image
    monkeypatch.setattr(models, "ENTRY_LIMIT", 1)
    # When additional descriptors continue after eligibility has already been lost.
    for name in ("one", "two", "three"):
        collector.observe_file(
            program.repo, name, kind="file", sha256="a" * 64, size=0, limit=0, budget_tag="reader"
        )
    # Then no hidden lexical memo grows without the same finite dependency bound.
    assert (
        max(
            len(collector._normalized_roots),
            len(collector._path_specs),
            len(collector._physical_names),
            len(collector._key_bytes),
        )
        <= 1
    )
    assert collector.finish({"unsafe": True})["eligible"] is False
