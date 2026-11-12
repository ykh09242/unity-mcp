from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.verification.edits import complete_edit
from services.verification.service import VerificationService


def test_compact_summary_retains_observations_counts_and_truncation():
    from services.verification.edits import verification_summary

    summary = verification_summary(
        {
            "success": True,
            "data": {
                "state": "failed",
                "native_settled": True,
                "stages": [
                    {
                        "name": "saved_source_before",
                        "state": "passed",
                        "evidence": {
                            "files": [{"path": "Assets/A.cs", "matches_saved_source": True}]
                        },
                    },
                    {
                        "name": "analysis",
                        "state": "failed",
                        "evidence": {
                            "complete": True,
                            "active_severity_counts": {"error": 25, "warning": 2, "info": 0},
                            "findings": [
                                {"severity": "error", "message": str(i)} for i in range(20)
                            ],
                        },
                    },
                    {
                        "name": "saved_source_after",
                        "state": "stale",
                        "evidence": {
                            "files": [{"path": "Assets/A.cs", "matches_saved_source": False}]
                        },
                    },
                ],
            },
        }
    )
    assert summary["observed_values"] == [
        {"stage": "saved_source_before", "path": "Assets/A.cs", "matches_saved_source": True},
        {"stage": "saved_source_after", "path": "Assets/A.cs", "matches_saved_source": False},
    ]
    assert summary["active_severity_counts"] == {"error": 25, "warning": 2, "info": 0}
    assert len(summary["findings"]) == 20
    assert summary["findings_count"] == 27
    assert summary["findings_omitted"] == 7
    assert summary["findings_truncated"] is True
    assert summary["complete"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state,expected",
    [("passed", "passed"), ("failed", "issues_found"), ("running", "pending"), ("stale", "stale")],
)
async def test_edit_keeps_saved_outcome_and_attaches_original_run(monkeypatch, state, expected):
    start = AsyncMock(
        return_value={
            "success": True,
            "data": {
                "run_id": "a" * 32,
                "state": state,
                "stages": [],
                "native_settled": state != "running",
            },
        }
    )
    monkeypatch.setattr(VerificationService, "start_edit", start)
    original = {"success": True, "data": {"source_saved": True, "bytes_sha256": "b" * 64}}
    result = await complete_edit(
        SimpleNamespace(fastmcp=SimpleNamespace()), "Editor@project", original, ["Assets/A.cs"]
    )
    assert result["success"] is True and result["data"]["source_saved"] is True
    assert result["data"]["verification"]["status"] == expected
    assert result["data"]["verification"]["run_id"] == "a" * 32
    assert "verification" not in original["data"]
    assert start.call_args.kwargs["expected_sources"] == {"Assets/A.cs": {"bytes_sha256": "b" * 64}}


@pytest.mark.asyncio
async def test_unavailable_analysis_preserves_partial_write_receipt(monkeypatch):
    monkeypatch.setattr(
        VerificationService, "start_edit", AsyncMock(side_effect=ConnectionError("offline"))
    )
    result = await complete_edit(
        SimpleNamespace(fastmcp=SimpleNamespace()),
        "Editor@project",
        {
            "success": False,
            "code": "partial",
            "data": {"source_saved": True},
        },
        ["Assets/A.cs"],
    )
    assert result["success"] is False and result["code"] == "partial"
    assert result["data"]["verification"]["status"] == "unavailable"
    assert result["data"]["verification"]["further_fixes_required"] is None


@pytest.mark.asyncio
async def test_opt_out_and_failed_write_never_start_verification(monkeypatch):
    start = AsyncMock()
    monkeypatch.setattr(VerificationService, "start_edit", start)
    ctx = SimpleNamespace(fastmcp=SimpleNamespace())
    for response, mode in [
        ({"success": True}, "off"),
        ({"success": False}, "auto"),
        ({"success": True, "data": {"no_op": True}}, "auto"),
    ]:
        assert (
            await complete_edit(ctx, "Editor@project", response, ["Assets/A.cs"], mode=mode)
            is response
        )
    start.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_is_verified_as_absent_and_never_selected_for_analysis(monkeypatch):
    start = AsyncMock(
        return_value={
            "success": True,
            "data": {"run_id": "a" * 32, "state": "running", "stages": []},
        }
    )
    monkeypatch.setattr(VerificationService, "start_edit", start)
    await complete_edit(
        SimpleNamespace(fastmcp=SimpleNamespace()),
        "Editor@project",
        {"success": True, "data": {"deleted": True}},
        ["Assets/A.cs"],
        deleted=True,
    )
    assert start.call_args.kwargs["expected_sources"] == {"Assets/A.cs": {"absent": True}}
    assert start.call_args.kwargs["paths"] == []


@pytest.mark.asyncio
async def test_registered_edit_releases_mutation_ownership_before_observation(monkeypatch):
    from services.verification import edits

    owner = SimpleNamespace(
        request=SimpleNamespace(scope=SimpleNamespace(instance="original-project"))
    )
    current = [owner]
    monkeypatch.setattr(edits, "active_operation", lambda: current[0])
    start = AsyncMock(
        return_value={
            "success": True,
            "data": {"run_id": "a" * 32, "state": "running", "stages": []},
        }
    )
    monkeypatch.setattr(VerificationService, "start_edit", start)
    ctx = SimpleNamespace(fastmcp=SimpleNamespace())

    async def invoke():
        with edits.bind_edit_target(
            SimpleNamespace(scope=owner.request.scope, resume_generation="process")
        ):
            response = await complete_edit(
                ctx, None, {"success": True, "data": {"source_saved": True}}, ["Assets/A.cs"]
            )
        start.assert_not_awaited()
        current[0] = None
        return response

    result = await edits.after_operation(invoke)()
    assert result["data"]["verification"]["run_id"] == "a" * 32
    assert start.call_args.args[1] == "original-project"
    assert start.call_args.kwargs["original_target"].resume_generation == "process"


@pytest.mark.asyncio
async def test_registered_edit_without_original_target_does_not_acquire_a_later_editor(monkeypatch):
    from services.verification import edits

    start = AsyncMock(return_value={"success": True, "data": {"state": "running"}})
    monkeypatch.setattr(VerificationService, "start_edit", start)

    async def invoke():
        with edits.bind_edit_target(None):
            return await complete_edit(
                SimpleNamespace(fastmcp=SimpleNamespace()),
                "Editor@project",
                {"success": True, "data": {"source_saved": True}},
                ["Assets/A.cs"],
            )

    result = await edits.after_operation(invoke)()
    assert result["data"]["source_saved"] is True
    assert result["data"]["verification"]["status"] == "unavailable"
    start.assert_not_awaited()


@pytest.mark.asyncio
async def test_outer_unknown_result_never_becomes_a_confirmed_edit(monkeypatch):
    from services.verification import edits

    monkeypatch.setattr(edits, "active_operation", lambda: None)
    start = AsyncMock()
    monkeypatch.setattr(VerificationService, "start_edit", start)

    async def invoke():
        await complete_edit(
            SimpleNamespace(fastmcp=SimpleNamespace()),
            "project",
            {"success": True, "data": {"source_saved": True}},
            ["Assets/A.cs"],
        )
        return {"success": False, "data": {"execution_status": "unknown"}}

    result = await edits.after_operation(invoke)()
    assert "verification" not in result["data"]
    start.assert_not_awaited()


@pytest.mark.asyncio
async def test_public_mutation_late_refusal_never_replays_or_starts_verification(monkeypatch):
    from services.registry import operation_invocation as invocation
    from services.registry.operation_scheduler import OperationScope, OperationScopeChanged
    from services.registry.operation_target import OperationTarget
    from services.verification.service import BoundContext

    target = OperationTarget(OperationScope("local", "project", "g1"), lambda: True)
    monkeypatch.setattr(invocation, "capture_target", AsyncMock(return_value=target))
    monkeypatch.setattr(invocation, "authorize_remote_operation", AsyncMock())
    monkeypatch.setattr(invocation, "trusted_tool", lambda *_: True)
    start = AsyncMock()
    monkeypatch.setattr(VerificationService, "start_edit", start)
    writes = []

    async def edit(ctx):
        writes.append("saved")
        await complete_edit(
            ctx, "project", {"success": True, "data": {"source_saved": True}}, ["Assets/A.cs"]
        )
        raise OperationScopeChanged()

    result = await invocation.wrap_tool(edit, "create_script", "manage_script")(
        BoundContext(SimpleNamespace(), "Editor@project", None)
    )
    assert result["success"] is False
    assert writes == ["saved"]
    start.assert_not_awaited()


@pytest.mark.asyncio
async def test_expired_hosted_credential_keeps_saved_source_without_starting_jobs(monkeypatch):
    from services.verification import service

    monkeypatch.setattr(service.config, "http_remote_hosted", True)
    credential = AsyncMock(side_effect=PermissionError("expired"))
    capture = AsyncMock()
    monkeypatch.setattr(service, "require_credential_lease", credential)
    monkeypatch.setattr(service, "capture_target", capture)
    result = await complete_edit(
        SimpleNamespace(fastmcp=SimpleNamespace()),
        "Editor@project",
        {"success": True, "data": {"source_saved": True}},
        ["Assets/A.cs"],
    )
    assert result["success"] is True and result["data"]["source_saved"] is True
    assert result["data"]["verification"]["status"] == "unavailable"
    credential.assert_awaited_once()
    capture.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("partial", [False, True])
async def test_change_set_checks_final_confirmed_files_once(monkeypatch, partial):
    import importlib
    from services.reference_graph import mutation_tracking

    module = importlib.import_module("services.tools.script_change_set")
    rows = [
        {
            "path": f"Assets/{name}.cs",
            "state": state,
            "replace_returned": True,
            "candidate_bytes_sha256": "b" * 64,
            "observed_bytes_sha256": observed,
        }
        for name, state, observed in [
            ("A", "confirmed", "b" * 64),
            ("B", "confirmed", "b" * 64),
            ("C", "restored", "b" * 64),
            ("D", "uncertain", "b" * 64),
            ("E", "confirmed", "c" * 64),
        ]
    ]
    receipt = {
        "success": not partial,
        "data": {"files": rows, "phase": "partial" if partial else "applied"},
    }
    mutation = AsyncMock(return_value=receipt)
    monkeypatch.setattr(mutation_tracking, "active_service", lambda *_: None)
    monkeypatch.setattr(mutation_tracking, "tracked_mutation", mutation)
    monkeypatch.setattr(
        module, "get_unity_instance_from_context", AsyncMock(return_value="original")
    )
    start = AsyncMock(
        return_value={"success": True, "data": {"state": "running", "run_id": "a" * 32}}
    )
    monkeypatch.setattr(VerificationService, "start_edit", start)
    result = await module.script_change_set(
        SimpleNamespace(fastmcp=SimpleNamespace()),
        "apply",
        change_set_id="a" * 32,
        verification="auto",
    )
    assert result["success"] is not partial
    assert result["data"]["files"] == rows
    assert result["data"]["verification_excluded_files"] == 3
    assert start.await_count == mutation.await_count == 1
    assert start.call_args.kwargs["paths"] == ["Assets/A.cs", "Assets/B.cs"]
    assert start.call_args.kwargs["expected_sources"] == {
        "Assets/A.cs": {"bytes_sha256": "b" * 64},
        "Assets/B.cs": {"bytes_sha256": "b" * 64},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("data", ["malformed", [], {"files": None}, {"files": "malformed"}])
async def test_change_set_malformed_receipt_preserves_native_result(monkeypatch, data):
    import importlib
    from services.reference_graph import mutation_tracking

    module = importlib.import_module("services.tools.script_change_set")
    receipt = {"success": True, "data": data}
    monkeypatch.setattr(mutation_tracking, "active_service", lambda *_: None)
    monkeypatch.setattr(mutation_tracking, "tracked_mutation", AsyncMock(return_value=receipt))
    monkeypatch.setattr(
        module, "get_unity_instance_from_context", AsyncMock(return_value="original")
    )
    start = AsyncMock()
    monkeypatch.setattr(VerificationService, "start_edit", start)
    result = await module.script_change_set(
        SimpleNamespace(fastmcp=SimpleNamespace()),
        "apply",
        change_set_id="a" * 32,
        verification="auto",
    )
    assert result["success"] is True
    if isinstance(data, dict):
        assert result["data"]["files"] == data["files"]
    else:
        assert result["data"] == data
    start.assert_not_awaited()
