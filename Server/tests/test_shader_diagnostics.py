"""Dedicated shader observations use bounded inputs and explicit compilation targets."""

from unittest.mock import AsyncMock

import pytest

from services.tools import manage_shader as shader


@pytest.mark.parametrize("native", [False, True])
def test_shader_diagnostic_permissions_follow_effects(native):
    from services.operation_permissions import Permission, required_permissions

    assert required_permissions("manage_shader", {"action": "diagnostics"}, native=native) == {
        Permission.READ
    }
    assert required_permissions("manage_shader", {"action": "compile_variant"}, native=native) == {
        Permission.RUN
    }


@pytest.fixture
def dispatch(monkeypatch):
    lookup = AsyncMock(return_value="Fixture@shader")
    send = AsyncMock(return_value={"success": True, "data": {"status": "observed"}})
    monkeypatch.setattr(shader, "get_unity_instance_from_context", lookup)
    monkeypatch.setattr(shader, "send_with_unity_instance", send)
    return lookup, send


@pytest.mark.asyncio
async def test_diagnostics_accepts_a_full_shader_path_without_a_name(dispatch):
    _, send = dispatch
    result = await shader.manage_shader(None, "diagnostics", path="Assets/Fixture.shadergraph")
    assert result["success"]
    assert send.call_args.args[3] == {"action": "diagnostics", "path": "Assets/Fixture.shadergraph"}


@pytest.mark.asyncio
async def test_variant_compilation_preserves_explicit_selection_and_disables_replay(dispatch):
    _, send = dispatch
    variant = {
        "shader_type": "Vertex",
        "platform": "D3D",
        "build_target": "StandaloneWindows64",
        "keywords": ["FEATURE_ON"],
    }
    result = await shader.manage_shader(
        None, "compile_variant", path="Assets/Fixture.shader", variant=variant
    )
    assert result["success"]
    assert send.call_args.args[1] == "Fixture@shader"
    assert send.call_args.args[3]["variant"] == {**variant, "subshader_index": 0, "pass_index": 0}
    assert send.call_args.kwargs["retry_on_reload"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "variant",
    [
        None,
        {},
        {"platform": "D3D"},
        {
            "shader_type": "Vertex",
            "platform": "D3D",
            "build_target": "StandaloneWindows64",
            "keywords": ["X"] * 65,
        },
    ],
)
async def test_invalid_variant_is_rejected_before_editor_lookup(dispatch, variant):
    lookup, send = dispatch
    result = await shader.manage_shader(
        None, "compile_variant", path="Assets/Fixture.shader", variant=variant
    )
    assert result["success"] is False
    lookup.assert_not_awaited()
    send.assert_not_awaited()
