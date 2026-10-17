"""Public diagnostics preserve the committed SDK catalog on failed refresh."""

import json
from unittest.mock import AsyncMock

from fastmcp import Client, FastMCP
import pytest

from core.config import config
from models.models import ToolDefinitionModel
from services.custom_tool_service import CustomToolService


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_public_diagnostic_resources_are_discoverable(mode, monkeypatch):
    from services.resources import register_all_resources

    # Given a real SDK server using the production resource discovery.
    monkeypatch.setattr(config, "http_remote_hosted", False)
    app = FastMCP("diagnostic-resource-discovery")
    register_all_resources(app)
    # When the client discovers public resources and templates.
    async with Client(app, mode=mode) as client:
        resources = await client.list_resources()
        templates = await client.list_resource_templates()
    # Then both summary support and the actual named template are advertised.
    assert "mcpforunity://tool-diagnostics" in {str(row.uri) for row in resources}
    assert "mcpforunity://tool-diagnostics/{tool_name}" in {row.uri_template for row in templates}


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_failed_refresh_records_safe_attempt_and_preserves_public_call(mode, monkeypatch):
    import services.custom_tool_service as module

    # Given a committed callable tool and an invalid newer signature.
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(CustomToolService, "_instance", None)
    app = FastMCP("diagnostic-failed-refresh")
    service = CustomToolService(app)
    service.replace_project_catalog(
        "project", [ToolDefinitionModel(name="safe_tool")], kind="manual"
    )
    monkeypatch.setattr(
        module, "get_unity_instance_from_context", AsyncMock(return_value="P@project")
    )
    monkeypatch.setattr(module, "resolve_project_id_for_unity_instance", lambda _: "project")
    monkeypatch.setattr(
        module, "send_with_unity_instance", AsyncMock(return_value={"success": True})
    )
    old = service._sources[("manual", None, "project")]
    bad = ToolDefinitionModel(
        name="safe_tool", parameters=[{"name": "bad-name", "type": "integer"}]
    )
    # When a whole-catalog refresh is rejected.
    with pytest.raises((ValueError, TypeError)):
        service.replace_project_catalog("project", [bad], kind="manual")
    # Then actual SDK discovery/call still use the prior generation and the attempt is safe.
    async with Client(app, mode=mode) as client:
        assert [tool.name for tool in await client.list_tools()] == ["safe_tool"]
        assert not (await client.call_tool("safe_tool", {})).is_error
    assert service._sources[old.key] is old
    assert hasattr(service, "get_diagnostic_attempts"), (
        "failed registration has no structured evidence"
    )
    attempts = service.get_diagnostic_attempts("manual", "project")
    assert attempts.rows[0].state == "registration_failed"
    assert attempts.prior_catalog_retained is True
    encoded = attempts.model_dump_json()
    assert "bad-name" not in encoded and "Traceback" not in encoded
    assert json.loads(encoded)["rows"][0]["next_action"] == "review_schema_and_defaults"


def make_service(monkeypatch):
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(CustomToolService, "_instance", None)
    return CustomToolService(FastMCP("diagnostic-control"))


def test_bad_member_is_not_confused_with_later_valid_sibling(monkeypatch):
    service = make_service(monkeypatch)
    bad = ToolDefinitionModel(name="bad", parameters=[{"name": "bad-name"}])
    good = ToolDefinitionModel(name="good")
    with pytest.raises(ValueError):
        service.replace_project_catalog("p", [bad, good], kind="manual")
    attempt = service.get_diagnostic_attempts("manual", "p")
    assert [(row.tool_name, row.reason) for row in attempt.rows] == [
        ("bad", "schema_or_default_invalid"),
        ("good", "batch_rejected"),
    ]
    assert not service._sources and not service._owned_tools


def test_duplicate_batch_reason_and_committed_catalog_are_independent(monkeypatch):
    service = make_service(monkeypatch)
    good = ToolDefinitionModel(name="prior")
    service.replace_project_catalog("p", [good], kind="manual")
    previous = service._sources
    with pytest.raises(ValueError):
        service.replace_project_catalog("p", [good, good], kind="manual")
    attempt = service.get_diagnostic_attempts("manual", "p")
    assert attempt.rows[0].reason == "duplicate_name"
    assert attempt.catalog_revision == 1 and attempt.prior_catalog_retained
    assert service._sources is previous


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
@pytest.mark.parametrize("failure", ["add_after_insert", "visibility_after_apply"])
async def test_late_publication_failure_keeps_actual_sdk_generation(monkeypatch, mode, failure):
    service = make_service(monkeypatch)
    app = service._mcp
    service.replace_project_catalog("p", [ToolDefinitionModel(name="prior")], kind="manual")
    previous = service._sources
    original = app.add_tool
    visibility = []

    def fail_after_insert(tool):
        original(tool)
        raise RuntimeError("secret detail must not escape")

    def fail_visibility():
        visibility.append("applied")
        raise RuntimeError("secret detail must not escape")

    options = {}
    if failure == "add_after_insert":
        monkeypatch.setattr(app, "add_tool", fail_after_insert)
    else:
        options["visibility"] = (fail_visibility, lambda: visibility.append("rolled_back"))
    with pytest.raises(RuntimeError):
        service.replace_project_catalog(
            "p", [ToolDefinitionModel(name="replacement")], kind="manual", **options
        )
    async with Client(app, mode=mode) as client:
        assert [row.name for row in await client.list_tools()] == ["prior"]
    assert service._sources is previous
    attempt = service.get_diagnostic_attempts("manual", "p")
    assert attempt.rows[0].reason == "publication_failed"
    assert "secret" not in attempt.model_dump_json()
    if failure == "visibility_after_apply":
        assert visibility == ["applied", "rolled_back"]


def test_encoded_quotas_charge_complete_identity_and_outer_wrappers(monkeypatch):
    service = make_service(monkeypatch)
    names = ["\u4e00" * 125 + f"{i:03}" for i in range(64)]
    for i in range(80):
        service.record_diagnostic_attempt(
            "http", "p" * 200 + str(i), "g" * 200, "u" * 200, names=names
        )
    retained = service._diagnostic_attempts
    assert len(retained) <= 256
    assert all(size <= 32 * 1024 for _, size, _, _ in retained.values())
    assert any(observation.truncated for _, _, observation, _ in retained.values())
    complete = []
    for (kind, tenant, project, generation), (_, size, observation, _) in retained.items():
        record = {
            "transport": kind,
            "tenant": tenant,
            "project": project,
            "generation": generation,
            "observation": observation.model_dump(mode="json"),
        }
        assert size == len(
            json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
        )
        complete.append(record)
    assert (
        len(
            json.dumps(
                {"records": complete}, ensure_ascii=False, separators=(",", ":"), allow_nan=False
            ).encode()
        )
        <= 512 * 1024
    )


def test_live_scope_capacity_and_manual_expiry_do_not_consume_catalog_slots(monkeypatch):
    import services.custom_tool_service as module

    service = make_service(monkeypatch)
    for i in range(260):
        service.record_diagnostic_attempt("http", str(i), "generation", names=["owned"])
    assert len(service._diagnostic_attempts) == 256 and not service._sources
    assert service.get_diagnostic_attempts("http", "0", generation="generation") is None
    service.record_diagnostic_attempt("manual", "manual", names=["owned"])
    now = module.time.monotonic()
    monkeypatch.setattr(module.time, "monotonic", lambda: now + 601)
    assert service.get_diagnostic_attempts("manual", "manual") is None


def test_exact_generation_retirement_and_retained_generation_are_separate(monkeypatch):
    from hashlib import sha256

    service = make_service(monkeypatch)
    service.replace_project_catalog("p", [ToolDefinitionModel(name="prior")], generation="old")
    bad = ToolDefinitionModel(name="bad", parameters=[{"name": "bad-name"}])
    with pytest.raises(ValueError):
        service.replace_project_catalog("p", [bad], generation="new")
    attempt = service.get_diagnostic_attempts("http", "p", generation="new")
    assert attempt.generation == sha256(b"new").hexdigest()
    assert attempt.catalog_generation == sha256(b"old").hexdigest()
    service.retire_diagnostic_generation("http", "p", "old")
    assert service.get_diagnostic_attempts("http", "p", generation="new") is attempt


@pytest.mark.asyncio
@pytest.mark.parametrize("known_offender", [True, False])
async def test_malformed_stdio_snapshot_records_only_captured_current_owner(
    monkeypatch, known_offender
):
    from transport.legacy import unity_connection as legacy
    from services.tools import sync_tool_visibility_from_unity
    from transport.plugin_hub import PluginHub

    service = make_service(monkeypatch)
    monkeypatch.setattr(config, "transport_mode", "stdio")
    monkeypatch.setattr(PluginHub, "_mcp", service._mcp)
    pool = legacy.UnityConnectionPool()
    connection = legacy.UnityConnection(port=1, instance_id="Owned@p")
    connection.sock = object()
    connection.session_generation = "owned-generation"
    pool._connections[connection.instance_id] = connection
    monkeypatch.setattr(legacy, "_unity_connection_pool", pool)
    monkeypatch.setattr(legacy, "get_unity_connection", lambda *_: connection)
    monkeypatch.setattr(
        legacy,
        "get_authenticated_stdio_generation",
        AsyncMock(return_value=connection.session_generation),
    )
    rows = [{"name": "prior", "is_built_in": False}]
    monkeypatch.setattr(
        connection, "send_command", lambda *_a, **_kw: {"success": True, "data": {"tools": rows}}
    )
    assert (await sync_tool_visibility_from_unity("Owned@p", notify=False))["synced"]
    old = service._sources[("stdio", None, "p")]
    rows = [
        {"name": "good", "is_built_in": False},
        {"name": "bad", "parameters": ["secret"]} if known_offender else "secret",
    ]
    assert "error" in await sync_tool_visibility_from_unity("Owned@p", notify=False)
    attempt = service.get_diagnostic_attempts(
        "stdio", "p", generation=connection.session_generation
    )
    assert {(row.tool_name, row.reason) for row in attempt.rows} == (
        {("good", "batch_rejected"), ("bad", "metadata_invalid")}
        if known_offender
        else {(None, "metadata_invalid")}
    )
    assert "secret" not in attempt.model_dump_json()
    assert service._sources[old.key] is old
    connection.sock = None


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_failed_refresh_preserves_actual_custom_fallback_call(monkeypatch, mode):
    import services.custom_tool_service as custom
    import services.tools.execute_custom_tool as fallback

    service = make_service(monkeypatch)
    service.replace_project_catalog("p", [ToolDefinitionModel(name="prior")], kind="manual")
    monkeypatch.setattr(
        fallback, "get_unity_instance_from_context", AsyncMock(return_value="Owned@p")
    )
    monkeypatch.setattr(fallback, "resolve_project_id_for_unity_instance", lambda _: "p")
    dispatch = AsyncMock(return_value={"success": True, "data": {"owned": True}})
    monkeypatch.setattr(custom, "send_with_unity_instance", dispatch)
    service._mcp.tool(fallback.execute_custom_tool)
    with pytest.raises(ValueError):
        service.replace_project_catalog(
            "p", [ToolDefinitionModel(name="bad", parameters=[{"name": "bad-name"}])], kind="manual"
        )
    async with Client(service._mcp, mode=mode) as client:
        response = await client.call_tool(
            "execute_custom_tool", {"tool_name": "prior", "parameters": {}}
        )
    assert response.structured_content["success"] is True
    assert dispatch.await_count == 1


def test_public_attempt_ordinal_has_no_cross_owner_gaps(monkeypatch):
    service = make_service(monkeypatch)
    service.record_diagnostic_attempt(
        "http", "same-project", "owner-a", "tenant-a", names=["owned"]
    )
    first = service.get_diagnostic_attempts("http", "same-project", "tenant-a", "owner-a")
    for _ in range(7):
        service.record_diagnostic_attempt(
            "http", "same-project", "owner-b", "tenant-b", names=["foreign"]
        )
    service.record_diagnostic_attempt(
        "http", "same-project", "owner-a", "tenant-a", names=["owned"]
    )
    second = service.get_diagnostic_attempts("http", "same-project", "tenant-a", "owner-a")
    assert first.attempt == 1 and second.attempt == 2
