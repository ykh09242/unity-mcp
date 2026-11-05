"""Scene observation budgets must survive both public routes before Editor I/O."""

import asyncio
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.mark.parametrize(
    "options",
    [
        {"max_objects": 0},
        {"max_objects": 10001},
        {"max_milliseconds": 0},
        {"max_milliseconds": 1001},
        {"continuation": "bad"},
        {"auto_repair": True, "continuation": "a" * 32},
    ],
)
def test_invalid_scene_validation_budget_avoids_editor_io(monkeypatch, options):
    module = importlib.import_module("services.tools.manage_scene")
    instance = AsyncMock(return_value="instance")
    monkeypatch.setattr(module, "get_unity_instance_from_context", instance)
    result = asyncio.run(module.manage_scene(SimpleNamespace(), action="validate", **options))
    assert result["success"] is False
    assert result["code"] == "invalid_scene_validation"
    instance.assert_not_awaited()


def test_scene_validation_budget_and_continuation_reach_editor(monkeypatch):
    module = importlib.import_module("services.tools.manage_scene")
    monkeypatch.setattr(
        module, "get_unity_instance_from_context", AsyncMock(return_value="instance")
    )
    monkeypatch.setattr(module, "preflight", AsyncMock(return_value=None))
    send = AsyncMock(return_value={"success": True, "data": {"scanComplete": False}})
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    result = asyncio.run(
        module.manage_scene(
            SimpleNamespace(),
            action="validate",
            max_objects="7",
            max_milliseconds="3",
            continuation="a" * 32,
        )
    )
    assert result["data"]["scanComplete"] is False
    assert send.await_args.args[3] == {
        "action": "validate",
        "maxObjects": 7,
        "maxMilliseconds": 3,
        "continuation": "a" * 32,
    }


def test_capability_scene_validate_exposes_budgets():
    from services.registry.capability_catalog import CAPABILITIES

    capability = next(item for item in CAPABILITIES if item.name == "scene_validate")
    assert {"max_objects", "max_milliseconds", "continuation"} <= set(capability.parameters)


@pytest.mark.parametrize("previous_sdk_registration", [False, True])
def test_capability_scene_validate_routes_bounded_request(monkeypatch, previous_sdk_registration):
    from fastmcp import FastMCP
    from services.registry import tool_registry
    from services.registry.capability_adapter import register_capability
    from services.registry.capability_catalog import CAPABILITIES

    module = importlib.import_module("services.tools.manage_scene")
    monkeypatch.setattr(tool_registry, "_tool_registry", list(tool_registry._tool_registry))
    for name in ("__signature__", "__fastmcp__"):
        monkeypatch.setattr(
            module.manage_scene, name, getattr(module.manage_scene, name, None), raising=False
        )
    if previous_sdk_registration:
        FastMCP("previous-scene-source-registration").tool()(module.manage_scene)
    monkeypatch.setattr(
        module, "get_unity_instance_from_context", AsyncMock(return_value="instance")
    )
    monkeypatch.setattr(module, "preflight", AsyncMock(return_value=None))
    send = AsyncMock(return_value={"success": True, "data": {"scanComplete": False}})
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    capability = next(item for item in CAPABILITIES if item.name == "scene_validate")
    adapter = register_capability(capability, module.manage_scene)
    result = asyncio.run(
        adapter(ctx=SimpleNamespace(), action="validate", max_objects=3, max_milliseconds=2)
    )
    assert result["data"]["scanComplete"] is False
    assert send.await_args.args[2] == "scene_validate"
    assert send.await_args.args[3] == {
        "action": "validate",
        "maxObjects": 3,
        "maxMilliseconds": 2,
    }


@pytest.mark.parametrize(
    "options", [{"max_objects": 2}, {"max_milliseconds": 2}, {"continuation": "a" * 32}]
)
def test_non_validation_actions_reject_scene_budgets(monkeypatch, options):
    module = importlib.import_module("services.tools.manage_scene")
    instance = AsyncMock(return_value="instance")
    monkeypatch.setattr(module, "get_unity_instance_from_context", instance)
    result = asyncio.run(module.manage_scene(SimpleNamespace(), action="get_active", **options))
    assert result["success"] is False
    assert result["code"] == "invalid_scene_validation"
    instance.assert_not_awaited()
