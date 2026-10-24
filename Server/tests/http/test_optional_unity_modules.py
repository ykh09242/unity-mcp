"""Optional engine modules survive bounded native capability composition."""

from types import SimpleNamespace

import pytest

from services.capabilities import query_project_capabilities
from tests.http.test_project_capabilities import install_native_replies, native_observations
from tests.http.test_scoped_capabilities import state
from tests.test_http_command_outcome import command_hub  # pylint: disable=unused-import


@pytest.mark.asyncio
async def test_missing_engine_modules_remain_explicit_without_extra_native_probes(command_hub):
    observations = native_observations()
    project = observations["get_project_info"]
    project["packages"].update(physics=False, physics2d=True, screenCapture=False)
    project["featureReadiness"] = {
        "physics": {
            "supported": False,
            "reason": "missing_engine_module",
            "blocking_reasons": ["missing_engine_module"],
            "tool_enabled": True,
            "next_action": "inspect_module_requirements",
            "required_version": "com.unity.modules.physics and com.unity.modules.physics2d",
            "observed": {"physics3d_module": False, "physics2d_module": True, "unknown": True},
        },
        "screen_capture": {
            "supported": False,
            "reason": "missing_engine_module",
            "blocking_reasons": ["missing_engine_module"],
            "tool_enabled": True,
            "next_action": "use_camera_or_scene_view_capture",
            "observed": {"screen_capture_module": False},
        },
    }
    await install_native_replies(command_hub, observations)
    result = await query_project_capabilities(SimpleNamespace(get_state=state))
    physics = result.features["physics"]
    assert physics.supported is False and physics.ready is False
    assert physics.reason == "missing_engine_module"
    assert physics.blocking_reasons == ["missing_engine_module"]
    assert physics.observed == {"physics3d_module": False, "physics2d_module": True}
    assert physics.next_action == "inspect_module_requirements"
    assert result.features["screen_capture"].next_action == "use_camera_or_scene_view_capture"
    assert result.packages["physics"] is False
    assert result.packages["physics2d"] is True
    assert result.packages["screenCapture"] is False
    assert len([message for message in command_hub.sent if message["type"] == "execute"]) == 5


@pytest.mark.asyncio
async def test_old_editor_module_evidence_stays_unknown_and_scoped_reads_remain_available(
    command_hub,
):
    await install_native_replies(command_hub, native_observations())
    result = await query_project_capabilities(
        SimpleNamespace(get_state=state), features=("physics", "screen_capture")
    )
    for name in ("physics", "screen_capture"):
        feature = result.features[name]
        assert feature.supported is None and feature.ready is None
        assert feature.reason == "not_observed"
        assert feature.observed == {}
    assert result.packages["physics"] is None
    assert sorted(
        message["name"] for message in command_hub.sent if message["type"] == "execute"
    ) == ["get_editor_state", "get_project_info"]
