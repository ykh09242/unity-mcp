"""Focused Timeline schema and exact authoring/preview envelopes."""

import asyncio
import importlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from core.logging_decorator import log_execution
from core.telemetry_decorator import telemetry_tool


@pytest.fixture
def wire(monkeypatch):
    def connect():
        assert importlib.util.find_spec("services.tools.manage_timeline") is not None, (
            "manage_timeline public tool is missing"
        )
        module = importlib.import_module("services.tools.manage_timeline")
        sender = AsyncMock(return_value={"success": True, "data": {"revision": "fresh"}})
        monkeypatch.setattr(
            module, "get_unity_instance_from_context", AsyncMock(return_value="fixture")
        )
        monkeypatch.setattr(module, "send_with_unity_instance", sender)
        return module, sender

    return connect


def edit(**changes):
    return {
        "action": "add_clip",
        "path": "Assets/Cutscene.playable",
        "guid": "a" * 32,
        "file_id": "11400000",
        "expected_revision": "fresh",
        "properties": {
            "track_id": "11400001",
            "source": {"guid": "b" * 32, "file_id": "7400000"},
            "start": 0,
            "duration": 1,
            "clip_in": 0,
            "time_scale": 1,
        },
        **changes,
    }


def test_timeline_exact_identity_and_zero_timing_survive_transport(wire):
    # Given an exact saved track/source identity and valid zero timing values.
    module, sender = wire()
    values = edit()
    # When the public authoring call crosses its boundary.
    result = asyncio.run(module.manage_timeline(SimpleNamespace(), **values))
    # Then all selectors/values survive unchanged.
    assert result["success"] is True
    assert sender.call_args.args[2] == "manage_timeline"
    assert sender.call_args.args[3] == values


@pytest.mark.parametrize(
    "paging",
    [
        {"offset": 2**31},
        {"offset": -1},
        {"offset": True},
        {"clip_offset": 2**31, "track_id": "1"},
        {"clip_offset": 1},
        {"clip_offset": 1, "track_id": "0"},
        {"clip_page_size": 33},
        {"offset": 1, "track_id": "1"},
    ],
)
def test_invalid_large_timeline_page_does_not_reach_unity(wire, paging):
    module, sender = wire()
    result = asyncio.run(
        module.manage_timeline(
            SimpleNamespace(),
            action="read",
            path="Assets/Cutscene.playable",
            expected_revision="revision",
            **paging,
        )
    )
    assert result["success"] is False
    sender.assert_not_called()


@pytest.mark.parametrize(
    "paging",
    [
        {"offset": 64000, "page_size": 32},
        {"track_id": "11400001", "clip_offset": 512, "clip_page_size": 32},
    ],
)
def test_large_timeline_continuation_preserves_exact_page_selectors(wire, paging):
    module, sender = wire()
    values = {
        "action": "read",
        "path": "Assets/Cutscene.playable",
        "expected_revision": "revision",
        **paging,
    }
    result = asyncio.run(module.manage_timeline(SimpleNamespace(), **values))
    assert result["success"] is True
    assert sender.call_args.args[3] == values


@pytest.mark.parametrize(
    "values",
    [
        edit(expected_revision=None),
        edit(file_id="0"),
        edit(
            properties={
                "track_id": "1",
                "source": {"path": "Assets/A.anim"},
                "start": 0,
                "duration": 1,
            }
        ),
        edit(
            properties={
                "track_id": "1",
                "source": {"guid": "b" * 32, "file_id": "1"},
                "start": True,
                "duration": 1,
            }
        ),
        edit(
            properties={
                "track_id": "1",
                "source": {"guid": "b" * 32, "file_id": "1"},
                "start": 0,
                "duration": -1,
            }
        ),
        edit(
            properties={
                "track_id": "1",
                "source": {"guid": "b" * 32, "file_id": "1"},
                "start": 0,
                "duration": float("nan"),
            }
        ),
        {"action": "seek", "preview_id": "session", "time": True},
        {"action": "seek", "preview_id": "session", "time": -1},
        {"action": "preview_stop"},
        {"action": "read_director", "target": True, "scene_handle": 1},
        {"action": "read", "path": "Assets/Cutscene.playable", "offset": 80},
        {
            "action": "read",
            "path": "Assets/Cutscene.playable",
            "track_id": "11400001",
            "clip_offset": 600,
        },
        {"action": "read_director", "target": 17, "scene_handle": 22, "offset": 80},
    ],
)
def test_invalid_timeline_request_never_reaches_native_mutation(wire, values):
    # Given malformed authoring/preview inputs.
    module, sender = wire()
    # When complete boundary validation runs, then nothing is dispatched.
    result = asyncio.run(module.manage_timeline(SimpleNamespace(), **values))
    assert result["success"] is False
    sender.assert_not_called()


@pytest.mark.asyncio
async def test_registered_timeline_sdk_rejects_identity_coercion_and_preserves_preview(wire):
    # Given the actual production decorators and SDK.
    module, sender = wire()
    server = FastMCP("timeline")
    wrapped = telemetry_tool("manage_timeline")(
        log_execution("manage_timeline", "Tool")(module.manage_timeline)
    )
    server.tool(name="manage_timeline")(wrapped)
    async with Client(server, mode="2026-07-28") as client:
        # When exact authoring and session requests cross the registered schema.
        for values in [
            edit(),
            {"action": "capabilities"},
            {"action": "preview_stop", "preview_id": "session"},
            {"action": "seek", "preview_id": "session", "time": 0.5},
            {"action": "read_director", "target": 17, "scene_handle": 22},
            {"action": "create", "path": "Assets/New.playable", "properties": {"name": "New"}},
            {"action": "read", "path": "Assets/New.playable", "page_size": 1},
            {
                "action": "read",
                "path": "Assets/New.playable",
                "offset": 64000,
                "expected_revision": "fresh",
            },
            {
                "action": "read",
                "path": "Assets/New.playable",
                "track_id": "11400001",
                "clip_offset": 600,
                "clip_page_size": 32,
                "expected_revision": "fresh",
            },
            {
                "action": "read_director",
                "target": 17,
                "scene_handle": 22,
                "offset": 80,
                "expected_director_revision": "director",
            },
            edit(action="add_track", properties={"kind": "animation", "name": "Motion"}),
            edit(
                action="assign",
                properties=None,
                target=17,
                scene_handle=22,
                expected_director_revision="director",
            ),
            edit(
                action="bind",
                properties={"track_id": "11400001", "binding_target": 30},
                target=17,
                scene_handle=22,
                expected_director_revision="director",
            ),
            edit(
                action="preview_start",
                properties=None,
                target=17,
                scene_handle=22,
                expected_director_revision="director",
            ),
        ]:
            result = await client.call_tool("manage_timeline", values)
            # Then identity and token/time fields survive the real surface.
            assert json.loads(result.content[0].text)["success"] is True
            assert sender.call_args.args[3] == {
                key: value for key, value in values.items() if value is not None
            }
        sender.reset_mock()
        for values in (
            edit(file_id=11400000),
            {"action": "read_director", "target": True, "scene_handle": 1},
            {"action": "read", "path": "Assets/New.playable", "track_id": 11400001},
            {"action": "read", "path": "Assets/New.playable", "clip_offset": True},
        ):
            with pytest.raises(ToolError):
                await client.call_tool("manage_timeline", values)
        sender.assert_not_called()
