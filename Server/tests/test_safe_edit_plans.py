"""Focused boundary regressions for optional safe edit plans."""

import importlib
from unittest.mock import AsyncMock

import pytest

scene = importlib.import_module("services.tools.manage_scene_edits")
prefabs = importlib.import_module("services.tools.manage_prefabs")


@pytest.fixture
def bridge(monkeypatch):
    send = AsyncMock(return_value={"success": True, "data": {"preview_id": "fixture"}})
    for tool in (scene, prefabs):
        monkeypatch.setattr(
            tool, "get_unity_instance_from_context", AsyncMock(return_value="fixture")
        )
        monkeypatch.setattr(tool, "preflight", AsyncMock(return_value=None))
        monkeypatch.setattr(tool, "send_with_unity_instance", send)
    return send


@pytest.mark.asyncio
async def test_structure_preview_preserves_order_and_temporary_ids(bridge):
    operations = [
        {"op": "create", "temp_id": "$parent", "name": "Parent", "parent": 42},
        {"op": "reparent", "target": 43, "parent": "$parent"},
        {"op": "delete", "target": 44},
    ]
    result = await scene.manage_scene_edits(
        None, "preview_structure", target=42, operations=operations
    )
    assert result["success"] is True
    assert bridge.call_args.args[3]["operations"] == operations
    assert bridge.call_args.args[3]["target"] == 42


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {"action": "apply_structure", "operations": [{"op": "delete", "target": 42}], "target": 42},
        {
            "action": "preview_structure",
            "target": True,
            "operations": [{"op": "delete", "target": 42}],
        },
        {
            "action": "preview_structure",
            "target": 42,
            "operations": [{"op": "create", "temp_id": "$p", "name": "Parent", "parent": "$later"}],
        },
        {
            "action": "preview_structure",
            "target": 42,
            "operations": [{"op": "delete", "target": 42, "extra": 1}],
        },
    ],
)
async def test_invalid_structure_never_dispatches(bridge, arguments):
    result = await scene.manage_scene_edits(None, **arguments)
    assert result["success"] is False
    bridge.assert_not_awaited()


@pytest.mark.asyncio
async def test_structure_apply_is_bound_to_token(bridge):
    result = await scene.manage_scene_edits(None, "apply_structure", preview_id="fixture")
    assert result["success"] is True
    assert bridge.call_args.args[3] == {
        "action": "apply_structure",
        "preview_id": "fixture",
        "undo_name": "MCP Scene Edits",
    }


@pytest.mark.asyncio
async def test_capsule_reuses_expected_old_contract(bridge):
    edit = {
        "target": 42,
        "component_type": "CapsuleCollider",
        "property": "m_Radius",
        "value": 2,
        "expected_old": 0.5,
    }
    result = await scene.manage_scene_edits(None, "apply", edits=[edit])
    assert result["success"] is True
    assert bridge.call_args.args[3]["edits"] == [edit]


@pytest.mark.asyncio
async def test_prefab_preview_and_apply_route_opaque_token(bridge):
    result = await prefabs.manage_prefabs(
        None, "preview_modify_contents", prefab_path="Assets/Source.prefab", position=[1, 2, 3]
    )
    assert result["success"] is True
    assert bridge.call_args.args[3]["position"] == [1.0, 2.0, 3.0]
    result = await prefabs.manage_prefabs(None, "apply_preview", preview_id="fixture")
    assert result["success"] is True
    assert bridge.call_args.args[3] == {"action": "apply_preview", "preview_id": "fixture"}


@pytest.mark.asyncio
async def test_prefab_preview_root_name_refuses_before_dispatch(bridge):
    result = await prefabs.manage_prefabs(
        None, "preview_modify_contents", prefab_path="Assets/Source.prefab", name="Unsupported"
    )
    assert result["success"] is False
    assert "normalizes" in result["message"]
    bridge.assert_not_awaited()
