import asyncio
from time import monotonic
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.verification.service import VerificationService


@pytest.fixture
def service(monkeypatch):
    import services.verification.service as module

    target = SimpleNamespace(
        scope=SimpleNamespace(key=("u", "p"), generation="generation"),
        resume_generation="process",
        is_current=lambda: True,
        authorize_public=None,
        public_available=None,
    )
    monkeypatch.setattr(
        module, "get_unity_instance_from_context", AsyncMock(return_value="Editor@project")
    )
    monkeypatch.setattr(module, "capture_target", AsyncMock(return_value=target))
    native = AsyncMock(
        return_value={
            "success": True,
            "data": {"byte_hash": "missing", "document": {"schema_version": 1, "profiles": {}}},
        }
    )
    monkeypatch.setattr(module, "send_with_unity_instance", native)
    ctx = SimpleNamespace(fastmcp=SimpleNamespace())
    return VerificationService(), ctx, target, native


@pytest.mark.asyncio
async def test_prepare_freezes_profile_and_selection_without_running_stages(service):
    subject, ctx, target, native = service
    response = await subject.execute(
        ctx, "prepare", {"paths": ["Assets/A.cs"], "profile_id": "editmode-check"}
    )
    assert response["success"]
    data = response["data"]
    assert data["state"] == "pending" and data["ready"]
    assert data["profile"]["test_mode"] == "EditMode"
    assert native.await_count == 1
    assert native.call_args.args[2] == "manage_verification"
    target.scope.key = ("other", "p")
    hidden = await subject.execute(ctx, "status", {"preparation_id": data["preparation_id"]})
    assert hidden["code"] == "verification_not_found"


@pytest.mark.asyncio
async def test_repeated_run_uses_one_handle_and_server_restart_does_not_replay(
    service, monkeypatch
):
    subject, ctx, _, native = service
    prepared = await subject.execute(ctx, "prepare", {"paths": ["Assets/A.cs"]})
    identity = prepared["data"]["preparation_id"]
    release = asyncio.Event()

    async def work(self):
        await release.wait()
        return {"state": "incomplete", "native_settled": True, "stages": [], "children": []}

    monkeypatch.setattr("services.verification.runner.VerificationRunner.run", work)
    monkeypatch.setattr(
        "services.verification.service.VerificationIO.authorize_profile", AsyncMock()
    )
    first = await subject.execute(ctx, "run", {"preparation_id": identity})
    again = await subject.execute(ctx, "run", {"preparation_id": identity})
    assert first["data"]["run_id"] == again["data"]["run_id"]
    fresh = await VerificationService().execute(ctx, "run", {"preparation_id": identity})
    assert fresh["code"] == "verification_not_found"
    release.set()
    await asyncio.gather(*subject.tasks.values())


@pytest.mark.asyncio
async def test_direct_run_prepares_selection_and_wait_expiry_keeps_original_job(
    service, monkeypatch
):
    subject, ctx, _, native = service
    release = asyncio.Event()

    async def work(self):
        await release.wait()
        self.report.update(state="passed", native_settled=True)
        return self.report

    monkeypatch.setattr("services.verification.runner.VerificationRunner.run", work)
    monkeypatch.setattr(
        "services.verification.service.VerificationIO.authorize_profile", AsyncMock()
    )
    first = await subject.execute(ctx, "run", {"paths": ["Assets/A.cs"], "wait_seconds": 0.01})
    assert first["success"]
    report = first["data"]
    assert report["selection"] == {"paths": ["Assets/A.cs"]}
    assert report["state"] == "running"
    assert len(subject.tasks) == 1 and not next(iter(subject.tasks.values())).cancelled()
    again = await subject.execute(ctx, "run", {"preparation_id": report["preparation_id"]})
    assert again["data"]["run_id"] == report["run_id"]
    assert native.await_count == 1
    release.set()
    await asyncio.gather(*subject.tasks.values())
    terminal = await subject.execute(ctx, "status", {"run_id": report["run_id"]})
    assert terminal["data"]["state"] == "passed"


@pytest.mark.asyncio
async def test_live_permission_union_is_checked_before_starting_any_background_work(
    service, monkeypatch
):
    subject, ctx, target, native = service
    prepared = await subject.execute(
        ctx, "prepare", {"paths": ["Assets/A.cs"], "profile_id": "playmode-check"}
    )

    async def authorize(name, params):
        if name == "run_tests":
            raise PermissionError("test denied")
        return lambda: None

    target.authorize_public = authorize
    with pytest.raises(PermissionError):
        await subject.execute(ctx, "run", {"preparation_id": prepared["data"]["preparation_id"]})
    assert not subject.tasks
    assert native.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "generation,resume,owner,allowed",
    [
        ("generation", "process", ("u", "p"), True),
        ("after-reload", "process", ("u", "p"), True),
        ("after-restart", "other-process", ("u", "p"), False),
        ("generation", "process", ("other-user", "p"), False),
        ("generation", "process", ("u", "other-project"), False),
        ("after-reload", None, ("u", "p"), False),
    ],
)
async def test_edit_keeps_original_process_and_principal_across_deferred_start(
    service, monkeypatch, generation, resume, owner, allowed
):
    from services.verification import service as module

    subject, ctx, original, _ = service
    fresh = SimpleNamespace(
        scope=SimpleNamespace(key=owner, generation=generation),
        resume_generation=resume,
        is_current=lambda: True,
        authorize_public=None,
        public_available=None,
    )
    monkeypatch.setattr(module, "capture_target", AsyncMock(return_value=fresh))
    start = AsyncMock(return_value="a" * 32)
    monkeypatch.setattr(subject, "start_runner", start)
    monkeypatch.setattr(subject, "read", lambda *_: {"success": True, "data": {"run_id": "a" * 32}})
    result = await subject.start_edit(
        ctx,
        "Editor@project",
        paths=["Assets/A.cs"],
        expected_sources={"Assets/A.cs": {"bytes_sha256": "b" * 64}},
        original_target=original,
    )
    assert result["success"] is allowed
    assert start.await_count == int(allowed)
    if not allowed:
        assert result["code"] == "verification_editor_process_changed"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["analysis", "tests"])
async def test_late_observation_of_a_different_job_never_settles_retained_ownership(kind):
    subject = VerificationService()
    child = {"kind": kind, "job_id": "a" * 32, "settled": False}
    response = {
        "success": True,
        "data": {
            "job_id": "b" * 32,
            "state": "completed",
            "terminal": True,
            "status": "completed",
            "native_cleanup_pending": False,
        },
    }
    runner = SimpleNamespace(
        report={"children": [child]},
        io=SimpleNamespace(
            quality=AsyncMock(return_value=response), native=AsyncMock(return_value=response)
        ),
        publish=lambda: None,
    )
    await subject.observe_settlement(runner, monotonic() + 5, cancel=False)
    assert child["settled"] is False


@pytest.mark.asyncio
async def test_compile_owner_requires_the_matching_pipeline_finish_witness():
    child = {"kind": "compile", "job_id": "a" * 32, "settled": False}
    response = {
        "success": True,
        "data": {"compile_id": "a" * 32, "state": "finished"},
        "native_coordination": {
            "scope": "owned_request_pipeline_events",
            "phase": "pending",
            "reason": "native_job_active",
            "retryable": True,
            "recovery_action": "wait_for_pipeline_finish",
            "completion_requires": "matching_pipeline_finish_and_editor_quiescence",
        },
    }
    runner = SimpleNamespace(
        report={"children": [child]},
        io=SimpleNamespace(native=AsyncMock(return_value=response)),
        publish=lambda: None,
    )
    subject = VerificationService()
    await subject.observe_settlement(runner, monotonic() + 5, cancel=False)
    assert not child["settled"]
    response["native_coordination"].update(
        phase="finished", reason=None, retryable=False, recovery_action=None
    )
    await subject.observe_settlement(runner, monotonic() + 5, cancel=False)
    assert child["settled"]
