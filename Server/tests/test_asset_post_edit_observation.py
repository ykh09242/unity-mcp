"""Explicit asset observations preserve the single original mutation call."""

import asyncio
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def test_addressables_preflight_is_explicit_and_one_call(monkeypatch):
    module = importlib.import_module("services.tools.manage_addressables")
    monkeypatch.setattr(module, "get_unity_instance_from_context", AsyncMock(return_value="owner"))
    result = {"success": True, "data": {"changed": True, "verification": {"status": "incomplete"}}}
    sender = AsyncMock(return_value=result)
    monkeypatch.setattr(module, "send_with_unity_instance", sender)
    observed = asyncio.run(
        module.manage_addressables(
            SimpleNamespace(),
            action="set_address",
            settings_path="Assets/Settings.asset",
            entry_guid="a" * 32,
            address="new",
            expected_revision="addr1:" + "b" * 64,
            include_preflight=True,
        )
    )
    assert observed == result
    sender.assert_awaited_once()
    assert sender.await_args.args[3]["include_preflight"] is True


@pytest.mark.parametrize("action", ["inspect", "preflight", "analyze_layout"])
def test_addressables_observation_option_refuses_read_actions(monkeypatch, action):
    module = importlib.import_module("services.tools.manage_addressables")
    sender = AsyncMock()
    monkeypatch.setattr(module, "send_with_unity_instance", sender)
    observed = asyncio.run(
        module.manage_addressables(SimpleNamespace(), action=action, include_preflight=True)
    )
    assert observed["success"] is False
    sender.assert_not_awaited()


def test_blender_import_verification_does_not_repeat_import(monkeypatch):
    module = importlib.import_module("services.tools.blender_bridge")
    monkeypatch.setattr(module, "get_unity_instance_from_context", AsyncMock(return_value="owner"))
    result = {
        "success": True,
        "data": {"asset_path": "Assets/Model.prefab", "verification": {"status": "issues_found"}},
    }
    sender = AsyncMock(return_value=result)
    monkeypatch.setattr(module, "send_with_unity_instance", sender)
    observed = asyncio.run(
        module.blender_bridge(SimpleNamespace(), action="import_model", verify_import=True)
    )
    assert observed == result
    sender.assert_awaited_once()
    assert sender.await_args.args[3]["verifyImport"] is True
