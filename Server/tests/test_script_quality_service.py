"""Owner fences and once-only dispatch for optional analysis jobs."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.script_quality.service import ScriptQualityService


@pytest.fixture
def quality(monkeypatch):
    import services.script_quality.service as module

    scope = SimpleNamespace(key=("local", "project-a"))
    target = SimpleNamespace(scope=scope, is_current=lambda: True, authorize_public=None)
    monkeypatch.setattr(
        module, "get_unity_instance_from_context", AsyncMock(return_value="Editor@project-a")
    )
    monkeypatch.setattr(module, "capture_target", AsyncMock(return_value=target))
    send = AsyncMock(return_value={"success": True, "data": {"state": "queued"}})
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    return ScriptQualityService(), target, send


@pytest.mark.asyncio
async def test_job_is_bound_to_authenticated_project_and_principal(quality):
    service, target, send = quality
    response = await service.execute(None, "analyze", {"paths": ["Assets/A.cs"]})
    job_id = response["data"]["job_id"]
    assert len(job_id) == 32
    target.scope.key = ("another-user", "project-a")
    hidden = await service.execute(None, "status", {"job_id": job_id})
    assert hidden["code"] == "analysis_not_found"
    assert send.await_count == 1
    target.scope.key = ("local", "another-project")
    hidden = await service.execute(None, "cancel", {"job_id": job_id})
    assert hidden["code"] == "analysis_not_found"
    assert send.await_count == 1


@pytest.mark.asyncio
async def test_uncertain_start_preserves_id_and_never_retries(quality):
    service, _, send = quality
    send.side_effect = TimeoutError
    response = await service.execute(None, "analyze", {"paths": ["Assets/A.cs"]})
    assert response["code"] == "analysis_transport_unknown"
    assert len(response["job_id"]) == 32
    assert send.await_count == 1
    assert send.call_args.kwargs["retry_on_reload"] is False


@pytest.mark.asyncio
async def test_changed_target_is_refused_before_dispatch(quality):
    service, target, send = quality
    target.is_current = lambda: False
    response = await service.execute(None, "analyze", {"paths": ["Assets/A.cs"]})
    assert response["code"] == "analysis_target_changed"
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_read_operations_do_not_install_refresh_or_broaden_scope(quality):
    service, _, send = quality
    await service.execute(None, "capabilities", {})
    args = send.call_args.args
    assert args[2:] == ("manage_script_quality", {"action": "capabilities"})
    send.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["before", "after", None])
async def test_impact_binds_both_live_observations_to_retained_analysis(
    quality, monkeypatch, drift
):
    import services.script_quality.impact as module

    service, target, send = quality
    identity = "e" * 32
    current = {"source_token": "a" * 64, "project_id": "p"}
    service.jobs[identity] = SimpleNamespace(owner=target.scope.key, retained=True)
    monkeypatch.setattr(
        service.results, "read", lambda key, *args, **kwargs: current if key == identity else None
    )
    inspect = AsyncMock(return_value={"complete": True})
    monkeypatch.setattr(module, "inspect_script_impact", inspect)
    observations = 0

    async def respond(*args, **kwargs):
        nonlocal observations
        assert args[2] == "get_compile_job"
        assert args[3]["observe_source"] is True
        observations += 1
        changed = (drift == "before" and observations == 1) or (
            drift == "after" and observations == 2
        )
        return {
            "success": True,
            "source_freshness": {
                **current,
                "source_token": "b" * 64 if changed else "a" * 64,
                "coverage": {"complete": True},
            },
        }

    send.side_effect = respond
    response = await service.execute(None, "impact", {"analysis_id": identity})
    assert response["success"] is (drift is None)
    assert send.await_count == (1 if drift == "before" else 2)
    assert inspect.await_count == (0 if drift == "before" else 1)


@pytest.mark.asyncio
async def test_missing_explicit_selection_never_dispatches(quality):
    service, _, send = quality
    response = await service.execute(None, "analyze", {})
    assert response["code"] == "invalid_script_quality_request"
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_transport_error_envelope_cannot_settle_an_unknown_native_job(quality):
    service, _, send = quality
    send.return_value = {
        "success": False,
        "code": "command_outcome_unknown",
        "error": "Connection closed after dispatch",
    }
    response = await service.execute(None, "analyze", {"paths": ["Assets/A.cs"]})
    assert service.jobs[response["job_id"]].terminal is False


def native_completion(job_id, *, complete=True, source="a" * 64, rows=None, cursor=None, offset=0):
    return {
        "success": True,
        "data": {
            "job_id": job_id,
            "state": "completed",
            "terminal": True,
            "editor_generation": "generation",
            "result": {
                "protocol": 1,
                "complete": complete,
                "stale": False,
                "source_token": source,
                "project_id": "project",
                "context_digest": "context",
                "environment_signature": "environment",
                "selected_files": [
                    {"path": "Assets/A.cs", "guid": "b" * 32, "bytes_sha256": "c" * 64}
                ],
                "coverage_gaps": [] if complete else ["partial"],
                "analyzer_errors": [],
                "rows": rows or [],
                "offset": offset,
                "total_rows": len(rows or []),
                "next_cursor": cursor,
            },
        },
    }


@pytest.mark.asyncio
async def test_baseline_is_published_only_after_complete_analysis_and_cached(quality):
    service, _, send = quality
    start = await service.execute(None, "capture_baseline", {"paths": ["Assets/A.cs"]})
    job_id = start["data"]["job_id"]
    assert "baseline_id" not in start["data"]
    send.return_value = native_completion(job_id)
    response = await service.execute(None, "status", {"job_id": job_id})
    assert response["data"]["baseline_id"] == job_id
    send.reset_mock()
    send.side_effect = ConnectionError
    cached = await service.execute(None, "status", {"job_id": job_id})
    assert cached["data"]["baseline_id"] == job_id
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_partial_capture_does_not_publish_a_baseline(quality):
    service, _, send = quality
    start = await service.execute(None, "capture_baseline", {"paths": ["Assets/A.cs"]})
    job_id = start["data"]["job_id"]
    send.return_value = native_completion(job_id, complete=False)
    response = await service.execute(None, "status", {"job_id": job_id})
    assert response["data"]["baseline_id"] is None
    assert response["data"]["baseline_reason"] == "baseline_coverage_incomplete"


@pytest.mark.asyncio
async def test_expired_result_does_not_fetch_or_rerun_native_work(quality):
    service, _, send = quality
    now = [0.0]
    service.results.clock = lambda: now[0]
    start = await service.execute(None, "analyze", {"paths": ["Assets/A.cs"]})
    job_id = start["data"]["job_id"]
    send.return_value = native_completion(job_id)
    await service.execute(None, "status", {"job_id": job_id})
    now[0] = 3600
    send.reset_mock()
    response = await service.execute(None, "status", {"job_id": job_id})
    assert response["code"] == "analysis_expired_or_evicted"
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_pages_with_changed_source_identity_are_never_published(quality):
    service, _, send = quality
    start = await service.execute(None, "capture_baseline", {"paths": ["Assets/A.cs"]})
    job_id = start["data"]["job_id"]
    first = native_completion(job_id, cursor="next", rows=[diagnostic_row()])
    second = native_completion(job_id, source="d" * 64, rows=[diagnostic_row("new")], offset=1)
    first["data"]["result"]["total_rows"] = 2
    second["data"]["result"]["total_rows"] = 2
    send.reset_mock()
    send.side_effect = [first, second]
    response = await service.execute(None, "status", {"job_id": job_id})
    assert response["code"] == "analysis_result_inconsistent"
    assert service.results.read(job_id, ("local", "project-a"), baseline=True) is None
    assert send.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "interruption",
    [
        None,
        "exception",
        "unknown",
        "deadline",
        "connection",
        "command_capacity",
        "result_capacity",
        "result_transfer_timeout",
        "operation_capacity",
        "reloading",
        "operation_busy",
    ],
)
async def test_completed_collection_recovers_without_restarting(quality, interruption):
    from models import MCPResponse
    from transport.command_budget import command_outcome_unknown
    from transport.legacy.unity_connection import _stdio_deadline_response
    from transport.plugin_hub import PluginHub, _result_limit_response

    service, target, send = quality
    start = await service.execute(None, "analyze", {"paths": ["Assets/A.cs"]})
    job_id = start["data"]["job_id"]
    first = native_completion(job_id, rows=[diagnostic_row()], cursor="next")
    last = native_completion(job_id, rows=[diagnostic_row("last")], offset=1)
    first["data"]["result"]["total_rows"] = last["data"]["result"]["total_rows"] = 2
    errors = {
        "exception": TimeoutError(),
        "unknown": command_outcome_unknown("manage_script_quality", "Connection lost"),
        "deadline": _stdio_deadline_response("manage_script_quality", None),
        "connection": MCPResponse(success=False, error="Connection reset by peer"),
        "command_capacity": PluginHub._command_capacity_response(),
        "result_capacity": _result_limit_response("result_capacity", phase="readiness"),
        "result_transfer_timeout": _result_limit_response(
            "result_transfer_timeout", phase="result_delivery", command_id="original-command"
        ),
        "operation_capacity": MCPResponse(
            success=False,
            error="operation_capacity",
            data={"reason": "operation_capacity", "dispatch": "not_dispatched"},
        ),
        "reloading": MCPResponse(
            success=False,
            error="Unity is reloading; please retry",
            hint="retry",
            data={"reason": "reloading"},
        ),
        "operation_busy": MCPResponse(
            success=False, error="Native operation is busy", code="operation_busy"
        ),
    }
    send.side_effect = [first, errors[interruption], first, last] if interruption else [first, last]
    options = {"job_id": job_id}
    if interruption:
        interrupted = await service.execute(None, "status", options)
        assert interrupted["code"] == "analysis_collection_interrupted"
        assert interrupted["job_id"] == job_id
        assert job_id in service.results.active
        target.scope.key = ("other", "project-a")
        assert (await service.execute(None, "status", options))["code"] == "analysis_not_found"
        target.scope.key = ("local", "project-a")
    response = await service.execute(None, "status", options)
    assert [row["diagnostic"]["anchor"] for row in response["data"]["result"]["rows"]] == [
        "existing",
        "last",
    ]
    assert job_id not in service.results.active
    assert [call.args[3]["action"] for call in send.await_args_list] == [
        "analyze",
        *(["status"] * (4 if interruption else 2)),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        "missing",
        "refused",
        "scheduler",
        "auth",
        "changed_generation",
        "unframed",
        "invalid_encoding",
        "interrupted_greeting",
    ],
)
async def test_stdio_collection_keeps_exact_editor_through_selection_loss(monkeypatch, failure):
    from unittest.mock import Mock

    from models.models import UnityInstanceInfo
    from services.registry import operation_invocation
    from services.registry.operation_scheduler import OperationScheduler
    import services.script_quality.service as module
    from transport.legacy import unity_connection as legacy

    a, b = [
        UnityInstanceInfo(
            id=f"{name}@{project}",
            name=name,
            path=f"/{name}",
            hash=project,
            port=port,
            status="running",
        )
        for name, project, port in (("A", "abcd1234", 6400), ("B", "deadbeef", 6401))
    ]
    inventory = [a, b]
    pool = legacy.UnityConnectionPool()
    monkeypatch.setattr(legacy, "_unity_connection_pool", pool)
    monkeypatch.setattr(pool, "_discover_instance_candidates", lambda *args: inventory)
    monkeypatch.setattr(module.config, "transport_mode", "stdio")
    monkeypatch.setattr(module.config, "http_remote_hosted", False)
    monkeypatch.setattr(module.config, "require_framing", True)
    monkeypatch.setenv("UNITY_MCP_STDIO_ALLOW_LEGACY", "1")
    selector = AsyncMock(return_value=a.id)
    monkeypatch.setattr(module, "get_unity_instance_from_context", selector)
    scheduler = OperationScheduler(max_control_participants=0)
    monkeypatch.setattr(operation_invocation, "scheduler", lambda: scheduler)
    monkeypatch.setattr(legacy.UnityConnection, "_ensure_live_connection", lambda self: None)
    armed = False
    recovered = False
    attempts = []

    def dial(address, timeout):
        greeting = b"WELCOME UNITY-MCP 1 FRAMING=1\n"
        if address[1] == a.port and armed and not recovered:
            if failure == "refused":
                raise ConnectionRefusedError()
            greeting = {
                "unframed": b"WELCOME UNITY-MCP 1\n",
                "invalid_encoding": b"\xff\n",
                "interrupted_greeting": b"WELCOME UNITY-MCP 1 FRAM",
                "auth": b"WELCOME UNITY-MCP 2 FRAMING=1 AUTH=HMAC-SHA256\n",
            }.get(failure, greeting)
        chunks = iter(bytes([value]) for value in greeting)
        sock = Mock()
        sock.gettimeout.return_value = timeout
        sock.recv.side_effect = lambda count: next(chunks, b"")
        return sock

    def native(connection, command, params, **kwargs):
        nonlocal armed
        attempts.append((connection.instance_id, params["action"], params.get("cursor")))
        if params["action"] == "analyze":
            return {"success": True, "data": {"state": "queued"}}
        continuation = bool(params.get("cursor"))
        response = native_completion(
            params["job_id"],
            rows=[diagnostic_row("last" if continuation else "first")],
            cursor=None if continuation else "next",
            offset=int(continuation),
        )
        response["data"]["result"]["total_rows"] = 2
        if failure == "changed_generation" and recovered:
            response["data"]["editor_generation"] = "new-process"
        if not armed:
            armed = True
            if failure in {"missing", "changed_generation"}:
                inventory.remove(a)
            elif failure == "scheduler":
                scheduler._max_participants = 0
            else:
                connection.disconnect()
        return response

    monkeypatch.setattr(legacy.socket, "create_connection", dial)
    monkeypatch.setattr(legacy.UnityConnection, "send_command", native)
    monkeypatch.setattr(pool, "schedule_tool_resync", lambda connection: False)
    service = ScriptQualityService()
    try:
        start = await service.execute(None, "analyze", {"paths": ["Assets/A.cs"]})
        job_id = start["data"]["job_id"]
        options = {"job_id": job_id}
        first = await service.execute(None, "status", options)
        if failure in {"auth", "unframed", "invalid_encoding"}:
            assert first["code"] == "analysis_result_inconsistent"
            assert job_id not in service.results.active
            recovered = True
            assert (await service.execute(None, "status", options))[
                "code"
            ] == "analysis_expired_or_evicted"
            assert [action for _, action, _ in attempts] == ["analyze", "status"]
            return
        assert first["code"] == "analysis_collection_interrupted"
        assert job_id in service.results.active
        selector.return_value = b.id
        assert (await service.execute(None, "status", options))["code"] == "analysis_not_found"
        selector.return_value = a.id
        inventory[:] = [a, b]
        recovered = True
        scheduler._max_participants = 128
        retry = await service.execute(None, "status", options)
        if failure == "changed_generation":
            assert retry["code"] == "analysis_result_inconsistent"
        else:
            rows = retry["data"]["result"]["rows"]
            assert [row["diagnostic"]["anchor"] for row in rows] == ["first", "last"]
        assert job_id not in service.results.active
        assert all(target == a.id for target, _, _ in attempts)
        assert sum(action == "analyze" for _, action, _ in attempts) == 1
    finally:
        pool.disconnect_all()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["unauthorized", "permission_ended", "integrity", "payload_limit", "invalid_cursor"]
)
async def test_authorization_and_permanent_page_failures_are_not_retried(quality, failure):
    from models import MCPResponse
    from transport.command_budget import command_outcome_unknown
    from transport.plugin_hub import _result_limit_response

    service, _, send = quality
    options = {
        "job_id": (await service.execute(None, "analyze", {"paths": ["Assets/A.cs"]}))["data"][
            "job_id"
        ]
    }
    first = native_completion(options["job_id"], rows=[diagnostic_row()], cursor="next")
    first["data"]["result"]["total_rows"] = 2
    errors = {
        "unauthorized": MCPResponse(success=False, error="auth_required"),
        "permission_ended": command_outcome_unknown(
            "manage_script_quality", "Permission ended", cause="permission_ended"
        ),
        "integrity": MCPResponse(success=False, error="invalid_native_arbitration_response"),
        "payload_limit": _result_limit_response(
            "result_payload_limit", phase="result_delivery", command_id="original-command"
        ),
        "invalid_cursor": MCPResponse(
            success=False,
            error="script_quality_unavailable",
            data={"reason": "invalid_analysis_cursor"},
        ),
    }
    send.side_effect = [first, errors[failure]]
    assert (await service.execute(None, "status", options))[
        "code"
    ] == "analysis_result_inconsistent"
    assert (await service.execute(None, "status", options))["code"] == "analysis_expired_or_evicted"
    assert send.await_count == 3 and options["job_id"] not in service.results.active


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["generation", "source", "expiry"])
async def test_interrupted_collection_is_bound_to_original_observation_and_lifetime(quality, drift):
    service, target, send = quality
    now = [0.0]
    service.results.clock = lambda: now[0]
    start = await service.execute(None, "capture_baseline", {"paths": ["Assets/A.cs"]})
    job_id = start["data"]["job_id"]
    first = native_completion(job_id, rows=[diagnostic_row()], cursor="next")
    first["data"]["result"]["total_rows"] = 2
    send.side_effect = [first, ConnectionError()]
    assert (await service.execute(None, "status", {"job_id": job_id}))[
        "code"
    ] == "analysis_collection_interrupted"
    if drift == "expiry":
        now[0] += service.results.ttl
    else:
        changed = native_completion(job_id, rows=[diagnostic_row()], cursor="next")
        changed["data"]["result"]["total_rows"] = 2
        if drift == "generation":
            changed["data"]["editor_generation"] = "another-generation"
        else:
            changed["data"]["result"]["source_token"] = "d" * 64
        send.side_effect = [changed]
    response = await service.execute(None, "status", {"job_id": job_id})
    assert response["code"] == (
        "analysis_expired_or_evicted" if drift == "expiry" else "analysis_result_inconsistent"
    )
    assert job_id not in service.results.active
    assert service.results.read(job_id, target.scope.key, baseline=True) is None
    assert [call.args[3]["action"] for call in send.await_args_list].count("capture_baseline") == 1


@pytest.mark.asyncio
async def test_retention_capacity_is_reserved_before_native_dispatch(quality):
    service, _, send = quality
    service.results.total_bytes = 1
    response = await service.execute(None, "analyze", {"paths": ["Assets/A.cs"]})
    assert response["code"] == "analysis_retention_capacity"
    send.assert_not_awaited()


def diagnostic_row(anchor="existing"):
    return {
        "scope": "selected",
        "diagnostic": {
            "id": "UNT0008",
            "provider": "Unity",
            "provider_version": "1",
            "path": "Assets/A.cs",
            "anchor": anchor,
            "syntax_anchor": anchor,
            "symbol": "C.M",
            "severity": "warning",
            "message": "Unity null semantics",
            "suppressed": False,
        },
    }


@pytest.mark.asyncio
async def test_completed_baseline_classifies_new_issues_without_reusing_other_owners(quality):
    service, target, send = quality
    start = await service.execute(None, "capture_baseline", {"paths": ["Assets/A.cs"]})
    baseline_id = start["data"]["job_id"]
    send.return_value = native_completion(baseline_id, rows=[diagnostic_row()])
    await service.execute(None, "status", {"job_id": baseline_id})
    send.return_value = {"success": True, "data": {"state": "queued"}}
    start = await service.execute(
        None, "analyze", {"paths": ["Assets/A.cs"], "baseline_id": baseline_id}
    )
    job_id = start["data"]["job_id"]
    send.return_value = native_completion(
        job_id, source="d" * 64, rows=[diagnostic_row(), diagnostic_row("new")]
    )
    response = await service.execute(None, "status", {"job_id": job_id, "page_size": 1})
    page = response["data"]["result"]
    assert page["counts"] == {"introduced": 1, "existing": 1, "resolved": 0, "unclassified": 0}
    cursor = page["next_cursor"]
    assert cursor
    send.reset_mock()
    next_page = await service.execute(None, "status", {"job_id": job_id, "cursor": cursor})
    assert next_page["data"]["result"]["offset"] == 1
    send.assert_not_awaited()
    target.scope.key = ("another-user", "project-a")
    assert (await service.execute(None, "status", {"job_id": job_id, "cursor": cursor}))[
        "code"
    ] == "analysis_not_found"


@pytest.mark.asyncio
async def test_late_classified_pages_read_only_requested_rows_and_recheck_baseline(
    quality, monkeypatch
):
    import services.script_quality.pages as pages
    from services.script_quality.results import ResultRecord

    service, target, send = quality
    now = [0.0]
    service.results.clock = lambda: now[0]
    start = await service.execute(None, "capture_baseline", {"paths": ["Assets/A.cs"]})
    baseline_id = start["data"]["job_id"]
    send.return_value = native_completion(
        baseline_id, rows=[diagnostic_row("existing"), diagnostic_row("resolved")]
    )
    await service.execute(None, "status", {"job_id": baseline_id})
    now[0] = 1.0
    send.return_value = {"success": True, "data": {"state": "queued"}}
    start = await service.execute(
        None, "analyze", {"paths": ["Assets/A.cs"], "baseline_id": baseline_id}
    )
    job_id = start["data"]["job_id"]
    context = {**diagnostic_row("context"), "scope": "context"}
    rows = [diagnostic_row(str(i)) for i in range(128)] + [diagnostic_row("existing"), context]
    send.return_value = native_completion(job_id, rows=rows)
    options = {"job_id": job_id, "page_size": 129}
    page = (await service.execute(None, "status", options))["data"]["result"]
    assert [r["classification"] for r in page["rows"]] == ["introduced"] * 128 + ["existing"]
    cursor = page["next_cursor"]
    visited = []
    original = ResultRecord.read_row

    def read_row(record, index):
        visited.append(index)
        return original(record, index)

    def forbidden(*args, **kwargs):
        pytest.fail("A retained page rebuilt the diagnostic inventory")

    monkeypatch.setattr(pages, "compare_diagnostics", forbidden)
    monkeypatch.setattr(ResultRecord, "read", forbidden)
    monkeypatch.setattr(ResultRecord, "read_row", read_row)
    send.reset_mock()
    for kind, expected_index in (("resolved", 1), ("context", 129)):
        response = await service.execute(
            None, "status", {"job_id": job_id, "page_size": 1, "cursor": cursor}
        )
        page = response["data"]["result"]
        assert page["rows"][0]["classification"] == page["rows"][0]["diagnostic"]["anchor"] == kind
        assert visited == [expected_index]
        visited.clear()
        if page["next_cursor"]:
            cursor = page["next_cursor"]
    assert page["next_cursor"] is None
    target.scope.key = ("other", "project-a")
    assert (await service.execute(None, "status", options))["code"] == "analysis_not_found"
    target.scope.key = ("local", "project-a")
    now[0] = service.results.ttl
    assert (await service.execute(None, "status", {**options, "cursor": cursor}))[
        "code"
    ] == "invalid_analysis_cursor"
    assert not visited
    missing = (await service.execute(None, "status", {"job_id": job_id, "page_size": 1}))["data"][
        "result"
    ]
    assert missing["classification_reasons"] == ["baseline_missing"]
    assert missing["counts"]["unclassified"] == 129
    assert visited == [0]
    assert missing["rows"][0]["observation"] == "current"
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_public_page_respects_response_budget_across_native_pages(quality):
    from services.script_quality.pages import encoded_size

    service, _, send = quality
    start = await service.execute(None, "analyze", {"paths": ["Assets/A.cs"]})
    job_id = start["data"]["job_id"]
    row = diagnostic_row()
    row["diagnostic"]["message"] = "x" * 260_000
    first = native_completion(job_id, rows=[row], cursor="next")
    last = native_completion(job_id, rows=[row, row], offset=1)
    first["data"]["result"]["total_rows"] = last["data"]["result"]["total_rows"] = 3
    send.side_effect = [first, last]
    response = await service.execute(None, "status", {"job_id": job_id})
    page = response["data"]["result"]
    for offset in range(3):
        assert page["offset"] == offset
        assert len(page["rows"]) == 1 and encoded_size(page) <= 512 * 1024
        if offset < 2:
            follow = await service.execute(
                None, "status", {"job_id": job_id, "cursor": page["next_cursor"]}
            )
            page = follow["data"]["result"]
    assert page["next_cursor"] is None


@pytest.mark.asyncio
async def test_wrong_native_job_receipt_retains_uncertain_reservation(quality):
    service, _, send = quality
    start = await service.execute(None, "analyze", {"paths": ["Assets/A.cs"]})
    job_id = start["data"]["job_id"]
    send.return_value = native_completion("e" * 32)
    assert (await service.execute(None, "status", {"job_id": job_id}))[
        "code"
    ] == "analysis_result_inconsistent"
    assert job_id in service.results.active


@pytest.mark.asyncio
async def test_unavailable_native_job_does_not_prove_process_settlement(quality):
    service, _, send = quality
    start = await service.execute(None, "analyze", {"paths": ["Assets/A.cs"]})
    job_id = start["data"]["job_id"]
    send.return_value = {
        "success": True,
        "data": {"job_id": job_id, "state": "unavailable", "terminal": True},
    }
    await service.execute(None, "status", {"job_id": job_id})
    assert job_id in service.results.active


@pytest.mark.asyncio
async def test_host_update_refusal_releases_pre_admission_reservation(quality):
    service, _, send = quality
    send.return_value = {
        "success": False,
        "error": "script_quality_unavailable",
        "data": {"reason": "analysis_host_update_required"},
    }
    response = await service.execute(None, "analyze", {"paths": ["Assets/A.cs"]})
    assert response["job_id"] not in service.results.active
