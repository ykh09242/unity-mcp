"""Invalid local inputs must not trigger real readiness checks or dirty refresh."""
import copy
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastmcp import Client, FastMCP
from fastmcp.server.middleware import Middleware


INVALID = [
    *[("manage_gameobject", {"action": "create", "name": "Fixture", "primitive_type": value})
      for value in ("Bogus", "Cube,Bogus", "Cube,1", "3.0", "3e0", "3_0", "３", "2147483648", "  ")],
    *[("manage_prefabs", {"action": "modify_contents", "prefab_path": "Assets/Fixture.prefab", "create_child": {"name": "Child", key: value}})
      for key in ("primitive_type", "primitiveType") for value in ("Bogus", "Cube,Bogus", "Cube,1", "3.0", "3e0", "3_0", "３", "2147483648", "  ")],
    *[(name, {"action": "unknown", **required}) for name, required in (
        ("manage_asset", {"path": "Assets/Fixture.mat"}),
        ("manage_components", {"target": "Fixture", "component_type": "BoxCollider"}),
        ("manage_gameobject", {"target": "Fixture"}),
        ("manage_prefabs", {"prefab_path": "Assets/Fixture.prefab"}),
    )],
    ("manage_asset", {"action": "get_info", "path": ""}),
    ("manage_asset", {"action": "create", "path": "Assets/Fixture.mat"}),
    ("manage_asset", {"action": "create", "path": "Assets/Fixture.asset", "asset_type": "UnsupportedType"}),
    ("manage_asset", {"action": "modify", "path": "Assets/Fixture.mat"}),
    ("manage_asset", {"action": "move", "path": "Assets/Fixture.mat"}),
    ("manage_asset", {"action": "rename", "path": "Assets/Fixture.mat", "destination": ""}),
    ("manage_asset", {"action": "search", "path": "Assets", "page_size": "1e0"}),
    ("manage_asset", {"action": "create", "path": "Assets/Fixture.mat", "properties": "[]"}),
    ("manage_components", {"action": "set_property", "target": "Fixture", "component_type": "BoxCollider"}),
    ("manage_components", {"action": "set_property", "target": "Fixture", "component_type": "BoxCollider", "properties": {}}),
    ("manage_components", {"action": "set_property", "target": "Fixture", "component_type": "BoxCollider", "property": "enabled"}),
    ("manage_components", {"action": "add", "target": "Fixture", "component_type": "BoxCollider", "properties": "[]"}),
    ("manage_gameobject", {"action": "create"}),
    ("manage_gameobject", {"action": "modify"}),
    ("manage_gameobject", {"action": "delete", "target": ""}),
    ("manage_gameobject", {"action": "move_relative", "target": "Fixture", "direction": "up"}),
    ("manage_gameobject", {"action": "move_relative", "target": "Fixture", "reference_object": "Other"}),
    ("manage_gameobject", {"action": "move_relative", "target": "Fixture", "reference_object": "Other", "direction": ""}),
    ("manage_gameobject", {"action": "look_at", "target": "Fixture"}),
    ("manage_gameobject", {"action": "look_at", "target": "Fixture", "look_at_target": [0, 1]}),
    ("manage_gameobject", {"action": "look_at", "target": "Fixture", "look_at_target": [False, 0, 1]}),
    ("manage_gameobject", {"action": "look_at", "target": "Fixture", "look_at_target": "Other", "look_at_up": "bad"}),
    ("manage_gameobject", {"action": "modify", "target": "Fixture", "component_properties": '{"BoxCollider":false}'}),
    ("manage_gameobject", {"action": "create", "name": "Fixture", "save_as_prefab": True}),
    ("manage_gameobject", {"action": "modify", "target": "Fixture", "position": "[0,false,0]"}),
    ("manage_gameobject", {"action": "modify", "target": "Fixture", "components_to_add": '[{"properties":{}}]'}),
    ("manage_prefabs", {"action": "get_info"}),
    ("manage_prefabs", {"action": "modify_contents", "prefab_path": "Assets/Fixture.prefab", "create_child": {"position": [0, 0, 0]}}),
    ("manage_prefabs", {"action": "modify_contents", "prefab_path": "Assets/Fixture.prefab", "create_child": {"name": "Child", "primitive_type": "Cube", "source_prefab_path": "Assets/Other.prefab"}}),
    ("manage_prefabs", {"action": "modify_contents", "prefab_path": "Assets/Fixture.prefab", "create_child": [{"name": "Valid"}, {"name": "Invalid", "components_to_add": [False]}]}),
    ("manage_prefabs", {"action": "modify_contents", "prefab_path": "Assets/Fixture.prefab", "create_child": {"name": "Child", "position": "[0,false,0]"}}),
    *[("manage_prefabs", {"action": "modify_contents", "prefab_path": "Assets/Fixture.prefab", "create_child": {"name": "Child", key: value}})
      for key in ("set_active", "setActive") for value in (0, 1, "0", "bad")],
    *[("manage_prefabs", {"action": "modify_contents", "prefab_path": "Assets/Fixture.prefab", "create_child": {"name": "Child", key: value}})
      for key in ("source_prefab_path", "sourcePrefabPath", "primitive_type", "primitiveType") for value in (0, False)],
    ("find_gameobjects", {"search_term": ""}),
    ("find_gameobjects", {"search_term": "Fixture", "page_size": "1e0"}),
    ("find_gameobjects", {"search_term": "Fixture", "include_inactive": "0"}),
]

VALID = [
    *[("manage_gameobject", {"action": "create", "name": "Fixture", "primitive_type": value}, {"primitiveType": value})
      for value in ("Cube", " cube ", " cApSuLe ", "3", "+3", "0003", "Cube,Sphere", "Cube , Sphere", "")],
    *[("manage_prefabs", {"action": "modify_contents", "prefab_path": "Assets/Fixture.prefab", "create_child": {"name": "Child", key: value}}, {"createChild": {"name": "Child", key: value}})
      for key in ("primitive_type", "primitiveType") for value in ("Cube", " cube ", " cApSuLe ", "3", "+3", "0003", "Cube,Sphere", "Cube , Sphere", "")],
    ("manage_gameobject", {"action": "create", "name": "Fixture", "prefab_path": "Assets/Fixture.prefab", "primitive_type": "Bogus"}, {"prefabPath": "Assets/Fixture.prefab", "primitiveType": "Bogus"}),
    ("manage_asset", {"action": "search", "path": "t:Material", "page_size": "1", "generate_preview": False}, {"path": "Assets", "searchPattern": "t:Material", "pageSize": 1, "pageNumber": 1, "generatePreview": False}),
    ("manage_components", {"action": "set_property", "target": "Fixture", "component_type": "Renderer", "property": "sharedMaterial", "value": None, "component_index": 0}, {"property": "sharedMaterial", "value": None, "componentIndex": 0}),
    ("manage_gameobject", {"action": "modify", "name": "Fixture", "set_active": "false", "position": "0,0,0", "component_properties": '{"BoxCollider":{"enabled":false,"reference":null}}'}, {"name": "Fixture", "setActive": False, "position": [0.0, 0.0, 0.0], "componentProperties": {"BoxCollider": {"enabled": False, "reference": None}}}),
    ("manage_gameobject", {"action": "look_at", "target": "Fixture", "look_at_target": "Other"}, {"look_at_target": "Other"}),
    ("manage_gameobject", {"action": "look_at", "target": "Fixture", "look_at_target": "[false,0,1]", "look_at_up": "[0,1,0]"}, {"look_at_target": "[false,0,1]", "look_at_up": [0.0, 1.0, 0.0]}),
    ("manage_gameobject", {"action": "move_relative", "target": "Fixture", "reference_object": "Other", "offset": [0, 0, 0], "distance": 0, "world_space": False}, {"offset": [0.0, 0.0, 0.0], "distance": 0, "world_space": False}),
    ("manage_prefabs", {"action": "modify_contents", "prefab_path": "Assets/Fixture.prefab", "set_active": False, "create_child": {"name": "Child", "position": "0,0,0", "components_to_add": [{"typeName": "BoxCollider"}]}}, {"setActive": False, "createChild": {"name": "Child", "position": [0.0, 0.0, 0.0], "components_to_add": [{"typeName": "BoxCollider"}]}}),
    ("manage_prefabs", {"action": "create_from_gameobject", "name": "Fixture", "prefab_path": "Assets/Fixture.prefab", "allow_overwrite": False}, {"target": "Fixture", "allowOverwrite": False}),
    ("find_gameobjects", {"search_term": "Fixture", "page_size": "0", "cursor": 0, "include_inactive": "false"}, {"pageSize": 0, "cursor": 0, "includeInactive": False}),
]


@pytest.fixture
def boundaries(monkeypatch):
    from core.config import config
    from services.resources import editor_state
    from services.tools import preflight, refresh_unity
    from transport.plugin_hub import PluginHub

    modules = {name: importlib.import_module("services.tools." + name) for name in {row[0] for row in INVALID}}
    requests = []

    async def send(instance, command, params, **kwargs):
        requests.append((command, copy.deepcopy(params)))
        if command == "get_editor_state":
            return {"success": True, "data": {"compilation": {"is_compiling": False}}}
        return {"success": True, "message": "Fixture", "data": {"enabled": False, "count": 0}}

    monkeypatch.setattr(config, "transport_mode", "http")
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(preflight, "_in_pytest", lambda: False)
    monkeypatch.setattr(PluginHub, "send_command_for_instance", staticmethod(send))
    monkeypatch.setattr(editor_state, "_local_project_root", AsyncMock(return_value=None))
    monkeypatch.setattr(editor_state.external_changes_scanner, "update_and_get_async", AsyncMock(return_value={"external_changes_dirty": True}))
    readiness = AsyncMock(wraps=editor_state.get_editor_state_authoritative)
    refresh = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(editor_state, "get_editor_state_authoritative", readiness)
    monkeypatch.setattr(refresh_unity, "refresh_unity", refresh)
    lookups = {}
    for name, module in modules.items():
        lookups[name] = AsyncMock(return_value="Fixture@selected")
        monkeypatch.setattr(module, "get_unity_instance_from_context", lookups[name])

    async def get_state(key):
        return "Fixture@selected" if key == "unity_instance" else None

    return SimpleNamespace(modules=modules, requests=requests, readiness=readiness, refresh=refresh, lookups=lookups, ctx=SimpleNamespace(get_state=get_state))


@pytest.mark.asyncio
@pytest.mark.parametrize("name,payload", INVALID)
async def test_invalid_local_object_input_has_no_readiness_or_refresh(boundaries, name, payload):
    result = await getattr(boundaries.modules[name], name)(boundaries.ctx, **payload)
    assert not boundaries.requests, (name, payload, boundaries.requests)
    boundaries.lookups[name].assert_not_awaited()
    boundaries.readiness.assert_not_awaited()
    boundaries.refresh.assert_not_awaited()
    assert result["success"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("name,payload,expected", VALID)
async def test_valid_object_input_reaches_real_preflight_and_preserves_values(boundaries, name, payload, expected):
    result = await getattr(boundaries.modules[name], name)(boundaries.ctx, **payload)
    assert result["success"] is True
    boundaries.readiness.assert_awaited_once()
    boundaries.refresh.assert_awaited_once()
    assert boundaries.requests[0][0] == "get_editor_state"
    command, wire = boundaries.requests[-1]
    assert command == name
    for key, value in expected.items():
        assert wire[key] == value
        if value is None or isinstance(value, bool):
            assert wire[key] is value


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_registered_object_inputs_reject_before_real_preflight(boundaries, mode):
    class Selected(Middleware):
        async def on_call_tool(self, context, call_next):
            await context.fastmcp_context.set_state("unity_instance", "Fixture@selected")
            return await call_next(context)

    app = FastMCP("preflight-object-validation")
    app.add_middleware(Selected())
    for name, module in boundaries.modules.items():
        app.tool(name=name)(getattr(module, name))
    async with Client(app, mode=mode) as client:
        for name, payload in INVALID:
            result = await client.call_tool(name, payload, raise_on_error=False)
            assert not boundaries.requests, (name, payload, boundaries.requests)
            assert result.is_error or result.structured_content["success"] is False, (name, payload)
            boundaries.lookups[name].assert_not_awaited()
        boundaries.readiness.assert_not_awaited()
        boundaries.refresh.assert_not_awaited()
        for name, payload, expected in VALID:
            before = len(boundaries.requests)
            result = await client.call_tool(name, payload)
            assert result.structured_content["success"] is True
            assert boundaries.requests[before][0] == "get_editor_state"
            command, wire = boundaries.requests[-1]
            assert command == name
            for key, value in expected.items():
                assert wire[key] == value
        assert boundaries.readiness.await_count == len(VALID)
        assert boundaries.refresh.await_count == len(VALID)
