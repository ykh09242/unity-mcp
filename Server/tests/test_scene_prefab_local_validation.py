"""Domain request validation must precede Editor I/O and retain explicit flags."""
import asyncio
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.mark.parametrize("field", ["additive", "remove_scene", "auto_repair", "include_transform", "build_index"])
def test_scene_rejects_malformed_explicit_option_before_editor_io(monkeypatch, field):
    # Given an explicit malformed option, with observable Editor I/O boundaries.
    module = importlib.import_module("services.tools.manage_scene")
    instance = AsyncMock(return_value="instance")
    preflight = AsyncMock(return_value=None)
    send = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(module, "get_unity_instance_from_context", instance)
    monkeypatch.setattr(module, "preflight", preflight)
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    # When the real wrapper receives it (additive omission would select Single load).
    response = asyncio.run(module.manage_scene(SimpleNamespace(), action="load", path="Assets/Level.unity", **{field: "garbage"}))
    # Then the request fails without reaching the Editor.
    assert response["success"] is False
    assert field in response["message"]
    instance.assert_not_awaited()
    preflight.assert_not_awaited()
    send.assert_not_awaited()


@pytest.mark.parametrize("field,wire", [("additive", "additive"), ("remove_scene", "removeScene"), ("auto_repair", "autoRepair"), ("include_transform", "includeTransform")])
@pytest.mark.parametrize("value", [False, "false", "0"])
def test_scene_retains_explicit_false_flags(monkeypatch, field, wire, value):
    # Given a valid false representation.
    module = importlib.import_module("services.tools.manage_scene")
    monkeypatch.setattr(module, "get_unity_instance_from_context", AsyncMock(return_value="instance"))
    monkeypatch.setattr(module, "preflight", AsyncMock(return_value=None))
    send = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    # When preparing the wire command.
    response = asyncio.run(module.manage_scene(SimpleNamespace(), action="load", **{field: value}, build_index="0"))
    # Then false and zero are present, not treated as omitted.
    assert response["success"] is True
    payload = send.await_args.args[3]
    assert payload[wire] is False
    assert payload["buildIndex"] == 0


@pytest.mark.parametrize("domain,kwargs", [
    ("manage_gameobject", {"action": "modify", "target": "Player", "position": [1, 2]}),
    ("manage_gameobject", {"action": "modify", "target": "Player", "components_to_add": '["broken"'}),
    ("manage_prefabs", {"action": "modify_contents", "prefab_path": "Assets/Test.prefab", "scale": [1, 2]}),
    ("manage_prefabs", {"action": "modify_contents", "prefab_path": "Assets/Test.prefab", "create_child": {"name": "Child", "position": [1, 2]}}),
])
def test_domain_invalid_local_payload_avoids_editor_io(monkeypatch, domain, kwargs):
    # Given an invalid payload already rejected by the domain's existing validators.
    module = importlib.import_module("services.tools." + domain)
    instance = AsyncMock(return_value="instance")
    preflight = AsyncMock(return_value=None)
    send = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(module, "get_unity_instance_from_context", instance)
    monkeypatch.setattr(module, "preflight", preflight)
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    # When invoking the real wrapper.
    response = asyncio.run(getattr(module, domain)(SimpleNamespace(), **kwargs))
    # Then validation occurs before potentially expensive refresh/compile work.
    assert response["success"] is False
    instance.assert_not_awaited()
    preflight.assert_not_awaited()
    send.assert_not_awaited()
