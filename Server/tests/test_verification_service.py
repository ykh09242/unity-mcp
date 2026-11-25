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


@pytest.fixture
def analysis_service(service, monkeypatch):
    from services.script_quality import service as quality_module
    from services.verification.service import VerificationIO

    subject, ctx, target, _ = service
    quality = ctx.fastmcp._script_quality_service = quality_module.ScriptQualityService()
    monkeypatch.setattr(
        quality_module, "get_unity_instance_from_context", AsyncMock(return_value="Editor@project")
    )
    monkeypatch.setattr(quality_module, "capture_target", AsyncMock(return_value=target))
    send = AsyncMock()
    monkeypatch.setattr(quality_module, "send_with_unity_instance", send)
    monkeypatch.setattr(
        VerificationIO, "freshness", AsyncMock(return_value={"status": "observed_current"})
    )
    return subject, ctx, quality, send


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["run", "prepare"])
async def test_bound_stdio_queue_refusal_releases_verification_capacity(
    analysis_service, monkeypatch, action
):
    from tests.integration.test_command_resume_stdio import configure, resume_peer
    from services.script_quality import service as quality_module
    from transport.unity_transport import send_with_unity_instance

    subject, ctx, quality, send = analysis_service
    monkeypatch.setattr(quality_module, "send_with_unity_instance", send_with_unity_instance)
    monkeypatch.setattr(
        quality_module, "get_unity_instance_from_context", AsyncMock(return_value="Owned@deadbeef")
    )
    options = {"paths": ["Assets/A.cs"]}
    options.update({"wait_seconds": 2} if action == "run" else {"capture_baseline": True})
    for _ in range(2):
        with resume_peer("queue_capacity", reloads=0) as (port, commands, _, _):
            connection = configure(monkeypatch, port)
            try:
                response = await subject.execute(ctx, action, options)
                assert connection.send_command("ping", {}) == {"message": "pong"}
                assert [c["type"] for c in commands] == ["manage_script_quality", "ping"]
            finally:
                connection.disconnect()
        if action == "run":
            (child,) = response["data"]["children"]
            assert response["data"]["native_settled"] and child["dispatch"] == "refused"
        else:
            assert response["success"] is False and not subject.preparations
        assert not quality.results.active

    monkeypatch.setattr(quality_module, "send_with_unity_instance", send)
    send.return_value = {"success": True, "data": {"state": "failed", "terminal": True}}
    admitted = await subject.execute(ctx, action, options)
    assert admitted["success"]
    assert send.call_args_list[0].args[3]["action"] == (
        "analyze" if action == "run" else "capture_baseline"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["run", "prepare"])
@pytest.mark.parametrize("revocation", ["target", "permission"])
async def test_refused_analysis_releases_original_owner_when_access_changes(
    analysis_service, service, action, revocation
):
    from transport.native_arbitration import VerifiedQueueRefusal

    subject, ctx, quality, send = analysis_service
    _, _, target, _ = service
    revoked = False
    target.is_current = lambda: not (revoked and revocation == "target")

    async def authorize(_command, _params):
        def check():
            if revoked and revocation == "permission":
                raise PermissionError("permission revoked")

        return check

    target.authorize_public = authorize

    async def refuse(*_args, **kwargs):
        nonlocal revoked
        assert kwargs["_preserve_queue_refusal"]
        revoked = True
        return VerifiedQueueRefusal(success=False, error="private queue receipt")

    send.side_effect = refuse
    for _ in range(3):
        revoked = False
        options = {"paths": ["Assets/A.cs"]}
        if action == "prepare":
            error = ConnectionError if revocation == "target" else PermissionError
            with pytest.raises(error, match="analysis_target_changed|permission revoked"):
                await subject.execute(ctx, action, {**options, "capture_baseline": True})
            assert not subject.preparations
        else:
            started = await subject.execute(ctx, action, {**options, "wait_seconds": 0})
            run_id = started["data"]["run_id"]
            await subject.tasks[run_id]
            report = subject.store.read(run_id, target.scope.key)
            (child,) = report["children"]
            assert child["settled"] and child["dispatch"] == "refused" and report["native_settled"]
            assert "private queue receipt" not in str(report)
            if revocation == "target":
                hidden = await subject.execute(ctx, "status", {"run_id": run_id})
                assert hidden["code"] == "verification_target_unavailable"
            else:
                with pytest.raises(PermissionError, match="permission revoked"):
                    await subject.execute(ctx, "status", {"run_id": run_id})
        assert not quality.results.active
    assert send.await_count == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["tests", "import", "compile"])
@pytest.mark.parametrize("retire", [False, True])
async def test_bound_queue_refusal_settles_only_the_current_native_child(
    service, monkeypatch, kind, retire
):
    from models.verification import VerificationProfile
    from services import source_freshness
    from services.verification.runner import VerificationRunner
    from services.verification.service import VerificationIO, BoundContext
    from tests.integration.test_command_resume_stdio import configure, resume_peer
    from transport.unity_transport import send_with_unity_instance

    _, ctx, target, _ = service

    async def route(send_fn, instance, command, params, **kwargs):
        if command == "get_compile_job":
            return {
                "success": True,
                "source_freshness": {
                    "schema": "unity-mcp/source_freshness@1",
                    "status": "changed",
                    "project_id": "p",
                    "session_id": "s",
                    "coverage": {"complete": True},
                },
            }
        if kind == "compile" and command == "refresh_unity" and params["compile"] == "none":
            return {"success": True}
        receipt = await send_with_unity_instance(send_fn, instance, command, params, **kwargs)
        if retire:
            target.is_current = lambda: False
        return receipt

    monkeypatch.setattr(source_freshness.unity_transport, "send_with_unity_instance", route)
    monkeypatch.setattr(source_freshness, "remote_operation_allowed", AsyncMock(return_value=True))
    monkeypatch.setattr("services.verification.service.send_with_unity_instance", route)
    with resume_peer("queue_capacity", reloads=0) as (port, commands, _, _):
        connection = configure(monkeypatch, port)
        io = VerificationIO(BoundContext(ctx.fastmcp, connection.instance_id, None), target)
        runner = VerificationRunner(io, VerificationProfile(test_mode="EditMode"), {})
        try:
            if kind == "tests":
                await runner.run_test_job("a" * 64, SimpleNamespace(window_id=None))
                if retire:
                    with pytest.raises(ConnectionError, match="verification_target_unavailable"):
                        await io.check()
            elif retire:
                with pytest.raises(ConnectionError, match="verification_target_unavailable"):
                    await io.freshness("if_needed", monotonic() + 5)
            else:
                evidence = await io.freshness("if_needed", monotonic() + 5)
                assert evidence["reason"] == "refresh_not_dispatched"
            assert connection.send_command("ping", {}) == {"message": "pong"}
        finally:
            connection.disconnect()
    *previous, child = runner.report["children"]
    assert child["kind"] == kind and child["settled"] and child["dispatch"] == "refused"
    if kind == "compile":
        assert len(previous) == 1 and previous[0]["kind"] == "import" and not previous[0]["settled"]


@pytest.mark.asyncio
async def test_queue_shaped_business_error_does_not_settle_native_work(analysis_service):
    subject, ctx, quality, send = analysis_service
    send.return_value = {
        "success": False,
        "error": "Command queue capacity reached",
        "code": "command_queue_capacity",
        "data": {
            "reason": "command_queue_capacity",
            "execution": "not_started",
            "dispatch": "not_dispatched",
        },
    }
    response = await subject.execute(ctx, "run", {"paths": ["Assets/A.cs"], "wait_seconds": 2})
    (child,) = response["data"]["children"]
    assert not child["settled"] and child["job_id"] in quality.results.active


@pytest.mark.asyncio
async def test_refused_analysis_releases_slots_but_lost_acknowledgment_keeps_job(analysis_service):
    subject, ctx, quality, send = analysis_service
    options = {"paths": ["Assets/A.cs"], "wait_seconds": 2}
    quality.results.scope_bytes = 0
    for _ in range(2):
        response = await subject.execute(ctx, "run", options)
        (child,) = response["data"]["children"]
        assert child == {"kind": "analysis", "job_id": None, "settled": True, "dispatch": "refused"}
        assert response["data"]["native_settled"]
    assert not quality.jobs and not quality.results.active
    send.assert_not_awaited()

    quality.results.scope_bytes = quality.results.reservation_bytes
    send.side_effect = ConnectionError("acknowledgment lost")
    response = await subject.execute(ctx, "run", options)
    (child,) = response["data"]["children"]
    assert not response["data"]["native_settled"] and not child["settled"]
    assert child["job_id"] in quality.results.active
    send.assert_awaited_once()
    send.side_effect = None
    send.return_value = {
        "success": True,
        "data": {"job_id": child["job_id"], "terminal": True, "state": "failed"},
    }
    polled = await subject.execute(ctx, "status", {"run_id": response["data"]["run_id"]})
    assert polled["data"]["native_settled"]
    assert [call.args[3]["action"] for call in send.await_args_list] == ["analyze", "status"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "refusal",
    ["verification_access", "quality_target", "rebound_owner", "rebound_generation", "final_fence"],
)
async def test_late_analysis_refusal_settles_before_native_send(
    analysis_service, service, monkeypatch, refusal
):
    subject, ctx, quality, send = analysis_service
    _, _, target, _ = service

    async def authorize(name, params):
        if name == "manage_script_quality" and params["action"] == "analyze":
            if refusal == "verification_access" and "options" in params:
                raise PermissionError("analysis permission revoked")
            if refusal == "final_fence" and "job_id" in params:
                raise PermissionError("analysis permission revoked before send")
        return lambda: None

    target.authorize_public = authorize
    if refusal == "quality_target":
        monkeypatch.setattr(
            "services.script_quality.service.capture_target",
            AsyncMock(side_effect=ConnectionError("target retired")),
        )
    elif refusal in {"rebound_owner", "rebound_generation"}:
        replacement = SimpleNamespace(**vars(target))
        replacement.scope = SimpleNamespace(
            key=("other", "p") if refusal == "rebound_owner" else target.scope.key,
            generation="changed" if refusal == "rebound_generation" else target.scope.generation,
        )
        monkeypatch.setattr(
            "services.script_quality.service.capture_target", AsyncMock(return_value=replacement)
        )
    for _ in range(3):
        response = await subject.execute(ctx, "run", {"paths": ["Assets/A.cs"], "wait_seconds": 2})
        (child,) = response["data"]["children"]
        assert child == {"kind": "analysis", "job_id": None, "settled": True, "dispatch": "refused"}
        assert response["data"]["native_settled"] and response["data"]["state"] == "incomplete"
    send.assert_not_awaited()
    assert not quality.jobs and not quality.results.active


@pytest.mark.asyncio
async def test_interrupted_analysis_send_retains_original_job_for_settlement(analysis_service):
    subject, ctx, quality, send = analysis_service
    sent = asyncio.Event()

    async def await_ack(*args, **kwargs):
        sent.set()
        await asyncio.Event().wait()

    send.side_effect = await_ack
    started = await subject.execute(ctx, "run", {"paths": ["Assets/A.cs"]})
    run_id = started["data"]["run_id"]
    await asyncio.wait_for(sent.wait(), 2)
    subject.tasks[run_id].cancel()
    await subject.tasks[run_id]
    (job_id,) = quality.results.active
    (child,) = subject.runners[run_id].report["children"]
    assert not child["settled"] and child["job_id"] == job_id
    send.side_effect = None
    send.return_value = {
        "success": True,
        "data": {"job_id": job_id, "terminal": True, "state": "failed"},
    }
    polled = await subject.execute(ctx, "status", {"run_id": run_id})
    assert polled["data"]["native_settled"]
    assert [call.args[3]["action"] for call in send.await_args_list] == ["analyze", "status"]


@pytest.mark.asyncio
async def test_abandoned_baselines_release_capacity_and_preserve_live_jobs(
    analysis_service, service, monkeypatch
):
    from .test_script_quality_service import native_completion

    subject, ctx, quality, send = analysis_service
    _, _, target, _ = service
    quality.results.reservation_bytes = 16384
    now = monotonic()
    monkeypatch.setattr("services.verification.service.monotonic", lambda: now)
    quality.results.clock = lambda: now
    states = {}

    async def native(*args, **kwargs):
        request = args[3]
        identity = request["job_id"]
        if request["action"] == "capture_baseline":
            states[identity] = "running"
        if states[identity] == "completed":
            return native_completion(identity)
        return {
            "success": True,
            "data": {
                "job_id": identity,
                "state": states[identity],
                "terminal": states[identity] == "failed",
            },
        }

    send.side_effect = native
    options = {"paths": ["Assets/A.cs"], "capture_baseline": True}
    abandoned = [(await subject.execute(ctx, "prepare", options))["data"] for _ in range(16)]
    assert (await subject.execute(ctx, "prepare", options))[
        "code"
    ] == "verification_preparation_capacity"
    first, *rest = abandoned
    for record in rest:
        states[record["baseline_id"]] = "failed"
    retained = rest[0]["baseline_id"]
    states[retained] = "completed"
    assert (await quality.execute(ctx, "status", {"job_id": retained}))["data"][
        "baseline_id"
    ] == retained
    now += 1801
    assert (await quality.execute(ctx, "status", {"job_id": retained}))[
        "code"
    ] == "analysis_expired_or_evicted"
    send.reset_mock()

    # A different principal or Editor process cannot poll the abandoned captures.
    target.scope.key = ("other", "p")
    assert (await subject.execute(ctx, "prepare", {"paths": ["Assets/B.cs"]}))["success"]
    target.scope.key = ("u", "p")
    target.resume_generation = "other-editor"
    await subject.execute(ctx, "prepare", {"paths": ["Assets/B.cs"]})
    send.assert_not_awaited()
    target.resume_generation = "process"

    admitted = await subject.execute(ctx, "prepare", options)
    assert admitted["success"], admitted
    assert not {r["preparation_id"] for r in rest} & subject.preparations.keys()
    assert first["preparation_id"] in subject.preparations
    pending = await subject.execute(ctx, "status", {"preparation_id": first["preparation_id"]})
    assert pending["success"] and not pending["data"]["ready"]
    states[first["baseline_id"]] = "completed"
    complete = await subject.execute(ctx, "status", {"preparation_id": first["preparation_id"]})
    assert complete["data"]["ready"]


@pytest.mark.asyncio
async def test_disconnected_baseline_request_is_reconciled_after_its_child_finishes(
    analysis_service, monkeypatch
):
    subject, ctx, quality, send = analysis_service
    sent = asyncio.Event()

    async def await_ack(*args, **kwargs):
        sent.set()
        await asyncio.Event().wait()

    send.side_effect = await_ack
    request = asyncio.create_task(
        subject.execute(ctx, "prepare", {"paths": ["Assets/A.cs"], "capture_baseline": True})
    )
    await asyncio.wait_for(sent.wait(), 2)
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    (job_id,) = quality.results.active
    (preparation,) = subject.preparations.values()
    assert preparation["baseline_id"] == job_id

    monkeypatch.setattr(
        "services.verification.service.monotonic", lambda: preparation["expires"] + 1
    )
    send.side_effect = None
    send.return_value = {
        "success": True,
        "data": {"job_id": job_id, "terminal": True, "state": "failed"},
    }
    assert (await subject.execute(ctx, "prepare", {"paths": ["Assets/B.cs"]}))["success"]
    assert preparation["preparation_id"] not in subject.preparations
    assert job_id not in quality.results.active
    assert [call.args[3]["action"] for call in send.await_args_list] == [
        "capture_baseline",
        "status",
    ]


@pytest.mark.asyncio
async def test_definitively_refused_baseline_releases_preparation(analysis_service):
    subject, ctx, quality, send = analysis_service
    send.return_value = {
        "success": False,
        "error": "script_quality_unavailable",
        "data": {"reason": "analysis_consent_required"},
    }
    response = await subject.execute(
        ctx, "prepare", {"paths": ["Assets/A.cs"], "capture_baseline": True}
    )
    assert response["success"] is False and response["settled"]
    assert not subject.preparations and not quality.results.active


@pytest.mark.asyncio
async def test_pending_baseline_reserves_capacity_while_native_capture_is_awaited(analysis_service):
    subject, ctx, _, send = analysis_service
    for _ in range(15):
        assert (await subject.execute(ctx, "prepare", {"paths": ["Assets/A.cs"]}))["success"]
    entered = asyncio.Event()
    release = asyncio.Event()

    async def native(*args, **kwargs):
        entered.set()
        await release.wait()
        return {"success": True, "data": {"job_id": args[3]["job_id"], "state": "queued"}}

    send.side_effect = native
    capture = asyncio.create_task(
        subject.execute(ctx, "prepare", {"paths": ["Assets/A.cs"], "capture_baseline": True})
    )
    try:
        await asyncio.wait_for(entered.wait(), 2)
        competing = await subject.execute(ctx, "prepare", {"paths": ["Assets/B.cs"]})
        assert competing.get("code") == "verification_preparation_capacity"
    finally:
        release.set()
        assert (await capture)["success"]


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
    assert [call.args[2] for call in native.await_args_list] == [
        "manage_verification",
        "get_project_info",
    ]
    target.scope.key = ("other", "p")
    hidden = await subject.execute(ctx, "status", {"preparation_id": data["preparation_id"]})
    assert hidden["code"] == "verification_not_found"


@pytest.mark.asyncio
async def test_report_identifies_actual_installed_components_and_target(service, monkeypatch):
    from core.server_build import ServerBuild

    subject, ctx, target, native = service
    monkeypatch.setattr(
        "services.verification.service.RUNNING_SERVER", ServerBuild("1.2", None, "server-process")
    )
    native.return_value["data"]["unityVersion"] = "2022.3.62f3"
    native.return_value["data"]["mcpPackage"] = {
        "name": "com.ykh09242.unity-mcp",
        "version": "9.0.0",
        "effectiveServerCommit": "b" * 40,
    }
    prepared = await subject.execute(ctx, "prepare", {"paths": ["Assets/A.cs"]})
    identity = prepared["data"]["installation"]
    assert identity["server"] == {
        "version": "1.2",
        "source_commit": None,
        "server_id": "server-process",
    }
    assert identity["project_scope"] == "p"
    assert identity["editor_generation"] == "process"
    assert identity["package"] == native.return_value["data"]["mcpPackage"]
    assert identity["unity_version"] == "2022.3.62f3"

    async def work(self):
        self.report.update(state="passed", source_token="starting-source")
        return self.report

    monkeypatch.setattr("services.verification.runner.VerificationRunner.run", work)
    report = await subject.execute(
        ctx, "run", {"preparation_id": prepared["data"]["preparation_id"], "wait_seconds": 1}
    )
    assert report["data"]["installation"] == identity
    assert report["data"]["source_token"] == "starting-source"
    target.scope.key = ("u", "second-project")
    refused = await subject.execute(ctx, "report", {"run_id": report["data"]["run_id"]})
    assert refused["code"] == "verification_not_found"


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
    assert native.await_count == 2
    release.set()
    await asyncio.gather(*subject.tasks.values())
    terminal = await subject.execute(ctx, "status", {"run_id": report["run_id"]})
    assert terminal["data"]["state"] == "passed"


@pytest.mark.asyncio
@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("oversized_field", ["selection", "installation"])
async def test_initial_report_limit_leaves_capacity_for_a_healthy_run(
    service, monkeypatch, prepared, oversized_field
):
    subject, ctx, _, native = service
    work = AsyncMock(return_value={"state": "passed", "native_settled": True})
    monkeypatch.setattr("services.verification.runner.VerificationRunner.run", work)
    subject.store.max_active_per_scope = 1
    paths = [
        f"Assets/{index:03d}_{'a' * 4082}.cs"
        for index in range(129 if oversized_field == "selection" else 126)
    ]
    if oversized_field == "installation":
        native.return_value["data"]["mcpPackage"] = {"description": "p" * 16_384}

    options = {"paths": paths}
    if prepared:
        result = await subject.execute(ctx, "prepare", options)
        options = {"preparation_id": result["data"]["preparation_id"]}

    refused = await subject.execute(ctx, "run", options)

    assert refused["code"] == "verification_report_limit"
    assert not subject.store.records and not subject.tasks and not subject.runners
    work.assert_not_awaited()
    if prepared:
        status = await subject.execute(ctx, "status", options)
        assert status["data"]["run_id"] is None
    else:
        assert not subject.preparations

    native.return_value["data"].pop("mcpPackage", None)
    healthy = await subject.execute(ctx, "run", {"paths": ["Assets/Healthy.cs"], "wait_seconds": 1})
    assert healthy["data"]["state"] == "passed"


@pytest.mark.asyncio
@pytest.mark.parametrize("interrupted", [False, True])
async def test_oversized_admitted_report_keeps_native_settlement_recoverable(
    service, monkeypatch, interrupted
):
    subject, ctx, target, _ = service
    subject.store.max_active_per_scope = 1
    child_id = "b" * 32
    emitted, resume = asyncio.Event(), asyncio.Event()
    quality = AsyncMock(
        return_value={"success": True, "data": {"job_id": child_id, "terminal": False}}
    )

    async def work(runner):
        child = {
            "kind": "analysis",
            "job_id": child_id,
            "settled": False,
            "dispatch": "acknowledged",
        }
        runner.report["children"].append(child)
        runner.report["latest_input_inventory"] = {"description": "x" * (512 * 1024)}
        try:
            runner.publish()
        finally:
            emitted.set()
        await resume.wait()
        if interrupted:
            raise asyncio.CancelledError
        child["settled"] = True
        runner.report["state"] = "passed"
        runner.publish()
        return runner.report

    monkeypatch.setattr("services.verification.runner.VerificationRunner.run", work)
    monkeypatch.setattr("services.verification.service.VerificationIO.quality", quality)
    started = await subject.execute(ctx, "run", {"paths": ["Assets/One.cs"]})
    run_id = started["data"]["run_id"]
    options = {"run_id": run_id}
    try:
        await emitted.wait()
        running = (await subject.execute(ctx, "report", options))["data"]
        assert running["state"] == "running"
        assert running["interruption"] == "verification_report_limit"
        assert running["native_settled"] is False
        assert running["children"][0]["job_id"] == child_id
        assert "latest_input_inventory" not in running
    finally:
        resume.set()
        await asyncio.gather(subject.tasks[run_id], return_exceptions=True)

    assert subject.tasks[run_id].exception() is None
    terminal = (await subject.execute(ctx, "status", options))["data"]
    assert terminal["state"] == "incomplete"
    assert terminal["interruption"] == "verification_report_limit"
    assert terminal["native_settled"] is not interrupted
    assert "latest_input_inventory" not in subject.runners[run_id].report
    if interrupted:
        assert not subject.store.records[run_id]["settled"]
        refused = await subject.execute(ctx, "run", {"paths": ["Assets/StillOwned.cs"]})
        assert refused["code"] == "verification_capacity"
        assert len(subject.preparations) == 1
        quality.return_value["data"].update(terminal=True, state="completed")
        recovered = (await subject.execute(ctx, "status", options))["data"]
        assert recovered["native_settled"] is True
    assert subject.store.records[run_id]["settled"]
    another = subject.store.create(target.scope.key, {"state": "running"})
    assert subject.store.read(another, target.scope.key)["state"] == "running"


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
    assert native.await_count == 2


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
async def test_compile_owner_waits_for_release_then_settles_from_the_same_job():
    from tests.test_compile_coordination_contract import NATIVE_IDLE, native_evidence

    child = {"kind": "compile", "job_id": "a" * 32, "settled": False}
    response = {
        "success": True,
        "data": native_evidence("success"),
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
        phase="finished", recovery_action="wait_for_editor_quiescence"
    )
    await subject.observe_settlement(runner, monotonic() + 5, cancel=False)
    assert not child["settled"]
    response["native_coordination"] = NATIVE_IDLE
    await subject.observe_settlement(runner, monotonic() + 5, cancel=False)
    assert child["settled"]


@pytest.mark.asyncio
@pytest.mark.parametrize("other_owner", [False, True])
@pytest.mark.parametrize(
    "reason,settled", [("interrupted_before_native_dispatch", True), ("result_unavailable", False)]
)
async def test_pre_dispatch_interruption_and_untracked_id_at_late_poll(
    other_owner, reason, settled
):
    from tests.test_compile_coordination_contract import COMPILE_ID, NATIVE_IDLE

    child = {"kind": "compile", "job_id": COMPILE_ID, "settled": False}
    coordination = dict(NATIVE_IDLE)
    if other_owner:
        coordination.update(
            phase="other_owned_reservation",
            reason="native_job_active",
            retryable=True,
            recovery_action="wait_for_source_preparation",
            recovery={
                "source": "owned_compilation",
                "phase": "preparing",
                "reason": "preparing_compilation",
                "mode": "recoverable",
                "message": "Preparing.",
                "job_id": "c" * 32,
            },
        )
    response = {
        "success": True,
        "data": {
            "schema_version": "unity-mcp/compile_job@1",
            "state": "finished",
            "compile_id": COMPILE_ID,
            "session_id": "b" * 32,
            "requested_at_unix_ms": 1 if settled else None,
            "attribution": "unconfirmed",
            "reason": reason,
        },
        "native_coordination": coordination,
    }
    runner = SimpleNamespace(
        report={"children": [child]},
        io=SimpleNamespace(native=AsyncMock(return_value=response)),
        publish=lambda: None,
    )
    subject = VerificationService()
    await subject.observe_settlement(runner, monotonic() + 5, cancel=False)
    assert child["settled"] is settled


@pytest.mark.asyncio
@pytest.mark.parametrize("last_owner", ["other_source_only", "still_finishing", "released"])
async def test_source_preflight_accounts_for_each_child_using_its_own_response(
    service, monkeypatch, last_owner
):
    from models.verification import VerificationProfile
    from services import source_freshness
    from services.verification.runner import VerificationRunner
    from services.verification.service import BoundContext, VerificationIO
    from tests.test_compile_coordination_contract import NATIVE_IDLE, native_evidence
    from tests.test_source_freshness_contracts import current

    _, ctx, target, _ = service
    io = VerificationIO(BoundContext(ctx.fastmcp, "Editor@project", None), target)
    runner = VerificationRunner(io, VerificationProfile(), {})
    requests = []

    async def send(_fn, _instance, name, params, **_kwargs):
        requests.append(name)
        if name == "refresh_unity":
            return {
                "success": True,
                "data": {
                    "refresh_triggered": params["compile"] == "none",
                    "compile_requested": params["compile"] == "request",
                },
            }
        if len(requests) < 5:
            return {"success": True, "source_freshness": {**current(), "status": "changed"}}
        reply = {"success": True, "source_freshness": current()}
        if last_owner != "other_source_only":
            proof = dict(NATIVE_IDLE)
            if last_owner == "still_finishing":
                proof.update(phase="finished", reason="native_job_active", retryable=True)
            reply.update(
                data={**native_evidence("success"), "compile_id": params["compile_id"]},
                native_coordination=proof,
            )
        return reply

    monkeypatch.setattr(source_freshness.unity_transport, "send_with_unity_instance", send)
    actual = await io.freshness("if_needed", monotonic() + 5)
    assert actual["status"] == "observed_current"
    children = runner.report["children"]
    assert len(children) == 2
    imported = children[0]
    compiled = children[1]
    assert imported["settled"] and imported["dispatch"] == "acknowledged"
    assert compiled["settled"] is (last_owner == "released")
    assert requests == [
        "get_compile_job",
        "refresh_unity",
        "get_compile_job",
        "refresh_unity",
        "get_compile_job",
    ]


@pytest.mark.asyncio
async def test_late_pending_poll_cannot_reopen_a_settled_verification_child():
    child = {"kind": "analysis", "job_id": "a" * 32, "settled": False}
    entered, resume = asyncio.Event(), asyncio.Event()

    async def poll(*_args):
        terminal = entered.is_set()
        if not terminal:
            entered.set()
            await resume.wait()
        return {
            "success": True,
            "data": {
                "job_id": child["job_id"],
                "state": "completed" if terminal else "running",
                "terminal": terminal,
            },
        }

    runner = SimpleNamespace(
        report={"children": [child]}, io=SimpleNamespace(quality=poll), publish=lambda: None
    )
    service = VerificationService()
    first = asyncio.create_task(service.observe_settlement(runner, monotonic() + 5, cancel=False))
    await entered.wait()
    try:
        await service.observe_settlement(runner, monotonic() + 5, cancel=False)
        assert child["settled"]
    finally:
        resume.set()
        await first
    assert child["settled"]
