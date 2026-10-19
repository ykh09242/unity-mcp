"""Native profile contracts cross the actual FastMCP schema and selected transport."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastmcp import FastMCP, Client
import pytest

from services.tools.manage_simulation_profiles import manage_simulation_profiles


@pytest.fixture
def unity_route(monkeypatch):
    send = AsyncMock(
        return_value={"success": True, "data": {"receipt_id": "owned", "state": "applied"}}
    )
    monkeypatch.setattr(
        "services.tools.manage_simulation_profiles.get_unity_instance_from_context",
        AsyncMock(return_value="selected-editor"),
    )
    monkeypatch.setattr("services.tools.manage_simulation_profiles.send_with_unity_instance", send)
    return send


@pytest.mark.asyncio
async def test_apply_preserves_both_revision_preconditions_without_reload_retry(unity_route):
    result = await manage_simulation_profiles(
        SimpleNamespace(),
        "apply",
        name="Slow",
        profile_revision="profile-sha",
        current_revision="current-sha",
    )
    assert result["success"]
    assert unity_route.call_args.args[1:] == (
        "selected-editor",
        "manage_simulation_profiles",
        {
            "action": "apply",
            "name": "Slow",
            "profile_revision": "profile-sha",
            "current_revision": "current-sha",
        },
    )
    assert unity_route.call_args.kwargs == {"retry_on_reload": False}


@pytest.mark.asyncio
async def test_capture_preserves_false_zero_and_vector_without_applying_globals(unity_route):
    settings = {"audio.pause": False, "audio.volume": 0, "physics3d.gravity": [0, -4, 0]}
    await manage_simulation_profiles(
        SimpleNamespace(),
        "capture",
        name="Quiet",
        domains=["audio", "physics3d"],
        settings=settings,
    )
    assert unity_route.call_args.args[3] == {
        "action": "capture",
        "name": "Quiet",
        "domains": ["audio", "physics3d"],
        "settings": settings,
    }
    assert unity_route.call_args.kwargs == {"retry_on_reload": False}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action,kwargs",
    [
        ("capabilities", {}),
        ("list", {"limit": 4, "offset": 0}),
        ("get", {"name": "Slow"}),
        ("diff", {"name": "Slow"}),
        ("validate", {"name": "Slow"}),
        ("receipt", {"receipt_id": "owned"}),
    ],
)
async def test_read_actions_keep_normal_transport_retry(action, kwargs, unity_route):
    await manage_simulation_profiles(SimpleNamespace(), action, **kwargs)
    assert unity_route.call_args.kwargs == {"retry_on_reload": True}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action,kwargs", [("restore", {"receipt_id": "owned"}), ("delete", {"name": "Slow"})]
)
async def test_restore_and_delete_are_not_replayed(action, kwargs, unity_route):
    await manage_simulation_profiles(SimpleNamespace(), action, **kwargs)
    assert unity_route.call_args.kwargs == {"retry_on_reload": False}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action,kwargs",
    [
        ("apply", {"name": "Slow"}),
        ("capture", {"name": ""}),
        ("capture", {"name": "x" * 65}),
        ("capture", {"name": "Owned", "domains": ["unknown"]}),
        ("restore", {}),
        ("list", {"limit": True}),
        ("list", {"limit": 33}),
        ("list", {"offset": -1}),
        ("execute_code", {}),
    ],
)
async def test_invalid_contract_refuses_before_editor_selection(action, kwargs, unity_route):
    result = await manage_simulation_profiles(SimpleNamespace(), action, **kwargs)
    assert result["success"] is False
    unity_route.assert_not_called()


@pytest.mark.asyncio
async def test_actual_fastmcp_schema_and_client_payload_preserve_receipt_protocol(unity_route):
    server = FastMCP("simulation-profile-schema")
    server.tool(manage_simulation_profiles)
    async with Client(server) as client:
        tool = next(
            tool for tool in await client.list_tools() if tool.name == "manage_simulation_profiles"
        )
        schema = tool.input_schema
        assert "apply" in schema["properties"]["action"]["enum"]
        assert "restore" in schema["properties"]["action"]["enum"]
        assert {
            "profile_revision",
            "current_revision",
            "receipt_id",
            "domains",
            "settings",
        } <= schema["properties"].keys()
        result = await client.call_tool(
            "manage_simulation_profiles",
            {
                "action": "apply",
                "name": "Owned",
                "profile_revision": "profile-sha",
                "current_revision": "current-sha",
            },
        )
        assert not result.is_error
        assert unity_route.call_args.args[3]["profile_revision"] == "profile-sha"
        assert unity_route.call_args.kwargs == {"retry_on_reload": False}
