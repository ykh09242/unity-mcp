"""Detached wait admission survives cancellation at the first authorization check."""

import asyncio
from contextvars import Context
from types import SimpleNamespace

import anyio
import pytest

from services.api_key_service import ValidationResult
from services.credential_lease import current_credential_lease
from services.test_wait_journal import (
    DeadlineCheckpoint,
    JournalCapacity,
    JournalUnavailable,
    WaitJournal,
    WaitOperation,
)
from tests.http.test_remote_credential_leases import credential_service
from tests.timing import await_completion, await_signal
from transport.test_job_tasks import CapturedWait, TestJobTasks, WaitAdmission


def admitted_wait(journal, lease, execute):
    """Use the real durable admission and worker boundary with an owned native stub."""
    operation = WaitOperation(
        owner="hosted:v1:alice",
        user_id="alice",
        project="fixture-project",
        instance="Fixture@fixture-project",
        session_id="fixture-session",
        job_id="fixture-job",
        wait=DeadlineCheckpoint.capture(10, now=1000),
        outer=DeadlineCheckpoint.capture(20, now=1000),
    )

    async def unused(*_args):
        pytest.fail("An admitted wait must not recapture the request")

    runtime = TestJobTasks(
        journal,
        capture=unused,
        execute=execute,
        owner=unused,
        admission=WaitAdmission(total=1),
        executor_owns_deadline=True,
    )
    runtime._closed = False
    record = journal.create(operation)
    slot = runtime.admission.acquire(operation)
    now = asyncio.get_running_loop().time()
    captured = CapturedWait(operation, now + 10, now + 20, {}, credential_lease=lease)
    runtime._scopes[record.task_id] = anyio.CancelScope()
    worker = asyncio.create_task(runtime._run(record.task_id, captured, slot), context=Context())
    runtime._workers[record.task_id] = worker
    return SimpleNamespace(runtime=runtime, worker=worker, record=record, operation=operation)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ending,status,error_fragment",
    [
        ("fresh", "completed", None),
        ("already_retired", "failed", "Remote authorization ended"),
        ("renewal_refused", "failed", "Remote authorization ended"),
        ("renewal_timeout", "failed", "Remote authorization ended"),
        ("cancelled_at_entry", "failed", "server shutdown"),
        ("client_cancelled_at_entry", "cancelled", None),
    ],
)
async def test_entry_authorization_closes_record_and_restores_capacity(
    monkeypatch, tmp_path, ending, status, error_fragment
):
    async with credential_service(monkeypatch, ttl=1, max_age=1) as fixture:
        lease = (await fixture.service.validate("fixture-key")).lease
        entered, release = asyncio.Event(), asyncio.Event()
        executions, finishes = [], []

        async def provider(key):
            assert key == "fixture-key"
            entered.set()
            await release.wait()
            return ValidationResult(valid=False, error="Fixture refusal")

        async def execute(captured):
            assert captured.credential_lease is current_credential_lease() is lease
            executions.append(captured.operation.job_id)
            return {"content": []}

        monkeypatch.setattr(fixture.service, "_validate_external", provider)
        if ending == "already_retired":
            await fixture.service.invalidate_cache("fixture-key")
        elif ending != "fresh":
            fixture.now[0] = 2
        if ending == "renewal_timeout":
            import services.api_key_service as service_module

            async def expire_after_entry(tasks, *, timeout):
                assert timeout > 0
                await await_signal(entered, "renewal provider entry", producers=tasks)
                return await asyncio.wait(tasks, timeout=0)

            monkeypatch.setattr(
                service_module,
                "asyncio",
                SimpleNamespace(**{**vars(asyncio), "wait": expire_after_entry}),
            )

        journal = WaitJournal(
            tmp_path / "waits",
            clock=lambda: 1000.0,
            result_bytes=128,
            metadata_bytes=2048,
            quota_bytes=5500,
        )
        finish = journal.finish

        def observed_finish(*args, **kwargs):
            finishes.append((args[0], args[1], fixture.service._lease_owners))
            return finish(*args, **kwargs)

        monkeypatch.setattr(journal, "finish", observed_finish)
        wait = admitted_wait(journal, lease, execute)
        runtime = wait.runtime
        try:
            with pytest.raises(JournalCapacity):
                journal.create(wait.operation)
            with pytest.raises(JournalCapacity):
                runtime.admission.acquire(wait.operation)
            if ending not in {"fresh", "already_retired"}:
                await await_signal(entered, "initial renewal", producers=(wait.worker,))
                if ending == "renewal_refused":
                    release.set()
                elif ending in {"cancelled_at_entry", "client_cancelled_at_entry"}:
                    if ending == "client_cancelled_at_entry":
                        journal.request_cancel(wait.record.task_id, wait.operation.owner)
                        runtime._cancelled.add(wait.record.task_id)
                    wait.worker.cancel()

            outcomes = await await_completion(
                asyncio.gather(wait.worker, return_exceptions=True), "initial wait cleanup"
            )
            stored = journal.get(wait.record.task_id, wait.operation.owner)
            assert stored.status == status
            assert outcomes == [None]
            assert stored.result == ({"content": []} if ending == "fresh" else None)
            if error_fragment is not None:
                assert stored.error["code"] == -32603
                assert error_fragment in stored.error["message"]
            assert executions == (["fixture-job"] if ending == "fresh" else [])
            assert finishes == [
                (wait.record.task_id, status, 0 if ending == "already_retired" else 1)
            ]
            assert not runtime.admission.active
            assert not runtime.admission.principals and not runtime.admission.projects
            assert not runtime._workers and not runtime._scopes and not runtime._cancelled
            assert fixture.service._lease_owners == 0

            # The same open journal and admission gate can accept another wait.
            slot = runtime.admission.acquire(wait.operation)
            following = journal.create(wait.operation)
            journal.finish(following.task_id, "cancelled")
            runtime.admission.release(slot)
            await runtime.close(grace_seconds=0)
            assert journal._writer is None
            with WaitJournal(journal.root, clock=lambda: 1000.0) as reopened:
                assert reopened.get(wait.record.task_id, wait.operation.owner).status == status
        finally:
            release.set()
            if not wait.worker.done():
                wait.worker.cancel()
            await asyncio.gather(wait.worker, return_exceptions=True)
            await runtime.close(grace_seconds=0)
            journal.close()


@pytest.mark.asyncio
async def test_running_noncooperative_wait_keeps_admission_until_actual_exit(monkeypatch, tmp_path):
    async with credential_service(monkeypatch) as fixture:
        lease = (await fixture.service.validate("fixture-key")).lease
        entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def execute(_captured):
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                with anyio.CancelScope(shield=True):
                    await release.wait()
            return {"content": []}

        journal = WaitJournal(tmp_path / "waits", clock=lambda: 1000.0)
        wait = admitted_wait(journal, lease, execute)
        runtime = wait.runtime
        try:
            await await_signal(entered, "native wait entry", producers=(wait.worker,))
            await fixture.service.invalidate_cache("fixture-key")
            await await_signal(cancelled, "native wait cancellation", producers=(wait.worker,))
            await runtime.close(grace_seconds=0)
            assert not wait.worker.done()
            assert journal.get(wait.record.task_id, wait.operation.owner).status == "working"
            assert fixture.service._lease_owners == 1
            assert len(runtime.admission.active) == 1
            assert wait.record.task_id in runtime._workers
            assert wait.record.task_id in runtime._scopes
            with pytest.raises(JournalCapacity):
                runtime.admission.acquire(wait.operation)
            with pytest.raises(JournalUnavailable):
                WaitJournal(journal.root)
        finally:
            release.set()
            await await_completion(wait.worker, "native wait exit")
            await runtime.close(grace_seconds=0)
        assert not runtime.admission.active and not runtime._workers and not runtime._scopes
        assert fixture.service._lease_owners == 0
        assert journal._writer is None
        with WaitJournal(journal.root, clock=lambda: 1000.0) as reopened:
            stored = reopened.get(wait.record.task_id, wait.operation.owner)
            assert stored.status == "failed"
            assert "Remote authorization ended" in stored.error["message"]
