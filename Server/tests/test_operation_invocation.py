"""Whole-operation admission precedes effectful preflight and rechecks authority."""

import asyncio  # noqa: ANYIO_OK -- deterministic held-signal admission tests.
from types import SimpleNamespace

import pytest

from services.operation_policy import OperationMode
from services.registry import operation_invocation as mod
from services.registry.operation_context import scheduler
from services.registry.operation_scheduler import OperationScope
from services.registry.operation_target import OperationTarget
from tests.timing import await_signal


class Context:
    allowed = True

    async def get_state(self, key, default=None):
        return "Owned@deadbeef" if key == "unity_instance" else default


@pytest.fixture
def boundary(monkeypatch):
    async def target(*args, **kwargs):
        return OperationTarget(OperationScope("local", "deadbeef", "g1"), lambda: True, True)

    async def authorize(name, params, *, ctx=None):
        if not ctx.allowed:
            raise PermissionError("denied")

    monkeypatch.setattr(mod, "capture_target", target)
    monkeypatch.setattr(mod, "authorize_remote_operation", authorize)
    monkeypatch.setattr(mod, "trusted_tool", lambda *_: True)


@pytest.mark.asyncio
async def test_exclusive_invocation_fences_next_tools_preflight(boundary):
    entered, release = asyncio.Event(), asyncio.Event()
    effects = []

    async def first(ctx):
        effects.append("first_preflight")
        entered.set()
        await release.wait()

    async def second(ctx):
        effects.append("second_preflight")

    first_call = asyncio.create_task(
        mod.wrap_tool(first, "create_script", "manage_script")(Context())
    )
    await await_signal(entered, "first whole invocation")
    second_call = asyncio.create_task(
        mod.wrap_tool(second, "create_script", "manage_script")(Context())
    )
    for _ in range(30):
        await asyncio.sleep(0)
    assert effects == ["first_preflight"]
    release.set()
    await asyncio.gather(first_call, second_call)
    assert effects == ["first_preflight", "second_preflight"]
    assert scheduler().snapshot()["instances"] == 0


@pytest.mark.asyncio
async def test_unauthorized_writer_never_fences_an_authorized_reader(boundary):
    async def read(ctx):
        return "read"

    async def write(ctx):
        pytest.fail("Unauthorized preflight ran")

    target = OperationTarget(OperationScope("local", "deadbeef", "g1"), lambda: True, True)
    request = mod.admission_for(target, OperationMode.SHARED_READ, expires_at=None)
    async with mod.operation_session(request):
        unauthorized = Context()
        unauthorized.allowed = False
        with pytest.raises(PermissionError):
            await mod.wrap_tool(write, "create_script", "manage_script")(unauthorized)
        assert scheduler().snapshot()["waiting"] == 0
        assert await mod.wrap_tool(read, "get_sha", "manage_script")(Context()) == "read"
    assert scheduler().snapshot()["instances"] == 0


@pytest.mark.asyncio
async def test_authority_revoked_while_queued_stops_before_preflight(boundary):
    ctx = Context()
    effects = []

    async def write(ctx):
        effects.append(True)

    # Hold the scheduler directly without inheriting another public operation.
    target = OperationTarget(OperationScope("local", "deadbeef", "g1"), lambda: True, True)
    request = mod.admission_for(target, OperationMode.EXCLUSIVE, expires_at=None)
    async with scheduler().session(request):
        queued = asyncio.create_task(mod.wrap_tool(write, "create_script", "manage_script")(ctx))
        for _ in range(30):
            await asyncio.sleep(0)
        ctx.allowed = False
    with pytest.raises(PermissionError):
        await queued
    assert effects == [] and scheduler().snapshot()["instances"] == 0


@pytest.mark.asyncio
async def test_read_invocation_cannot_escalate_or_change_target_in_nested_send(boundary):
    target = OperationTarget(OperationScope("local", "deadbeef", "g1"), lambda: True, True)
    async with mod.operation_session(
        mod.admission_for(target, OperationMode.SHARED_READ, expires_at=None)
    ):
        with pytest.raises(mod.OperationPolicyViolation):
            async with mod.operation_session(
                mod.admission_for(target, OperationMode.EXCLUSIVE, expires_at=None)
            ):
                pytest.fail("Unreviewed nested mutation bypassed the read claim")


def test_unknown_registered_callback_cannot_spoof_a_builtin_read_name():
    async def callback(ctx):
        return SimpleNamespace()

    assert mod.trusted_tool(callback, "get_sha") is False


def test_ingress_sized_argument_retention_does_not_inherit_http_command_ceiling():
    value = {"text": "x" * (64 * 1024 * 1024 - 1024)}
    charge = mod.operation_payload_charge(value)
    assert charge is not None and 256 * 1024 * 1024 < charge < 1024 * 1024 * 1024


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,params,allowed",
    [
        ("get_editor_state", {}, True),
        ("ping", {}, True),
        ("get_compile_job", {"compile_id": "owned-exact"}, True),
        ("get_compile_job", {}, False),
        ("cancel_compile_job", {"compile_id": "owned-exact"}, False),
        ("manage_packages", {"action": "list"}, False),
        ("manage_scene", {"action": "save"}, False),
    ],
)
async def test_reload_allows_only_exact_observational_continuations(name, params, allowed):
    old = OperationTarget(OperationScope("local", "owned", "old"), lambda: False, True)
    fresh = OperationTarget(OperationScope("local", "owned", "new"), lambda: True, True)
    # Admit before the native generation changes, then keep its mutation fence.
    admitted = OperationTarget(old.scope, lambda: True, True)
    async with mod.operation_session(
        mod.admission_for(admitted, OperationMode.EXCLUSIVE, expires_at=None)
    ) as owner:
        request = mod.admission_for(
            fresh,
            OperationMode.CONTROL,
            expires_at=None,
            observational_continuation=mod.is_observational_continuation(name, params),
        )
        if allowed:
            async with mod.operation_session(request):
                assert owner.request.scope.generation == "old"
        else:
            with pytest.raises(mod.OperationScopeChanged):
                async with mod.operation_session(request):
                    pytest.fail("Dependent operation crossed the immutable generation fence")
