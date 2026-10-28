"""Whole-operation admission precedes effectful preflight and rechecks authority."""

import asyncio  # noqa: ANYIO_OK -- deterministic held-signal admission tests.
from types import SimpleNamespace

import pytest
from pydantic import MISSING, Field
from typing_extensions import sentinel

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
async def test_completed_result_wrappers_bypass_held_writer_while_console_waits(boundary):
    entered, release = asyncio.Event(), asyncio.Event()
    effects = []

    async def held_writer(ctx):
        entered.set()
        await release.wait()

    async def waiting_writer(ctx):
        effects.append("waiting_writer")

    async def view(ctx, job_id):
        effects.append(("view", job_id))

    async def export(ctx, job_id, format="json"):
        effects.append(("export", job_id, format))

    async def console(ctx, action="get"):
        effects.append("console")

    held = asyncio.create_task(
        mod.wrap_tool(held_writer, "create_script", "manage_script")(Context())
    )
    await await_signal(entered, "held production writer")
    queued = asyncio.create_task(
        mod.wrap_tool(waiting_writer, "create_script", "manage_script")(Context())
    )
    blocked_console = asyncio.create_task(
        mod.wrap_tool(console, "read_console", "read_console")(Context())
    )
    try:
        await asyncio.gather(
            mod.wrap_tool(view, "view_test_results", "get_test_job")(Context(), job_id="completed"),
            mod.wrap_tool(export, "export_test_job", "export_test_job")(
                Context(), job_id="completed"
            ),
        )
        assert ("view", "completed") in effects
        assert ("export", "completed", "json") in effects
        assert "waiting_writer" not in effects
        assert "console" not in effects
    finally:
        release.set()
        await asyncio.gather(held, queued, blocked_console)
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
        ("get_test_job", {"job_id": "owned-exact", "includeDetails": True}, True),
        (
            "get_test_job",
            {
                "job_id": "owned-exact",
                "includeFailedTests": True,
                "page_size": 100,
                "cursor": "page-2",
            },
            True,
        ),
        ("get_test_job", {"job_id": "owned-exact", "action": "cancel"}, False),
        ("get_test_job", {"job_id": "owned-exact", "includeDetails": "true"}, False),
        ("get_test_job", {"job_id": "owned-exact", "page_size": True}, False),
        ("get_test_job", {"job_id": "owned-exact", "page_size": 101}, False),
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


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["generation", "project", "principal"])
async def test_job_read_rebinds_only_same_project_before_callback(boundary, monkeypatch, changed):
    old = OperationTarget(OperationScope("local", "deadbeef", "old"), lambda: False, True)
    fresh = OperationTarget(
        OperationScope(
            "other" if changed == "principal" else "local",
            "different" if changed == "project" else "deadbeef",
            "new",
        ),
        lambda: True,
        True,
    )
    captures, effects = [], []

    async def capture(instance, **_kwargs):
        captures.append(instance)
        return old if len(captures) == 1 else fresh

    async def get_test_job(
        ctx, job_id, include_details=False, wait_timeout=None, page_size=None, cursor=None
    ):
        effects.append((job_id, include_details, page_size, cursor))
        return {"success": True, "data": {"passed": 272}}

    monkeypatch.setattr(mod, "capture_target", capture)
    result = await mod.wrap_tool(get_test_job, "get_test_job", "get_test_job")(
        Context(), "kept-job", include_details=True, page_size=100, cursor="page-2"
    )
    if changed == "generation":
        assert result["success"] is True
        assert effects == [("kept-job", True, 100, "page-2")]
        assert len(captures) == 2
    else:
        assert result["error"] == "operation_target_changed"
        assert effects == []
    assert scheduler().snapshot()["active"] == 0


_OMITTED_MARKER = object()


@pytest.mark.asyncio
async def test_observation_rebind_rechecks_new_plugin_authority(boundary, monkeypatch):
    old = OperationTarget(OperationScope("local", "deadbeef", "old"), lambda: False, True)
    effects, captures = [], []

    async def denied(name, params):
        raise PermissionError("new plugin authority denied")

    fresh = OperationTarget(
        OperationScope("local", "deadbeef", "new"), lambda: True, True, authorize_public=denied
    )

    async def capture(*args, **kwargs):
        captures.append(True)
        return old if len(captures) == 1 else fresh

    async def get_test_job(ctx, job_id):
        effects.append(job_id)

    monkeypatch.setattr(mod, "capture_target", capture)
    with pytest.raises(PermissionError, match="new plugin authority denied"):
        await mod.wrap_tool(get_test_job, "get_test_job", "get_test_job")(Context(), "saved-job")
    assert len(captures) == 2 and effects == []
    assert scheduler().snapshot()["active"] == 0


_MARKER_DEFAULT = Field(default_factory=lambda: _OMITTED_MARKER)
_UNRELATED_SENTINEL = sentinel("UNRELATED")


@pytest.mark.parametrize(
    "default,value,omitted",
    [
        pytest.param(MISSING, MISSING, True, id="pydantic-missing-default"),
        pytest.param(None, MISSING, False, id="missing-is-not-the-default"),
        pytest.param(_OMITTED_MARKER, _OMITTED_MARKER, True, id="object-default"),
        pytest.param(_MARKER_DEFAULT, _MARKER_DEFAULT, True, id="field-info-default"),
        pytest.param(_MARKER_DEFAULT, _OMITTED_MARKER, True, id="factory-marker"),
        pytest.param(_MARKER_DEFAULT, object(), False, id="unrelated-object"),
        pytest.param(_UNRELATED_SENTINEL, _UNRELATED_SENTINEL, False, id="unknown-sentinel"),
        pytest.param(None, None, False, id="null-default"),
        pytest.param(False, False, False, id="false-default"),
        pytest.param(0, 0, False, id="zero-default"),
        pytest.param("active", "active", False, id="string-default"),
    ],
)
def test_omitted_defaults_recognize_only_owned_internal_markers(default, value, omitted):
    assert mod._omitted_default(default, value) is omitted


def test_omitted_default_does_not_evaluate_a_factory():
    def forbidden_factory():
        pytest.fail("Omission inspection must not invoke default factories")

    assert mod._omitted_default(Field(default_factory=forbidden_factory), object()) is False
