"""Tests for manage_prefabs tool."""

import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.tools.manage_prefabs import manage_prefabs
from services.registry import get_registered_tools


@pytest.mark.asyncio
async def test_override_listing_forwards_filters_and_paging(mock_unity):
    result = await manage_prefabs(
        SimpleNamespace(),
        action="list_overrides",
        target=-42,
        object_id=-7,
        property_filter="m_Size",
        offset=5,
        page_size=25,
    )
    assert result["success"] is True
    assert mock_unity["params"] == {
        "action": "list_overrides",
        "target": -42,
        "objectId": -7,
        "propertyFilter": "m_Size",
        "offset": 5,
        "pageSize": 25,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["revert_overrides", "apply_overrides"])
async def test_override_writes_forward_only_explicit_selection(mock_unity, action):
    result = await manage_prefabs(
        SimpleNamespace(),
        action=action,
        target="Parent/Child",
        prefab_path="Assets/Prefab.prefab",
        override_ids=["property:-42:m_IsTrigger"],
    )
    assert result["success"] is True
    assert mock_unity["params"]["overrideIds"] == ["property:-42:m_IsTrigger"]
    assert mock_unity["params"]["target"] == "Parent/Child"


@pytest.mark.asyncio
@pytest.mark.parametrize("values", [[], [""], ["id", "id"], ["id"] * 101, "id", [42]])
async def test_override_writes_reject_bad_selection_without_transport(mock_unity, values):
    result = await manage_prefabs(
        SimpleNamespace(), action="revert_overrides", target=42, override_ids=values
    )
    assert result["success"] is False
    assert "params" not in mock_unity


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {"offset": -1},
        {"offset": True},
        {"page_size": 0},
        {"page_size": 501},
        {"page_size": 1.5},
        {"object_id": True},
        {"object_id": 2**31},
        {"property_filter": "x" * 513},
    ],
)
async def test_override_listing_rejects_invalid_bounds(mock_unity, kwargs):
    result = await manage_prefabs(SimpleNamespace(), action="list_overrides", target=42, **kwargs)
    assert result["success"] is False
    assert "params" not in mock_unity


@pytest.mark.asyncio
async def test_override_apply_requires_destination_and_writes_reject_filters(mock_unity):
    result = await manage_prefabs(
        SimpleNamespace(), action="apply_overrides", target=42, override_ids=["id"]
    )
    assert result["success"] is False
    result = await manage_prefabs(
        SimpleNamespace(),
        action="revert_overrides",
        target=42,
        override_ids=["id"],
        property_filter="m_Size",
    )
    assert result["success"] is False
    assert "params" not in mock_unity


# ── Fixture ──────────────────────────────────────────────────────────


@pytest.fixture
def mock_unity(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_send(send_fn, unity_instance, tool_name, params):
        captured["unity_instance"] = unity_instance
        captured["tool_name"] = tool_name
        captured["params"] = params
        return {"success": True, "message": "ok"}

    monkeypatch.setattr(
        "services.tools.manage_prefabs.get_unity_instance_from_context",
        AsyncMock(return_value="unity-instance-1"),
    )
    monkeypatch.setattr(
        "services.tools.manage_prefabs.send_with_unity_instance",
        fake_send,
    )
    monkeypatch.setattr(
        "services.tools.manage_prefabs.preflight",
        AsyncMock(return_value=None),
    )
    return captured


# ── component_properties ─────────────────────────────────────────────


class TestManagePrefabsComponentProperties:
    """Tests for the component_properties parameter on manage_prefabs."""

    def test_component_properties_parameter_exists(self):
        """The manage_prefabs tool should have a component_properties parameter."""
        sig = inspect.signature(manage_prefabs)
        assert "component_properties" in sig.parameters

    def test_component_properties_parameter_is_optional(self):
        """component_properties should default to None."""
        sig = inspect.signature(manage_prefabs)
        param = sig.parameters["component_properties"]
        assert param.default is None

    def test_tool_description_mentions_component_properties(self):
        """The tool description should mention component_properties."""
        prefab_tool = next(
            (t for t in get_registered_tools() if t["name"] == "manage_prefabs"), None
        )
        assert prefab_tool is not None
        desc = prefab_tool.get("description") or prefab_tool.get("kwargs", {}).get(
            "description", ""
        )
        assert "component_properties" in desc

    def test_required_params_include_modify_contents(self):
        """modify_contents should be a valid action requiring prefab_path."""
        from services.tools.manage_prefabs import REQUIRED_PARAMS

        assert "modify_contents" in REQUIRED_PARAMS
        assert "prefab_path" in REQUIRED_PARAMS["modify_contents"]


# ── delete_child ─────────────────────────────────────────────────────


class TestManagePrefabsTargets:
    @pytest.mark.parametrize(
        "target",
        [-19340, "-19340", "McpProbeCube", "Parent/McpProbeCube", "/Parent/McpProbeCube", 0],
    )
    def test_create_target_forwards_id_name_and_path(self, mock_unity, target):
        result = asyncio.run(
            manage_prefabs(
                SimpleNamespace(),
                action="create_from_gameobject",
                target=target,
                prefab_path="Assets/Prefabs/ProbeCube.prefab",
            )
        )
        assert result["success"] is True
        assert mock_unity["params"]["target"] == target

    def test_create_response_keeps_saved_root_identity(self, monkeypatch, mock_unity):
        response = {
            "success": True,
            "data": {
                "instanceName": "McpProbeCube",
                "instanceId": -19340,
                "prefabPath": "Assets/Prefabs/ProbeCube.prefab",
                "rootObjectName": "ProbeCube",
                "rootObjectPath": "ProbeCube",
            },
        }

        async def fake_send(*_args):
            return response

        monkeypatch.setattr("services.tools.manage_prefabs.send_with_unity_instance", fake_send)
        result = asyncio.run(
            manage_prefabs(
                SimpleNamespace(),
                action="create_from_gameobject",
                target=-19340,
                prefab_path="Assets/Prefabs/ProbeCube.prefab",
            )
        )
        assert result is response
        assert result["data"]["rootObjectPath"] == "ProbeCube"


class TestManagePrefabsDeleteChild:
    """Tests for the delete_child parameter on manage_prefabs."""

    def test_delete_child_parameter_exists(self):
        """The manage_prefabs tool should have a delete_child parameter."""
        sig = inspect.signature(manage_prefabs)
        assert "delete_child" in sig.parameters

    def test_delete_child_parameter_is_optional(self):
        """delete_child should default to None."""
        sig = inspect.signature(manage_prefabs)
        param = sig.parameters["delete_child"]
        assert param.default is None

    def test_tool_description_mentions_delete_child(self):
        """The tool description should mention delete_child."""
        prefab_tool = next(
            (t for t in get_registered_tools() if t["name"] == "manage_prefabs"), None
        )
        assert prefab_tool is not None
        desc = prefab_tool.get("description") or prefab_tool.get("kwargs", {}).get(
            "description", ""
        )
        assert "delete_child" in desc

    def test_delete_child_string_forwards_to_unity(self, mock_unity):
        """A single string delete_child should be forwarded as-is."""
        result = asyncio.run(
            manage_prefabs(
                SimpleNamespace(),
                action="modify_contents",
                prefab_path="Assets/Prefabs/Test.prefab",
                delete_child="Child1",
            )
        )
        assert result["success"] is True
        assert mock_unity["tool_name"] == "manage_prefabs"
        assert mock_unity["params"]["deleteChild"] == "Child1"

    def test_delete_child_list_forwards_to_unity(self, mock_unity):
        """A list of delete_child paths should be forwarded as-is."""
        result = asyncio.run(
            manage_prefabs(
                SimpleNamespace(),
                action="modify_contents",
                prefab_path="Assets/Prefabs/Test.prefab",
                delete_child=["Child1", "Child2/Grandchild"],
            )
        )
        assert result["success"] is True
        assert mock_unity["params"]["deleteChild"] == ["Child1", "Child2/Grandchild"]

    def test_delete_child_none_omitted_from_params(self, mock_unity):
        """When delete_child is None, deleteChild should not appear in params."""
        asyncio.run(
            manage_prefabs(
                SimpleNamespace(),
                action="modify_contents",
                prefab_path="Assets/Prefabs/Test.prefab",
            )
        )
        assert "deleteChild" not in mock_unity["params"]


# ── Prefab Stage Actions ────────────────────────────────────────────


class TestManagePrefabsStageActions:
    """Tests for open/save/close prefab stage actions on manage_prefabs."""

    def test_description_mentions_open_prefab_stage(self):
        """The tool description should mention open_prefab_stage."""
        prefab_tool = next(
            (t for t in get_registered_tools() if t["name"] == "manage_prefabs"), None
        )
        assert prefab_tool is not None
        desc = prefab_tool.get("description") or prefab_tool.get("kwargs", {}).get(
            "description", ""
        )
        assert "open_prefab_stage" in desc

    def test_description_mentions_save_prefab_stage(self):
        """The tool description should mention save_prefab_stage."""
        prefab_tool = next(
            (t for t in get_registered_tools() if t["name"] == "manage_prefabs"), None
        )
        assert prefab_tool is not None
        desc = prefab_tool.get("description") or prefab_tool.get("kwargs", {}).get(
            "description", ""
        )
        assert "save_prefab_stage" in desc

    def test_open_prefab_stage_forwards_prefab_path(self, mock_unity):
        """open_prefab_stage should forward prefab_path as prefabPath."""
        result = asyncio.run(
            manage_prefabs(
                SimpleNamespace(),
                action="open_prefab_stage",
                prefab_path="Assets/Prefabs/Test.prefab",
            )
        )
        assert result["success"] is True
        assert mock_unity["params"]["action"] == "open_prefab_stage"
        assert mock_unity["params"]["prefabPath"] == "Assets/Prefabs/Test.prefab"
        assert mock_unity["tool_name"] == "manage_prefabs"

    def test_open_prefab_stage_requires_prefab_path(self, mock_unity):
        """open_prefab_stage should fail without prefab_path."""
        result = asyncio.run(
            manage_prefabs(
                SimpleNamespace(),
                action="open_prefab_stage",
            )
        )
        assert result["success"] is False
        assert "prefab_path" in result["message"]

    def test_save_prefab_stage_forwards_to_unity(self, mock_unity):
        """save_prefab_stage should forward to Unity."""
        result = asyncio.run(
            manage_prefabs(
                SimpleNamespace(),
                action="save_prefab_stage",
            )
        )
        assert result["success"] is True
        assert mock_unity["params"]["action"] == "save_prefab_stage"
        assert mock_unity["tool_name"] == "manage_prefabs"

    def test_close_prefab_stage_forwards_to_unity(self, mock_unity):
        """close_prefab_stage should forward to Unity."""
        result = asyncio.run(
            manage_prefabs(
                SimpleNamespace(),
                action="close_prefab_stage",
            )
        )
        assert result["success"] is True
        assert mock_unity["params"]["action"] == "close_prefab_stage"
        assert mock_unity["tool_name"] == "manage_prefabs"
