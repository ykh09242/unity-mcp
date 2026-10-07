"""Texture budgets through the production registration and real MCP SDK."""

import importlib

import pytest
import pytest_asyncio
from fastmcp import Client, FastMCP
from fastmcp.server.middleware import Middleware

from services.tools import register_all_tools


@pytest_asyncio.fixture
async def texture_sdk(monkeypatch):
    texture = importlib.import_module("services.tools.manage_texture")
    calls = []

    async def preflight(ctx, **kwargs):
        calls.append(("preflight", kwargs))
        return None

    async def send(fn, instance, command, params):
        calls.append(("send", params))
        assert instance == "Texture@fixture" and command == "manage_texture"
        return {"success": True}

    class UnityState(Middleware):
        async def on_call_tool(self, context, call_next):
            await context.fastmcp_context.set_state("unity_instance", "Texture@fixture")
            return await call_next(context)

    monkeypatch.setattr(texture, "preflight", preflight)
    monkeypatch.setattr(texture, "send_with_unity_instance", send)
    server = FastMCP("texture-budgets")
    server.add_middleware(UnityState())
    register_all_tools(server)
    async with Client(server) as client:
        yield client, calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "options",
    [
        {"action": action, "width": 4097, "height": 1}
        for action in ("create", "create_sprite", "apply_pattern", "apply_gradient", "apply_noise")
    ]
    + [
        {"width": 1, "height": 4097},
        {"width": 2147483647, "height": 2147483647},
        {"action": "apply_noise", "width": 4096, "height": 4096, "octaves": 3},
        {"action": "apply_noise", "width": 1, "height": 1, "octaves": 33554433},
        {"action": "apply_noise", "width": 1, "height": 1, "octaves": 2147483647},
        {"action": "modify", "set_pixels": {"width": 4097, "height": 1, "color": [0, 0, 0, 0]}},
        {
            "action": "modify",
            "set_pixels": {"width": 2147483647, "height": 2147483647, "pixels": []},
        },
        {"width": 1, "height": 1, "pixels": "base64:" + "A" * 12},
        {"width": 1, "height": 1, "pixels": "A" * 12},
        {
            "action": "modify",
            "set_pixels": {"width": 1, "height": 1, "pixels": "base64:" + "A" * 12},
        },
    ],
)
async def test_reject_before_preflight_or_unity_send(texture_sdk, options):
    client, calls = texture_sdk
    result = await client.call_tool(
        "manage_texture", {"action": "create", "path": "Assets/Fixture.png", **options}
    )
    assert result.structured_content["success"] is False
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "options",
    [
        {"width": 1, "height": 1, "pixels": "AAAAAA=="},
        {"width": 4096, "height": 4096},
        {"action": "apply_noise", "width": 2048, "height": 2048, "octaves": 8},
        {"action": "apply_noise", "width": 1, "height": 1, "octaves": 33554432},
        {"action": "apply_noise", "width": 4096, "height": 4096, "octaves": 2},
        {"action": "apply_gradient", "width": 2, "height": 2},
        {
            "action": "modify",
            "set_pixels": {"x": -1, "width": 2, "height": 1, "color": [0, 0, 0, 0]},
        },
        {"action": "modify", "import_settings": {"max_texture_size": 16384}},
        {"image_path": "Assets/Fixture.JPEG"},
    ],
)
async def test_allowed_boundaries_preserve_dispatch(texture_sdk, options):
    client, calls = texture_sdk
    result = await client.call_tool(
        "manage_texture", {"action": "create", "path": "Assets/Fixture.png", **options}
    )
    assert result.structured_content["success"] is True
    assert [name for name, _ in calls] == ["preflight", "send"]
    if "import_settings" in options:
        assert calls[-1][1]["importSettings"]["maxTextureSize"] == 16384
