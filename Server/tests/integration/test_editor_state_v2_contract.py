import pytest

from services.registry import get_registered_resources

from .test_helpers import DummyContext


@pytest.mark.asyncio
async def test_editor_state_v2_is_registered_and_has_contract_fields(monkeypatch):
    """
    Canonical editor state resource should be `mcpforunity://editor/state` and conform to v2 contract fields.
    """
    # Import module to ensure it registers its decorator without disturbing global registry state.
    import services.resources.editor_state  # noqa: F401

    resources = get_registered_resources()

    state_res = next(
        (r for r in resources if r.get("uri") == "mcpforunity://editor/state"),
        None,
    )
    assert state_res is not None, (
        "Expected canonical editor state resource `mcpforunity://editor/state` to be registered. "
        "This is required so clients can poll readiness/staleness and avoid tool loops."
    )

    async def fake_send_with_unity_instance(
        send_fn, unity_instance, command_type, params, **kwargs
    ):
        # Minimal stub payload for v2 resource tests. The server layer should enrich with staleness/advice.
        assert command_type == "get_editor_state"
        return {
            "success": True,
            "data": {
                "schema_version": "unity-mcp/editor_state@2",
                "observed_at_unix_ms": 1730000000000,
                "sequence": 1,
                "compilation": {"is_compiling": False, "is_domain_reload_pending": False},
                "tests": {"is_running": False},
            },
        }

    # Patch transport so the resource can be invoked without Unity running.
    import transport.unity_transport as unity_transport

    monkeypatch.setattr(unity_transport, "send_with_unity_instance", fake_send_with_unity_instance)

    result = await state_res["func"](DummyContext())
    payload = result.model_dump() if hasattr(result, "model_dump") else result
    assert isinstance(payload, dict)

    # Contract assertions (top-level)
    assert payload.get("success") is True
    data = payload.get("data")
    assert isinstance(data, dict)
    assert data.get("schema_version") == "unity-mcp/editor_state@2"
    assert "observed_at_unix_ms" in data
    assert "sequence" in data
    assert "advice" in data
    assert "staleness" in data


@pytest.mark.asyncio
@pytest.mark.parametrize("complete,limited", [(False, False), (True, True), (True, False)])
async def test_public_readiness_exposes_scan_coverage(monkeypatch, complete, limited):
    from unittest.mock import AsyncMock
    import services.resources.editor_state as state

    monkeypatch.setattr(state.config, "transport_mode", "http")
    monkeypatch.setattr(state.config, "http_remote_hosted", False)
    monkeypatch.setattr(
        state, "get_unity_instance_from_context", AsyncMock(return_value="Owned@coverage")
    )
    monkeypatch.setattr(state, "_local_project_root", AsyncMock(return_value=None))
    monkeypatch.setattr(
        state.unity_transport,
        "send_with_unity_instance",
        AsyncMock(
            return_value={
                "success": True,
                "data": {
                    "observed_at_unix_ms": state._now_unix_ms(),
                    "compilation": {"is_compiling": False, "is_domain_reload_pending": False},
                },
            }
        ),
    )
    monkeypatch.setattr(
        state.external_changes_scanner,
        "update_and_get_async",
        AsyncMock(
            return_value={
                "external_changes_dirty": False,
                "external_changes_scan_complete": complete,
                "external_changes_scan_limited": limited,
                "external_changes_scan_in_progress": True,
                "external_changes_scan_limited_reason": "local_package_root_limit"
                if limited
                else None,
            }
        ),
    )
    response = await state.get_editor_state_authoritative(DummyContext())
    assert response.success
    assert response.data["assets"]["external_changes_scan_complete"] is complete
    assert response.data["assets"]["external_changes_scan_limited"] is limited
    assert response.data["assets"]["external_changes_scan_in_progress"] is True
    assert response.data["assets"]["external_changes_scan_limited_reason"] == (
        "local_package_root_limit" if limited else None
    )
    assert response.data["advice"]["ready_for_tools"] is True
    assert response.data["advice"]["blocking_reasons"] == []
    if not complete or limited:
        assert any("scan" in reason for reason in response.data["advice"]["warnings"])


@pytest.mark.asyncio
@pytest.mark.parametrize("has_root", [False, True])
async def test_scan_readiness_distinguishes_unavailable_root_from_pending_scan(
    monkeypatch, tmp_path, has_root
):
    from unittest.mock import AsyncMock
    import services.resources.editor_state as state
    from services.state.external_changes_scanner import ExternalChangesScanner

    monkeypatch.setattr(state.config, "transport_mode", "http")
    monkeypatch.setattr(state.config, "http_remote_hosted", False)
    monkeypatch.setattr(
        state, "get_unity_instance_from_context", AsyncMock(return_value="Owned@scope")
    )
    monkeypatch.setattr(
        state, "_local_project_root", AsyncMock(return_value=str(tmp_path) if has_root else None)
    )
    # Use the real scanner state; pytest keeps filesystem traversal inert until requested.
    monkeypatch.setattr(state, "external_changes_scanner", ExternalChangesScanner())
    monkeypatch.setattr(
        state.unity_transport,
        "send_with_unity_instance",
        AsyncMock(
            return_value={
                "success": True,
                "data": {
                    "observed_at_unix_ms": state._now_unix_ms(),
                    "compilation": {"is_compiling": False, "is_domain_reload_pending": False},
                },
            }
        ),
    )
    response = await state.get_editor_state_authoritative(DummyContext())
    assert response.success
    assets, advice = response.data["assets"], response.data["advice"]
    assert assets["external_changes_scan_complete"] is (False if has_root else None)
    assert assets["external_changes_scan_limited"] is (False if has_root else None)
    assert advice["ready_for_tools"] is True
    assert advice["blocking_reasons"] == []
    assert advice["warnings"] == (["external_changes_scan_pending"] if has_root else [])
