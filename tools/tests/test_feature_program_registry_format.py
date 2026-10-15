"""Registry-only compaction preserves immutable identities and ordinary publication guards."""

from copy import deepcopy
import hashlib
import json

import pytest

from tools.tests.test_feature_program_review_throughput_integration import fixture, fp  # noqa: F401
from tools.tests.test_feature_program_phases import gate  # noqa: F401
from tools.tests.test_feature_program_presentation import language, receipt, record, inventory

MARKER = {"schema_version": 1, "encoding": "compact-ascii"}


def enable(program, owner="leader"):
    with program.lock():
        return fp.storage_enable(program, owner=owner, reason="Synthetic storage fixture")


def migrate(program, owner="leader", revision=None):
    state = program.load()
    with program.lock():
        return fp.registry_compact(
            program,
            owner=owner,
            reason="Synthetic registry capacity",
            expected_revision=revision or fp.phases.revision(state),
        )


def test_legacy_and_compact_registry_identity_leave_generic_artifacts_unchanged(fixture):
    state = fixture.program.load()
    legacy = (
        json.dumps(state, indent=2, ensure_ascii=True, sort_keys=True, allow_nan=False) + "\n"
    ).encode()
    assert fp.registry_body(state) == fp.canonical(state) == legacy
    assert fp.phases.revision(state) == hashlib.sha256(legacy).hexdigest()
    artifact = {"registry_format": MARKER, "text": "한국어", "value": True}
    frozen = fp.canonical(artifact)
    marked = {**state, "registry_format": deepcopy(MARKER)}
    compact = fp.registry_body(marked)
    assert (
        compact
        == (
            json.dumps(
                marked, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
            )
            + "\n"
        ).encode()
    )
    assert json.loads(compact) == marked
    assert fp.phases.revision(marked) == fp.sha(compact)
    assert fp.canonical(artifact) == frozen and fp.digest(artifact) == fp.sha(frozen)
    assert fp.canonical(marked) != compact


@pytest.mark.parametrize(
    "marker",
    [
        None,
        True,
        "compact-ascii",
        {},
        {"schema_version": True, "encoding": "compact-ascii"},
        {"schema_version": 2, "encoding": "compact-ascii"},
        {"schema_version": 1, "encoding": "utf8"},
        {**MARKER, "extra": False},
    ],
)
def test_registry_format_marker_is_closed_at_typed_read_and_save(fixture, marker):
    state = fixture.program.load()
    state["registry_format"] = marker
    with pytest.raises(fp.ProgramError, match="registry format"):
        fp.registry_body(state)
    with pytest.raises(fp.ProgramError, match="registry format"):
        fixture.program.validate(state)
    observed = fp.flow_module("storage").decode_registry(fp.canonical(state))
    with pytest.raises(fp.ProgramError, match="registry format"):
        fp.parse_registry_state(observed, path=fixture.program.path("backlog.json"))


def test_enabled_migration_backs_up_exact_envelope_and_refreshes_model(fixture):
    enable(fixture.program)
    original = fixture.program.read_registry_observation()
    state = fixture.program.load()
    result = migrate(fixture.program)
    current = fixture.program.read_registry_observation()
    marked = fixture.program.load()
    assert fixture.program.path(result["backup"]["path"]).read_bytes() == original.raw_bytes
    assert result["backup"]["sha256"] == original.storage_sha256
    assert marked.pop("registry_format") == MARKER and marked == state
    assert current.model_id != original.model_id
    assert current.body_sha256 == result["revision"] == fp.phases.revision(fixture.program.load())
    fast = fp._read_materialized_projection(fixture.program.repo, fixture.program.directory)
    assert fast is not None and fast["revision"] == current.body_sha256
    # Existing storage repair preserves the NEW exact BODY rather than migrating again.
    enable(fixture.program)
    assert fixture.program.read_state_bytes() == current.body_bytes


@pytest.mark.parametrize("failure", ["owner", "revision", "legacy", "lock"])
def test_migration_rejects_missing_authority_identity_or_owned_lock(fixture, failure):
    if failure != "legacy":
        enable(fixture.program)
    original = fixture.program.read_registry_observation()
    if failure == "lock":
        with pytest.raises(fp.ProgramError, match="owned program lock"):
            fp.registry_compact(
                fixture.program,
                owner="leader",
                reason="Synthetic",
                expected_revision=original.body_sha256,
            )
    else:
        with pytest.raises(fp.ProgramError):
            migrate(
                fixture.program,
                owner="worker" if failure == "owner" else "leader",
                revision="0" * 64 if failure == "revision" else None,
            )
    assert fixture.program.read_registry_observation().raw_bytes == original.raw_bytes


@pytest.mark.parametrize("failure", ["close", "source", "selector", "backup"])
def test_migration_failure_preserves_canonical_before_atomic_commit(fixture, monkeypatch, failure):
    enable(fixture.program)
    original = fixture.program.read_registry_observation()
    source_name = fixture.program.load()["source"]["path"]
    real_close = fp._close_staged_model
    foreign = None
    if failure == "backup":
        name = f"recovery/storage/compact-ascii/{original.storage_sha256}/original-backlog.json"
        fixture.program.path(name).parent.mkdir(parents=True)
        fixture.program.path(name).write_bytes(b"conflicting backup")
    else:

        def changed(program, staged):
            if failure == "close":
                raise fp.ProgramError("Synthetic precommit closure failure")
            if failure == "source":
                (program.repo / source_name).write_bytes(b"Synthetic changed source")
            if failure == "selector":
                program.path("backlog.json").write_bytes(foreign)
            real_close(program, staged)

        if failure == "selector":
            foreign = fp.flow_module("storage").encode_registry(original.body_bytes, "f" * 64)
        monkeypatch.setattr(fp, "_close_staged_model", changed)
    with pytest.raises(fp.ProgramError):
        migrate(fixture.program)
    assert fixture.program.read_registry_observation().raw_bytes == (foreign or original.raw_bytes)


def test_render_failure_is_committed_once_and_never_replays_migration(fixture, monkeypatch):
    enable(fixture.program)
    original = fixture.program.load()
    revision = fp.phases.revision(original)
    monkeypatch.setattr(
        fp.Program,
        "render",
        lambda *_args: (_ for _ in ()).throw(OSError("Synthetic render failure")),
    )
    with pytest.raises(fp.ProgramError, match="Registry committed at revision") as error:
        migrate(fixture.program, revision=revision)
    current = fixture.program.read_registry_observation()
    assert current.body_sha256 in str(error.value)
    state = fixture.program.load()
    assert state["registry_format"] == MARKER and state["events"] == original["events"]
    with pytest.raises(fp.ProgramError, match="revision|already"):
        migrate(fixture.program, revision=revision)
    assert fixture.program.read_registry_observation().raw_bytes == current.raw_bytes


def test_backup_tampering_during_model_close_cannot_commit(fixture, monkeypatch):
    enable(fixture.program)
    original = fixture.program.read_registry_observation()
    close = fp._close_staged_model

    def tamper(program, staged):
        close(program, staged)
        program.path(
            f"recovery/storage/compact-ascii/{original.storage_sha256}/original-backlog.json"
        ).write_bytes(b"changed backup")

    monkeypatch.setattr(fp, "_close_staged_model", tamper)
    with pytest.raises(fp.ProgramError, match="backup changed"):
        migrate(fixture.program)
    assert fixture.program.read_registry_observation().raw_bytes == original.raw_bytes


def test_byte_identical_backup_directory_alias_after_close_cannot_commit(fixture, monkeypatch):
    from tools.tests.test_feature_program import _directory_alias

    enable(fixture.program)
    original = fixture.program.read_registry_observation()
    close = fp._close_staged_model

    def replace_backup_directory(program, staged):
        close(program, staged)
        parent = program.path(
            f"recovery/storage/compact-ascii/{original.storage_sha256}/original-backlog.json"
        ).parent
        target = program.repo.parent / "identical-backup-target"
        parent.rename(target)
        _directory_alias(parent, target)

    monkeypatch.setattr(fp, "_close_staged_model", replace_backup_directory)
    with pytest.raises(fp.ProgramError):
        migrate(fixture.program)
    assert fixture.program.read_registry_observation().raw_bytes == original.raw_bytes


def test_public_save_has_no_callback_after_final_closure(fixture):
    enable(fixture.program)
    original = fixture.program.read_registry_observation()
    state = fixture.program.load()
    source = fixture.program.repo / state["source"]["path"]
    content = source.read_bytes()
    called = []

    def change_source_after_closure():
        called.append(True)
        source.write_bytes(b"Synthetic post-closure source mutation")

    with fixture.program.lock(), pytest.raises(TypeError, match="before_atomic"):
        fixture.program.save(state, before_atomic=change_source_after_closure)
    assert called == [] and source.read_bytes() == content
    assert fixture.program.read_registry_observation().raw_bytes == original.raw_bytes


@pytest.mark.parametrize("interrupt", [False, True])
def test_marked_relocation_and_recovery_preserve_compact_identity(fixture, monkeypatch, interrupt):
    from tools.tests.test_feature_program import Fixture

    enable(fixture.program)
    migrate(fixture.program)
    original = fixture.program.read_registry_observation()
    state = fixture.program.load()
    target = fp.Program(fixture.program.repo, fixture.program.repo / ".work/programs/moved")
    write = fp.atomic_write

    def fail_archive(path, content):
        if interrupt and path == fixture.program.path("RELOCATED.json"):
            raise OSError("Synthetic interrupted archive")
        return write(path, content)

    monkeypatch.setattr(fp, "atomic_write", fail_archive)
    assert fixture.run(
        "relocate",
        "--destination",
        ".work/programs/moved",
        "--owner",
        "leader",
        "--reason",
        "Synthetic compact relocation",
    ) == (1 if interrupt else 0)
    if interrupt:
        monkeypatch.setattr(fp, "atomic_write", write)
        assert (
            Fixture(target, fixture.report).run(
                "relocate-recover", "--owner", "leader", "--reason", "Inspect actual copied result"
            )
            == 0
        )
    moved = target.load()
    observed = target.read_registry_observation()
    assert moved["registry_format"] == MARKER and moved["tasks"] == state["tasks"]
    assert observed.body_bytes == fp.registry_body(moved)
    assert observed.body_sha256 == fp.phases.revision(moved)
    assert (
        target.path(
            f"recovery/relocations/{original.storage_sha256}/original-backlog.json"
        ).read_bytes()
        == original.raw_bytes
    )


@pytest.mark.parametrize("marked_side", ["predecessor", "successor"])
def test_mixed_legacy_compact_succession_keeps_exact_originals(gate, marked_side):
    from tools.tests.test_feature_program_phases import create_program, succession_arguments

    target = create_program(gate.program.repo, "execution")
    selected = gate.program if marked_side == "predecessor" else target.program
    enable(selected, "director" if marked_side == "predecessor" else "leader")
    migrate(selected, "director" if marked_side == "predecessor" else "leader")
    originals = {
        program.path("backlog.json"): program.read_registry_observation().raw_bytes
        for program in (gate.program, target.program)
    }
    assert gate.fixture.run(*succession_arguments(gate, target)) == 0
    assert all(path.read_bytes() == raw for path, raw in originals.items())


def test_near_limit_migration_and_real_presentation_keep_frozen_review(gate):
    language(gate)
    old_receipt = record(gate)
    frozen = {
        ref["path"]: gate.program.path(ref["path"]).read_bytes() for ref in [old_receipt["review"]]
    }
    state = gate.program.load()
    # A valid synthetic extension makes pretty overhead approach the ACTUAL8MiB bound.
    state["capacity_fixture"] = [""] * ((8 * 1024 * 1024 - len(fp.canonical(state)) - 2048) // 8)
    assert len(fp.canonical(state)) < 8 * 1024 * 1024
    gate.program.save(state)
    enable(gate.program, "director")
    migrate(gate.program, "director")
    name, _value = receipt(
        gate,
        identifier="after-compact",
        kind="task_action",
        field="next_action",
        text="합성 검증 " * 600,
    )
    assert gate.fixture.run("presentation-record", "--input", name) == 0
    current = gate.program.load()
    assert len(fp.canonical(current)) > 8 * 1024 * 1024
    assert len(gate.program.read_state_bytes()) < 8 * 1024 * 1024
    assert inventory(gate)["totals"]["current_translated"] == 2
    assert all(gate.program.path(name).read_bytes() == raw for name, raw in frozen.items())
    assert gate.program.read_registry_observation().body_sha256 == fp.phases.revision(current)
