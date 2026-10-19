"""Sentinel readiness belongs to the selected project's configured registry."""

import asyncio
import importlib
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

scripts = importlib.import_module("services.tools.manage_script")
legacy = importlib.import_module("transport.legacy.unity_connection")


@pytest.mark.asyncio
@pytest.mark.parametrize("selected_reloading", [False, True])
async def test_sentinel_ignores_newest_foreign_status(tmp_path, monkeypatch, selected_reloading):
    home = tmp_path / "home"
    global_registry = home / ".unity-mcp"
    global_registry.mkdir(parents=True)
    registry = tmp_path / "isolated-registry"
    registry.mkdir()
    selected = registry / "unity-mcp-status-01234567.json"
    foreign = global_registry / "unity-mcp-status-89abcdef.json"
    selected.write_text(json.dumps({"reloading": selected_reloading}), encoding="utf-8")
    foreign.write_text(json.dumps({"reloading": not selected_reloading}), encoding="utf-8")
    os.utime(selected, (1, 1))
    os.utime(foreign, (2, 2))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("UNITY_MCP_STATUS_DIR", str(registry))
    monkeypatch.setattr(
        os.path,
        "expanduser",
        lambda value: str(home / value[2:]) if value.startswith("~/") else value,
    )
    monkeypatch.setattr(
        scripts, "get_unity_instance_from_context", AsyncMock(return_value="Game@01234567")
    )
    monkeypatch.setattr(scripts, "send_mutation", AsyncMock(return_value={"success": True}))
    dispatch = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(legacy, "async_send_command_with_retry", dispatch)
    result = await scripts.apply_text_edits(
        AsyncMock(),
        "Assets/Foo.cs",
        [{"startLine": 1, "startCol": 1, "endLine": 1, "endCol": 1, "newText": "// test"}],
        options={"force_sentinel_reload": True},
    )
    assert result["success"]
    await asyncio.gather(*scripts._background_tasks)
    assert dispatch.await_count == (0 if selected_reloading else 1)
    if not selected_reloading:
        assert dispatch.call_args.kwargs["instance_id"] == "Game@01234567"


def test_status_reader_uses_configured_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("UNITY_MCP_STATUS_DIR", str(tmp_path))
    (tmp_path / "unity-mcp-status-01234567.json").write_text(
        '{"reloading": true}', encoding="utf-8"
    )
    assert legacy.read_status_file("01234567") == {"reloading": True}
