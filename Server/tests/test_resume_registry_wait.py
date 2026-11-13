"""Resume waits observe registry changes without re-probing an absent Editor."""

import asyncio
from types import SimpleNamespace

import pytest
import pytest_asyncio

from core.config import config
from tests.timing import await_signal, watchdog_timeout
from transport.command_budget import _CommandBudget
from transport.command_resume import CAPABILITY, CommandResumeInvocation
from transport.plugin_hub import PluginHub
from transport.plugin_registry import PluginRegistry


@pytest_asyncio.fixture
async def resume_wait(monkeypatch):
    registry = PluginRegistry()
    original, _ = await registry.register("old", "Project", "hash", "test")
    await registry.unregister("old")
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(PluginHub, "_registry", registry)
    monkeypatch.setattr(PluginHub, "_lock", asyncio.Lock())
    monkeypatch.setattr(PluginHub, "_connections", {})
    monkeypatch.setattr(PluginHub, "_shutdown_requested", False)
    budget = _CommandBudget(asyncio.get_running_loop().time() + 2)
    resume = CommandResumeInvocation.create("e" * 32, budget.expires_at)
    entered, lookups = asyncio.Event(), []
    lookup, wait = registry.get_session_id_by_hash, registry.wait_for_change

    async def observed_lookup(*args, **kwargs):
        lookups.append(args)
        return await lookup(*args, **kwargs)

    async def observed_wait(event, timeout):
        entered.set()
        return await wait(event, timeout)

    monkeypatch.setattr(registry, "get_session_id_by_hash", observed_lookup)
    monkeypatch.setattr(registry, "wait_for_change", observed_wait)
    return SimpleNamespace(
        registry=registry,
        original=original,
        resume=resume,
        budget=budget,
        entered=entered,
        lookups=lookups,
    )


def start_wait(fixture):
    return asyncio.create_task(
        PluginHub._resume_http_session(
            fixture.registry, fixture.original, fixture.resume, fixture.budget
        )
    )


async def register_target(fixture):
    PluginHub._connections["new"] = SimpleNamespace(
        state=SimpleNamespace(
            plugin_features={CAPABILITY}, plugin_resume_generation=fixture.resume.editor_session_id
        )
    )
    await fixture.registry.register("new", "Project", "hash", "test")


@pytest.mark.asyncio
async def test_absent_resume_does_not_repeat_lookup_or_timeout_warning(resume_wait, caplog):
    task = start_wait(resume_wait)
    try:
        await asyncio.sleep(0.25)
        assert len(resume_wait.lookups) == 1
        assert not caplog.records
        await register_target(resume_wait)
        assert await asyncio.wait_for(task, watchdog_timeout()) == "new"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_unrelated_notification_cannot_resolve_resume_or_restart_budget(resume_wait):
    task = start_wait(resume_wait)
    try:
        await await_signal(resume_wait.entered, "resume waiting", producers=(task,))
        deadline = resume_wait.budget.expires_at
        await resume_wait.registry.register("foreign", "Other", "other-hash", "test")
        await resume_wait.registry.register_tools_for_session("foreign", [])
        assert not task.done()
        assert resume_wait.budget.expires_at == deadline
        await register_target(resume_wait)
        assert await asyncio.wait_for(task, watchdog_timeout()) == "new"
        assert len(resume_wait.lookups) == 2
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("direct", [False, True])
async def test_resume_wait_cancellation_releases_waiter(resume_wait, direct):
    task = start_wait(resume_wait)
    event = resume_wait.registry.project_change_event("hash", None)
    try:
        await await_signal(resume_wait.entered, "resume waiting", producers=(task,))
        if direct:
            resume_wait.resume.cancel()
            assert await asyncio.wait_for(task, watchdog_timeout(0.5)) is None
        else:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert not event.is_set()
        assert len(resume_wait.lookups) == 1
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_resume_absence_deadline_has_no_extra_probe(resume_wait):
    resume_wait.budget.expires_at = asyncio.get_running_loop().time() + 0.15
    assert await asyncio.wait_for(start_wait(resume_wait), watchdog_timeout(0.5)) is None
    assert len(resume_wait.lookups) == 1


@pytest.mark.asyncio
async def test_resume_registry_replacement_wakes_without_admitting_old_target(
    resume_wait, monkeypatch
):
    task = start_wait(resume_wait)
    try:
        await await_signal(resume_wait.entered, "resume waiting", producers=(task,))
        monkeypatch.setattr(PluginHub, "_registry", PluginRegistry())
        resume_wait.registry.notify_changed()
        await register_target(resume_wait)
        assert await asyncio.wait_for(task, watchdog_timeout()) is None
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_resume_registration_between_lookup_and_wait_is_not_lost(resume_wait, monkeypatch):
    original = resume_wait.registry.get_session_id_by_hash

    async def register_after_miss(*args, **kwargs):
        result = await original(*args, **kwargs)
        if result is None:
            await register_target(resume_wait)
        return result

    monkeypatch.setattr(resume_wait.registry, "get_session_id_by_hash", register_after_miss)
    assert await asyncio.wait_for(start_wait(resume_wait), watchdog_timeout()) == "new"
    assert len(resume_wait.lookups) == 2


@pytest.mark.asyncio
async def test_registered_resume_without_socket_retains_retry_throttle(resume_wait):
    await resume_wait.registry.register("new", "Project", "hash", "test")
    resume_wait.budget.expires_at = asyncio.get_running_loop().time() + 0.25
    assert await asyncio.wait_for(start_wait(resume_wait), watchdog_timeout(0.5)) is None
    assert 1 <= len(resume_wait.lookups) <= 3
