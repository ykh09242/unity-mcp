"""Compilation tracking keeps request outcomes separate from readiness."""

from types import SimpleNamespace

import pytest

from services.tools import refresh_unity as refresh_module


@pytest.mark.asyncio
@pytest.mark.parametrize("wait", [False, True])
async def test_refresh_returns_pre_dispatch_compile_identity_even_for_legacy_peer(
    monkeypatch, wait
):
    # Given an old peer that acknowledges refresh but has no compile ledger.
    sent = []

    async def selected(ctx):
        return "CompileFixture@one"

    async def send(fn, instance, command, params, **kwargs):
        sent.append((command, params.copy(), kwargs))
        return {"success": True, "data": {"compile_requested": True}}

    async def ready(ctx, **kwargs):
        return True, 0.0

    monkeypatch.setattr(refresh_module, "get_unity_instance_from_context", selected)
    monkeypatch.setattr(refresh_module.unity_transport, "send_with_unity_instance", send)
    monkeypatch.setattr(refresh_module, "wait_for_editor_ready", ready)
    # When the existing public refresh operation requests compilation.
    result = await refresh_module.refresh_unity(
        SimpleNamespace(), compile="request", wait_for_ready=wait
    )
    # Then readiness cannot certify success, and the pre-dispatch token is recoverable.
    value = result.model_dump()
    compilation = value["data"]["compilation"]
    assert len(compilation["compile_id"]) == 32
    assert sent[0][1]["compile_id"] == compilation["compile_id"]
    assert compilation["outcome"] == "unknown"
    assert compilation["reason"] == "tracking_unsupported"
    assert sent[0][2] == {"retry_on_reload": False}


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["2026-07-28", "legacy"])
async def test_public_status_never_forwards_to_legacy_refresh(monkeypatch, protocol):
    # Given an old native dispatch table whose refresh ignores action=status.
    import json
    from fastmcp import Client, FastMCP
    from services.tools import get_compile_job as status

    commands = []
    mutations = []

    async def selected(ctx):
        return "CompileFixture@one"

    async def transport(fn, instance, command, params, **kwargs):
        commands.append(command)
        assert instance == "CompileFixture@one"
        if command == "refresh_unity":
            mutations.append(True)
            return {"success": True}
        assert command == "get_compile_job"
        return {"success": False, "error": "Unknown command"}

    monkeypatch.setattr(status, "get_unity_instance_from_context", selected)
    monkeypatch.setattr(status.unity_transport, "send_with_unity_instance", transport)
    app = FastMCP("compile-status")
    app.tool(name="get_compile_job")(status.get_compile_job)
    # When the public status tool queries an old peer.
    async with Client(app, mode=protocol) as client:
        result = await client.call_tool("get_compile_job", {"compile_id": "a" * 32})
    value = json.loads(result.content[0].text)
    # Then status is explicitly unsupported and no mutation dispatch occurred.
    assert commands == ["get_compile_job"]
    assert mutations == []
    assert value["data"]["outcome"] == "unknown"
    assert value["data"]["reason"] == "tracking_unsupported"


def native_job(compile_id, outcome="failed"):
    from services.tools.get_compile_job import unknown_compile_job

    job = unknown_compile_job(compile_id, "")
    job.update(
        session_id="b" * 32,
        generation=3,
        candidate_generations=[3],
        requested_at_unix_ms=1,
        started_at_unix_ms=2,
        finished_at_unix_ms=3,
        state="finished",
        outcome=outcome,
        attribution="observed_after_request",
        collection_complete=True,
        assembly_count=2,
        error_count=int(outcome == "failed"),
    )
    return job


@pytest.mark.asyncio
@pytest.mark.parametrize("disconnect", [False, True])
async def test_ready_editor_returns_failed_compile_without_mutation_replay(monkeypatch, disconnect):
    # Given a finished failed compilation on a ready Editor.
    commands = []
    ids = []

    async def selected(ctx):
        return "CompileFixture@one"

    async def send(fn, instance, command, params, **kwargs):
        commands.append(command)
        if command == "refresh_unity":
            ids.append(params["compile_id"])
            if disconnect:
                return {"success": False, "error": "disconnected"}
            return {"success": True, "data": {"compilation": native_job(ids[0])}}
        assert command == "get_compile_job"
        assert params["compile_id"] == ids[0]
        return {"success": True, "data": native_job(ids[0])}

    async def ready(ctx, **kwargs):
        return True, 0.0

    monkeypatch.setattr(refresh_module, "get_unity_instance_from_context", selected)
    monkeypatch.setattr(refresh_module.unity_transport, "send_with_unity_instance", send)
    monkeypatch.setattr(refresh_module, "wait_for_editor_ready", ready)
    # When readiness completes after refresh or response loss.
    result = await refresh_module.refresh_unity(SimpleNamespace(), compile="request")
    # Then top-level readiness success never erases failed compiler evidence.
    assert result.success is True
    assert result.data["compilation"]["outcome"] == "failed"
    assert result.data["compilation"]["compile_id"] == ids[0]
    assert commands == ["refresh_unity", "get_compile_job"]
    assert result.data["source_revision_confirmed"] is False


@pytest.mark.parametrize("change", ["id", "truth", "bool_counter", "oversize", "missing_schema"])
def test_untrusted_native_evidence_fails_closed(change):
    # Given a native response with a malformed or contradictory field.
    from services.tools.get_compile_job import parse_compile_job

    job = native_job("a" * 32, "success")
    if change == "id":
        job["compile_id"] = "c" * 32
    elif change == "truth":
        job["collection_complete"] = False
    elif change == "bool_counter":
        job["error_count"] = True
    elif change == "oversize":
        job["diagnostics"] = [{}] * 101
    else:
        del job["schema_version"]
    # When the response crosses the public boundary.
    result = parse_compile_job(job, "a" * 32)
    # Then no malformed response can certify success.
    assert result["outcome"] == "unknown"
    assert result["reason"] == "tracking_unsupported"


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["", "a" * 33, "A" * 32, "../job", None])
async def test_invalid_status_id_is_rejected_before_transport(monkeypatch, value):
    from services.tools import get_compile_job as status

    async def forbidden(*args, **kwargs):
        raise AssertionError("Invalid IDs must not reach transport")

    monkeypatch.setattr(status, "get_unity_instance_from_context", forbidden)
    result = await status.get_compile_job(SimpleNamespace(), value)
    assert result.success is False


@pytest.mark.asyncio
async def test_unavailable_status_read_is_unknown_without_exception_leak(monkeypatch):
    from services.tools import get_compile_job as status

    async def send(*args, **kwargs):
        raise RuntimeError("private exception text")

    monkeypatch.setattr(status.unity_transport, "send_with_unity_instance", send)
    value = await status.read_compile_job("CompileFixture@one", "a" * 32)
    assert value["outcome"] == "unknown"
    assert value["reason"] == "observation_unavailable"
    assert "private" not in str(value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("file", "X:/sdk/foo.cs"),
        ("assembly", "X:/sdk/A.dll"),
        ("message", "CS0006 '/opt/sdk/foo.dll' missing"),
    ],
)
def test_untrusted_absolute_paths_cannot_escape_native_response_boundary(field, value):
    from services.tools.get_compile_job import parse_compile_job

    job = native_job("a" * 32)
    item = dict(
        severity="error",
        assembly="A.dll",
        file="Assets/Fixture.cs",
        line=1,
        column=1,
        message="Missing type",
        diagnostic_text_omitted=False,
    )
    item[field] = value
    job["diagnostics"] = [item]
    assert parse_compile_job(job, "a" * 32)["reason"] == "tracking_unsupported"


def test_compilation_summary_is_preserved_by_editor_state_model():
    from services.resources.editor_state import EditorStateCompilation

    state = EditorStateCompilation.model_validate(
        {
            "latest_job": {
                "tracking_schema": "unity-mcp/compile_job@1",
                "session_id": "a" * 32,
                "generation": 4,
                "outcome": "failed",
                "error_count": 2,
                "warning_count": 3,
            }
        }
    )
    assert state.latest_job.outcome == "failed"
    assert state.model_dump()["latest_job"]["generation"] == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["2026-07-28", "legacy"])
@pytest.mark.parametrize("outcome", ["success", "failed"])
async def test_public_status_returns_selected_request_and_strict_outcome(
    monkeypatch, protocol, outcome
):
    import json
    from fastmcp import Client, FastMCP
    from services.tools import get_compile_job as status

    async def selected(ctx):
        return "CompileFixture@selected"

    async def transport(fn, instance, command, params, **kwargs):
        assert instance == "CompileFixture@selected"
        assert command == "get_compile_job" and params == {"compile_id": "a" * 32}
        return {"success": True, "data": native_job("a" * 32, outcome)}

    monkeypatch.setattr(status, "get_unity_instance_from_context", selected)
    monkeypatch.setattr(status.unity_transport, "send_with_unity_instance", transport)
    app = FastMCP("compile-status-positive")
    app.tool(name="get_compile_job")(status.get_compile_job)
    async with Client(app, mode=protocol) as client:
        response = await client.call_tool("get_compile_job", {"compile_id": "a" * 32})
    value = json.loads(response.content[0].text)
    assert value["success"] is True
    assert value["data"]["outcome"] == outcome
    assert value["data"]["generation"] == 3
    assert value["data"]["session_id"] == "b" * 32


@pytest.mark.parametrize(
    "change",
    [
        "no_candidate",
        "multiple_candidates",
        "different_candidate",
        "no_request_time",
        "reversed_request_time",
    ],
)
def test_definitive_outcome_requires_exact_request_candidate_and_chronology(change):
    from services.tools.get_compile_job import parse_compile_job

    job = native_job("a" * 32, "success")
    if change == "no_candidate":
        job["candidate_generations"] = []
    elif change == "multiple_candidates":
        job["candidate_generations"] = [2, 3]
    elif change == "different_candidate":
        job["candidate_generations"] = [2]
    elif change == "no_request_time":
        job["requested_at_unix_ms"] = None
    else:
        job["requested_at_unix_ms"] = 4
    assert parse_compile_job(job, "a" * 32)["reason"] == "tracking_unsupported"
