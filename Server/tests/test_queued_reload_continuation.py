"""A reload suspends an uninvoked public callback without replaying started work."""

import asyncio

import pytest

from services.operation_policy import OperationMode
from services.registry import operation_invocation as mod
from services.registry.operation_context import scheduler
from services.registry.operation_scheduler import OperationScope
from services.registry.operation_target import OperationTarget

PROCESS = "a" * 32


class Context:
    allowed = True

    async def get_state(self, key, default=None):
        return "Owned@deadbeef" if key == "unity_instance" else default


@pytest.fixture
def boundary(monkeypatch):
    async def authorize(name, params, *, ctx=None):
        if not ctx.allowed:
            raise PermissionError("revoked while waiting")

    monkeypatch.setattr(mod, "authorize_remote_operation", authorize)
    monkeypatch.setattr(mod, "trusted_tool", lambda *_: True)


def target(
    generation,
    current=lambda: True,
    *,
    process=PROCESS,
    principal="local",
    project="deadbeef",
    **kwargs,
):
    return OperationTarget(
        OperationScope(principal, project, generation),
        current,
        True,
        resume_generation=process,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_queued_mutation_waits_through_repeated_reload_and_runs_once(boundary, monkeypatch):
    generation = ["g1"]
    original = target("g1", lambda: generation[0] == "g1")
    waiting, reconnected = asyncio.Event(), asyncio.Event()
    effects, captures = [], []
    deadline = asyncio.get_running_loop().time() + 5

    async def capture(*args, **kwargs):
        captures.append(generation[0])
        if len(captures) == 1:
            return original
        if not reconnected.is_set():
            waiting.set()
            return None
        # The first recovered generation reloads before admission as well.
        if generation[0] == "g2":
            generation[0] = "g3"
            return target("g2", lambda: False)
        return target("g3")

    async def write(ctx, value):
        effects.append((value, mod.active_operation().request.expires_at))
        return {"success": True}

    monkeypatch.setattr(mod, "capture_target", capture)
    monkeypatch.setattr(mod, "_deadline", lambda *a, **k: deadline)
    async with scheduler().session(
        mod.admission_for(original, OperationMode.EXCLUSIVE, expires_at=deadline)
    ):
        task = asyncio.create_task(
            mod.wrap_tool(write, "create_script", "manage_script")(Context(), {"saved": 7})
        )
        while scheduler().snapshot()["waiting"] != 1:
            await asyncio.sleep(0)
        generation[0] = "offline"
    await asyncio.wait_for(waiting.wait(), 2)
    assert not task.done() and effects == []
    generation[0] = "g2"
    reconnected.set()
    assert (await asyncio.wait_for(task, 2))["success"]
    assert effects == [({"saved": 7}, deadline)]
    assert "g3" in captures
    assert scheduler().snapshot()["instances"] == 0


@pytest.mark.asyncio
async def test_explicit_cancel_while_reload_waiting_never_resurrects(boundary, monkeypatch):
    waiting = asyncio.Event()
    captures, effects = [], []
    original = target("g1", lambda: False)

    async def capture(*args, **kwargs):
        captures.append(True)
        if len(captures) == 1:
            return original
        waiting.set()
        return None

    async def write(ctx):
        effects.append(True)

    monkeypatch.setattr(mod, "capture_target", capture)
    task = asyncio.create_task(mod.wrap_tool(write, "create_script", "manage_script")(Context()))
    await asyncio.wait_for(waiting.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    count = len(captures)
    await asyncio.sleep(0)
    assert effects == [] and len(captures) == count
    assert scheduler().snapshot()["instances"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["process", "principal", "project", "capability"])
async def test_queued_resume_rejects_changed_ownership(boundary, monkeypatch, changed):
    old = target("g1", lambda: False)
    fresh = target(
        "g2",
        process=None if changed == "capability" else "b" * 32 if changed == "process" else PROCESS,
        principal="remote:other" if changed == "principal" else "local",
        project="other" if changed == "project" else "deadbeef",
    )
    captures, effects = [], []

    async def capture(*args, **kwargs):
        captures.append(True)
        return old if len(captures) == 1 else fresh

    async def write(ctx):
        effects.append(True)

    monkeypatch.setattr(mod, "capture_target", capture)
    result = await mod.wrap_tool(write, "create_script", "manage_script")(Context())
    assert result["data"]["dispatch"] == "not_dispatched"
    assert result["error"] == "operation_target_changed"
    assert effects == []


@pytest.mark.asyncio
async def test_original_deadline_expires_while_editor_is_reloading(boundary, monkeypatch):
    captures, effects = [], []
    original = target("g1", lambda: False)
    deadline = asyncio.get_running_loop().time() + 0.05

    async def capture(*args, **kwargs):
        captures.append(True)
        return original if len(captures) == 1 else None

    async def write(ctx):
        effects.append(True)

    monkeypatch.setattr(mod, "capture_target", capture)
    monkeypatch.setattr(mod, "_deadline", lambda *a, **k: deadline)
    with pytest.raises(TimeoutError):
        await mod.wrap_tool(write, "create_script", "manage_script")(Context())
    assert effects == [] and len(captures) >= 2


@pytest.mark.asyncio
@pytest.mark.parametrize("denial", ["authority", "catalog"])
async def test_reconnected_admission_rechecks_authority_and_catalog(boundary, monkeypatch, denial):
    ctx, captures, effects = Context(), [], []
    original = target("g1", lambda: False)

    async def available(*args):
        return False

    fresh = target("g2", public_available=available if denial == "catalog" else None)

    async def capture(*args, **kwargs):
        captures.append(True)
        if len(captures) == 1:
            return original
        if denial == "authority":
            ctx.allowed = False
        return fresh

    async def write(ctx):
        effects.append(True)

    monkeypatch.setattr(mod, "capture_target", capture)
    call = mod.wrap_tool(write, "create_script", "manage_script")(ctx)
    if denial == "authority":
        with pytest.raises(PermissionError, match="revoked"):
            await call
    else:
        assert (await call)["error"] == "operation_tool_unavailable"
    assert effects == [] and len(captures) == 2


@pytest.mark.asyncio
async def test_started_wrapper_is_never_replayed_even_with_resume_capability(boundary, monkeypatch):
    effects = []

    async def capture(*args, **kwargs):
        return target("g1")

    async def write(ctx):
        effects.append(True)
        raise mod.OperationScopeChanged()

    monkeypatch.setattr(mod, "capture_target", capture)
    result = await mod.wrap_tool(write, "create_script", "manage_script")(Context())
    assert result["data"]["dispatch"] == "unknown"
    assert effects == [True]


@pytest.mark.asyncio
async def test_reload_wait_retains_writer_precedence(boundary, monkeypatch):
    waiting, reconnect = asyncio.Event(), asyncio.Event()
    effects = []
    first_captures = 0

    async def capture(*args, **kwargs):
        nonlocal first_captures
        if asyncio.current_task().get_name() == "first-queued":
            first_captures += 1
            if first_captures == 1:
                return target("g1", lambda: False)
            if not reconnect.is_set():
                waiting.set()
                return None
        return target("g2")

    async def write(ctx, label):
        effects.append(label)
        return {"success": True}

    monkeypatch.setattr(mod, "capture_target", capture)
    wrapped = mod.wrap_tool(write, "create_script", "manage_script")
    first = asyncio.create_task(wrapped(Context(), "first"), name="first-queued")
    second = None
    try:
        await asyncio.wait_for(waiting.wait(), 2)
        second = asyncio.create_task(wrapped(Context(), "second"))
        for _ in range(30):
            await asyncio.sleep(0)
        assert effects == []
    finally:
        reconnect.set()
        await asyncio.gather(first, *([second] if second else []))
    assert effects == ["first", "second"]


@pytest.mark.asyncio
async def test_tool_removed_during_reload_finishes_without_busy_rebind_loop(boundary, monkeypatch):
    class Catalog:
        enabled = True

        async def get_tool(self, name):
            await asyncio.sleep(0)
            return object() if self.enabled else None

    catalog = Catalog()
    ctx = Context()
    ctx.fastmcp = catalog
    captures, effects = [], []

    async def capture(*args, **kwargs):
        captures.append(True)
        if len(captures) == 1:
            return target("g1", lambda: False)
        catalog.enabled = False
        return target("g2")

    async def write(ctx):
        effects.append(True)

    monkeypatch.setattr(mod, "capture_target", capture)
    result = await asyncio.wait_for(mod.wrap_tool(write, "create_script", "manage_script")(ctx), 2)
    assert result["success"] is False
    assert result["error"] == "operation_tool_unavailable"
    assert result["data"]["dispatch"] == "not_dispatched"
    assert len(captures) == 2 and effects == []
    assert scheduler().snapshot()["instances"] == 0


@pytest.mark.asyncio
async def test_original_deadline_also_bounds_pending_catalog_validation(boundary, monkeypatch):
    release = asyncio.Event()

    class Catalog:
        calls = 0

        async def get_tool(self, name):
            self.calls += 1
            if self.calls > 1:
                await release.wait()
            return object()

    ctx = Context()
    ctx.fastmcp = Catalog()
    effects = []
    deadline = asyncio.get_running_loop().time() + 0.05

    async def capture(*args, **kwargs):
        return target("g1")

    async def write(ctx):
        effects.append(True)

    monkeypatch.setattr(mod, "capture_target", capture)
    monkeypatch.setattr(mod, "_deadline", lambda *a, **k: deadline)
    task = asyncio.create_task(mod.wrap_tool(write, "create_script", "manage_script")(ctx))
    try:
        done, _ = await asyncio.wait({task}, timeout=2)
        assert task in done, "Catalog validation outlived the original admission deadline"
        with pytest.raises(TimeoutError):
            await task
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert effects == []
    assert scheduler().snapshot()["instances"] == 0
