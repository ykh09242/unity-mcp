"""Durable bounded-wait records, independent of production installation."""

from tests.timing import watchdog_timeout

import json
import os
import asyncio
import threading
import time
import stat
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock

import anyio

import pytest

from services.test_wait_journal import (
    DeadlineCheckpoint,
    JournalCapacity,
    JournalUnavailable,
    TaskNotFound,
    WaitJournal,
    WaitOperation,
    WaitRecord,
)
from pydantic import BaseModel, field_serializer


class JournalPayloadModel(BaseModel):
    value: str
    omitted: str | None = None


class JournalPayloadDict(dict):
    pass


class JournalPayloadInt(int):
    pass


def operation(owner="alice", *, now=1000.0):
    return WaitOperation(
        owner=owner,
        project="project",
        instance="Fixture@project",
        session_id="generation-one",
        job_id="native-existing-job",
        wait=DeadlineCheckpoint.capture(20, now=now),
        outer=DeadlineCheckpoint.capture(10, now=now),
    )


def test_journal_result_survives_reopen_without_native_or_new_wait(tmp_path):
    journal = WaitJournal(tmp_path / "waits", clock=lambda: 1000.0)
    record = journal.create(operation())
    result = {
        "resultType": "complete",
        "content": [],
        "structuredContent": {"status": "running"},
        "isError": True,
    }
    journal.finish(record.task_id, "completed", result=result)
    journal.close()
    with WaitJournal(tmp_path / "waits", clock=lambda: 1001.0) as reopened:
        stored = reopened.get(record.task_id, "alice")
        assert stored.status == "completed"
        assert stored.result == result
        with pytest.raises(TaskNotFound):
            reopened.get(record.task_id, "bob")
        assert reopened.get(record.task_id, "alice").operation == operation()


@pytest.mark.parametrize("status", ["completed", "failed"])
@pytest.mark.parametrize(
    "payload, decoded, expects_warning",
    [
        (
            {"null": None, "unicode": "한글 😀 é", "numbers": [0, -3, 2**60, -0.0, 1e-200]},
            {"null": None, "unicode": "한글 😀 é", "numbers": [0, -3, 2**60, -0.0, 1e-200]},
            False,
        ),
        (
            {"nested": [{"last": None, "first": True}, [], {}]},
            {"nested": [{"last": None, "first": True}, [], {}]},
            False,
        ),
        ({"tuple": (None, "value", 1)}, {"tuple": [None, "value", 1]}, True),
        (
            {"subclass": JournalPayloadDict(value=JournalPayloadInt(17))},
            {"subclass": {"value": 17}},
            False,
        ),
        ({"model": JournalPayloadModel(value="kept")}, {"model": {"value": "kept"}}, True),
    ],
    ids=["scalars", "containers", "tuple", "subclasses", "model"],
)
def test_journal_terminal_bytes_match_storage_projection(
    tmp_path, status, payload, decoded, expects_warning
):
    def projection_warnings():
        if expects_warning:
            return pytest.warns(
                UserWarning,
                match=r"(?s)Pydantic serializer warnings:.*PydanticSerializationUnexpectedValue",
            )
        return nullcontext()

    # Given the existing storage projection, including fallback-only values.
    with WaitJournal(tmp_path / "waits", clock=lambda: 1000.0) as journal:
        record = journal.create(operation())
        field = "result" if status == "completed" else "error"
        expected_record = record.model_copy(update={"status": status, field: payload})
        with projection_warnings():
            expected = json.dumps(
                expected_record.model_dump(exclude_none=True),
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()

        # When committing the ordinary result or JSON-RPC error.
        with projection_warnings():
            journal.finish(record.task_id, status, **{field: payload})

        # Then the committed bytes and reopened value preserve that wire contract.
        assert (journal.root / f"{record.task_id}.json").read_bytes() == expected
    with WaitJournal(tmp_path / "waits", clock=lambda: 1001.0) as reopened:
        restored = reopened.get(record.task_id, "alice")
        assert getattr(restored, field) == decoded


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), {1: "non-string-key"}])
def test_journal_refuses_invalid_payload_before_replacing_working_record(tmp_path, invalid):
    # Given a committed working record and an unsupported ordinary payload.
    with WaitJournal(tmp_path / "waits", clock=lambda: 1000.0) as journal:
        record = journal.create(operation())
        path = journal.root / f"{record.task_id}.json"
        committed = path.read_bytes()

        # When final admission refuses that payload.
        with pytest.raises(JournalUnavailable):
            journal.finish(record.task_id, "completed", result={"invalid": invalid})

        # Then the reservation and original record remain available for cancellation.
        assert path.read_bytes() == committed
        assert not list(journal.root.glob("*.stage"))
        assert journal.request_cancel(record.task_id, "alice").cancel_requested


def test_journal_record_subclass_keeps_its_serialization_rules(tmp_path):
    class ProjectedRecord(WaitRecord):
        @field_serializer("result")
        def project_result(self, _value):
            return {"projected": True}

    # Given an extension record with an explicit storage serializer.
    with WaitJournal(tmp_path / "waits", clock=lambda: 1000.0) as journal:
        record = journal.create(operation())
        projected = ProjectedRecord.model_validate(record.model_dump())
        projected.status = "completed"
        projected.result = {"ordinary": "value"}

        # When that record is committed through the compatibility path.
        journal._write(projected, active=False)

        # Then the serializer's storage contract is preserved.
        assert journal.get(record.task_id, "alice").result == {"projected": True}


def test_journal_projection_failure_preserves_commit_and_cleans_stage(tmp_path):
    class RefusedRecord(WaitRecord):
        @field_serializer("result")
        def refuse_result(self, _value):
            raise ValueError("synthetic projection refusal")

    # Given a previously committed record and a refused extension projection.
    with WaitJournal(tmp_path / "waits", clock=lambda: 1000.0) as journal:
        record = journal.create(operation())
        path = journal.root / f"{record.task_id}.json"
        committed = path.read_bytes()
        refused = RefusedRecord.model_validate(record.model_dump())
        refused.status = "completed"
        refused.result = {"ordinary": "value"}

        # When the storage serializer refuses the atomic replacement.
        with pytest.raises(JournalUnavailable):
            journal._write(refused, active=False)

        # Then the original commit survives with no partial stage.
        assert path.read_bytes() == committed
        assert not list(journal.root.glob("*.stage"))


def test_interrupted_work_is_honest_jsonrpc_failure_on_reopen(tmp_path):
    with WaitJournal(tmp_path / "waits", clock=lambda: 1000.0) as journal:
        task_id = journal.create(operation()).task_id
    with WaitJournal(tmp_path / "waits", clock=lambda: 1005.0) as reopened:
        record = reopened.get(task_id, "alice")
        assert record.status == "failed"
        assert record.error["code"] == -32603
        assert "interrupted" in record.error["message"].lower()
        assert record.operation.outer.remaining(now=1005.0) == 5
        assert record.result is None


@pytest.mark.parametrize("seconds", [300, 86400])
def test_outer_deadline_preserves_large_command_policy_across_persistence(seconds):
    captured = operation().model_copy(
        update={"outer": DeadlineCheckpoint.capture(seconds, now=1000.0)}
    )
    restored = WaitOperation.model_validate_json(captured.model_dump_json())
    assert restored.outer.remaining(now=1002.0) == seconds - 2
    assert restored.wait_timeout <= 60
    assert restored.outer.remaining(now=999.0) == 0
    with pytest.raises(ValueError):
        DeadlineCheckpoint.capture(86401, now=1000.0)


def test_deadlines_never_gain_time_on_restart_or_clock_rollback():
    checkpoint = DeadlineCheckpoint.capture(10, now=100)
    assert checkpoint.remaining(now=103) == 7
    assert checkpoint.remaining(now=200) == 0
    assert checkpoint.remaining(now=99) == 0
    assert DeadlineCheckpoint.capture(0, now=100).remaining(now=100) == 0


def test_reservation_includes_result_metadata_and_replacement(tmp_path):
    with WaitJournal(
        tmp_path / "waits",
        clock=lambda: 1000.0,
        result_bytes=128,
        metadata_bytes=2048,
        quota_bytes=5500,
    ) as journal:
        record = journal.create(operation())
        assert journal.charged_bytes >= 2 * (128 + 2048)
        with pytest.raises(JournalCapacity):
            journal.create(operation("bob"))
        # The record exceeds the payload ceiling; its valid ordinary payload does not.
        result = {"content": [], "structuredContent": {"value": "x" * 60}}
        assert len(json.dumps(result, separators=(",", ":")).encode()) <= 128
        journal.finish(record.task_id, "completed", result=result)
        assert journal.get(record.task_id, "alice").result == result
        assert journal.charged_bytes == sum(
            path.stat().st_size for path in (tmp_path / "waits").iterdir()
        )
        journal.create(operation("bob"))


def test_terminal_state_and_retention_are_not_renewed_by_reads(tmp_path):
    now = [1000.0]
    with WaitJournal(tmp_path / "waits", clock=lambda: now[0], ttl_seconds=10) as journal:
        record = journal.create(operation())
        journal.finish(record.task_id, "completed", result={"content": []})
        journal.finish(record.task_id, "cancelled")
        assert journal.get(record.task_id, "alice").status == "completed"
        now[0] = 1010
        with pytest.raises(TaskNotFound):
            journal.get(record.task_id, "alice")
        journal.prune()
        assert not list((tmp_path / "waits").glob("*.json"))


def test_exclusive_writer_and_corruption_fail_closed(tmp_path):
    root = tmp_path / "waits"
    with WaitJournal(root, clock=lambda: 1000.0) as journal:
        with pytest.raises(JournalUnavailable):
            WaitJournal(root)
        record = journal.create(operation())
    (root / f"{record.task_id}.json").write_text('{"schema":999}', encoding="utf-8")
    with pytest.raises(JournalUnavailable):
        WaitJournal(root, clock=lambda: 1001.0)


def test_atomic_replacement_failure_keeps_prior_committed_record(tmp_path, monkeypatch):
    with WaitJournal(tmp_path / "waits", clock=lambda: 1000.0) as journal:
        record = journal.create(operation())
        original = os.replace

        def reject_replace(*_args):
            raise OSError("injected disk replacement failure")

        monkeypatch.setattr(os, "replace", reject_replace)
        with pytest.raises(JournalUnavailable):
            journal.finish(record.task_id, "completed", result={"content": []})
        assert journal.get(record.task_id, "alice").status == "working"
        assert not list(journal.root.glob("*.stage"))
        monkeypatch.setattr(os, "replace", original)
        journal.finish(record.task_id, "completed", result={"content": []})
        assert journal.get(record.task_id, "alice").status == "completed"


def test_full_supported_ordinary_payload_has_separate_record_overhead(tmp_path):
    from models.response_limits import MAX_RESPONSE_BYTES, response_size

    shell = {"content": [{"type": "text", "text": ""}]}
    length = len(json.dumps(shell, ensure_ascii=False).encode())
    shell["content"][0]["text"] = "x" * (MAX_RESPONSE_BYTES - length)
    assert len(json.dumps(shell, ensure_ascii=False).encode()) == MAX_RESPONSE_BYTES
    assert response_size(shell) is not None
    with WaitJournal(tmp_path / "waits", clock=lambda: 1000.0) as journal:
        record = journal.create(operation())
        journal.finish(record.task_id, "completed", result=shell)
        path = journal.root / f"{record.task_id}.json"
        assert path.stat().st_size > MAX_RESPONSE_BYTES
        assert journal.get(record.task_id, "alice").result == shell
        assert journal.charged_bytes <= journal.quota_bytes


def test_private_directory_does_not_remove_foreign_entries(tmp_path):
    root = tmp_path / "waits"
    root.mkdir()
    foreign = root / "foreign.txt"
    foreign.write_text("preserve", encoding="utf-8")
    with pytest.raises(JournalUnavailable):
        WaitJournal(root)
    assert foreign.read_text(encoding="utf-8") == "preserve"


def test_nonsecret_installation_identity_is_stable_and_namespaces_local_owner(tmp_path):
    root = tmp_path / "waits"
    with WaitJournal(root) as journal:
        identity = journal.installation_id
        assert len(identity) == 32
        assert journal.local_owner == f"local:v1:{identity}"
    with WaitJournal(root) as journal:
        assert journal.installation_id == identity
    with WaitJournal(tmp_path / "other") as journal:
        assert journal.installation_id != identity


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_existing_records_never_get_reassigned_when_installation_identity_is_lost(tmp_path, damage):
    root = tmp_path / "waits"
    with WaitJournal(root, clock=lambda: 1000.0) as journal:
        record = journal.create(operation(journal.local_owner))
    identity = root / "installation"
    if damage == "missing":
        identity.unlink()
    else:
        identity.write_bytes(b"corrupt")
    with pytest.raises(JournalUnavailable):
        WaitJournal(root, clock=lambda: 1001.0)
    assert (root / f"{record.task_id}.json").exists()
    assert not identity.exists() if damage == "missing" else identity.read_bytes() == b"corrupt"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation_name", ["get", "request_cancel"])
async def test_shutdown_retains_actual_journal_io_without_blocking_loop(
    tmp_path, monkeypatch, operation_name
):
    from transport.test_job_tasks import TestJobTasks, WaitAdmission

    journal = WaitJournal(tmp_path / "waits", clock=lambda: 1000.0)
    record = journal.create(operation())
    runtime = TestJobTasks(
        journal,
        capture=AsyncMock(),
        execute=AsyncMock(),
        owner=AsyncMock(),
        admission=WaitAdmission(),
    )
    entered, release = threading.Event(), threading.Event()
    original = journal._read if operation_name == "get" else journal._write

    def held(*args, **kwargs):
        # Both callers already own the real journal RLock at this boundary.
        entered.set()
        assert release.wait(watchdog_timeout(120)), "journal lock watchdog must release the test"
        return original(*args, **kwargs)

    monkeypatch.setattr(journal, "_read" if operation_name == "get" else "_write", held)
    async with runtime.lifespan():
        io = asyncio.create_task(
            runtime._io(getattr(journal, operation_name), record.task_id, "alice")
        )
        watchdog = threading.Timer(watchdog_timeout(), release.set)
        try:
            with anyio.fail_after(watchdog_timeout()):
                while not entered.is_set():
                    await anyio.sleep(0)
            before = journal.charged_bytes
            watchdog.start()
            await runtime.close(grace_seconds=0)
            await anyio.sleep(0)
            assert not release.is_set(), "Shutdown waited for blocked journal I/O"
            assert journal._writer is not None
            assert not io.done()
            assert journal.charged_bytes == before
            with pytest.raises(JournalUnavailable):
                WaitJournal(journal.root)
        finally:
            release.set()
            watchdog.cancel()
            await io
    assert journal._writer is None
    with WaitJournal(journal.root, clock=lambda: 1000.0):
        pass


@pytest.mark.skipif(os.name != "posix", reason="Requires native POSIX owner/mode enforcement")
def test_native_posix_insecure_existing_directory_is_refused_and_preserved(tmp_path):
    root = tmp_path / "waits"
    root.mkdir(mode=0o755)
    root.chmod(0o755)
    with pytest.raises(JournalUnavailable):
        WaitJournal(root)
    assert root.stat().st_mode & 0o777 == 0o755
    assert not list(root.iterdir())


@pytest.mark.skipif(os.name != "posix", reason="Requires native POSIX owner/mode enforcement")
def test_native_posix_created_files_private_and_insecure_entry_preserved(tmp_path):
    root = tmp_path / "waits"
    previous = os.umask(0o022)
    try:
        with WaitJournal(root, clock=lambda: 1000.0) as journal:
            record = journal.create(operation())
            journal.finish(record.task_id, "completed", result={"content": []})
        assert root.stat().st_mode & 0o777 == 0o700
        assert all(path.stat().st_mode & 0o777 == 0o600 for path in root.iterdir())
        path = root / f"{record.task_id}.json"
        path.chmod(0o644)
        with pytest.raises(JournalUnavailable):
            WaitJournal(root, clock=lambda: 1000.0)
        assert path.stat().st_mode & 0o777 == 0o644
        assert path.exists()
    finally:
        os.umask(previous)


@pytest.mark.parametrize(
    "mode, owner, directory, allowed",
    [
        (stat.S_IFDIR | 0o700, 1001, True, True),
        (stat.S_IFDIR | 0o755, 1001, True, False),
        (stat.S_IFDIR | 0o700, 1002, True, False),
        (stat.S_IFREG | 0o600, 1001, False, True),
        (stat.S_IFREG | 0o644, 1001, False, False),
        (stat.S_IFREG | 0o600, 1002, False, False),
    ],
)
def test_posix_privacy_policy_units_are_not_native_permission_evidence(
    mode, owner, directory, allowed
):
    info = SimpleNamespace(st_mode=mode, st_uid=owner)
    if allowed:
        WaitJournal._check_posix_stat(info, uid=1001, directory=directory)
    else:
        with pytest.raises(JournalUnavailable):
            WaitJournal._check_posix_stat(info, uid=1001, directory=directory)


@pytest.mark.asyncio
async def test_final_worker_cleanup_does_not_close_over_a_live_read_thread(tmp_path, monkeypatch):
    from contextvars import Context
    from transport.test_job_tasks import CapturedWait, TestJobTasks, WaitAdmission

    journal = WaitJournal(tmp_path / "waits", clock=lambda: 1000.0)
    op = operation()
    record = journal.create(op)
    runtime = TestJobTasks(
        journal,
        capture=AsyncMock(),
        execute=AsyncMock(return_value={"content": []}),
        owner=AsyncMock(),
        admission=WaitAdmission(),
    )
    committed, entered, release = threading.Event(), threading.Event(), threading.Event()
    finish, read = journal.finish, journal._read

    def committed_finish(*args, **kwargs):
        finish(*args, **kwargs)
        committed.set()
        assert entered.wait(watchdog_timeout())

    def held_read(*args, **kwargs):
        entered.set()
        assert release.wait(watchdog_timeout())
        return read(*args, **kwargs)

    monkeypatch.setattr(journal, "finish", committed_finish)
    async with runtime.lifespan():
        captured = CapturedWait(
            op,
            asyncio.get_running_loop().time() + 5,
            asyncio.get_running_loop().time() + 5,
            {"content": []},
        )
        admission = runtime.admission.acquire(op)
        runtime._scopes[record.task_id] = anyio.CancelScope()
        worker = asyncio.create_task(
            runtime._run(record.task_id, captured, admission), context=Context()
        )
        runtime._workers[record.task_id] = worker
        with anyio.fail_after(watchdog_timeout()):
            while not committed.is_set():
                await anyio.sleep(0)
        monkeypatch.setattr(journal, "_read", held_read)
        io = asyncio.create_task(runtime._io(journal.get, record.task_id, "alice"))
        watchdog = threading.Timer(watchdog_timeout(), release.set)
        try:
            with anyio.fail_after(watchdog_timeout()):
                while not entered.is_set():
                    await anyio.sleep(0)
            watchdog.start()
            await runtime.close(grace_seconds=0)
            await worker
            await anyio.sleep(0)
            assert not release.is_set(), "Shutdown waited for blocked journal I/O"
            assert journal._writer is not None
            assert runtime._io_owners == 1
            assert not io.done()
            assert not runtime.admission.active
        finally:
            release.set()
            watchdog.cancel()
            await io
    assert journal._writer is None
