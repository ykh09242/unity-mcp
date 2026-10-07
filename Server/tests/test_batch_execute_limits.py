import importlib
from unittest.mock import AsyncMock, Mock

import pytest

from models.models import MCPResponse


@pytest.fixture
def batch_module(monkeypatch):
    module = importlib.import_module("services.tools.batch_execute")
    module.invalidate_cached_max_commands()
    monkeypatch.setattr(module.config, "transport_mode", "http")
    monkeypatch.setattr(module.config, "http_remote_hosted", False)
    monkeypatch.setattr(
        module,
        "get_unity_instance_from_context",
        AsyncMock(
            side_effect=lambda ctx: ctx.instance,
        ),
    )
    yield module
    module.invalidate_cached_max_commands()


class Context:
    def __init__(self, instance, user="user-a"):
        self.instance = instance
        self.get_state = AsyncMock(
            side_effect=lambda key: {
                "user_id": user,
                "unity_session_id": "owned-session:" + str(instance),
            }.get(key)
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "options",
    [
        {"parallel": 1},
        {"fail_fast": 0},
        {"parallel": "yes"},
        {"max_parallelism": True},
        {"max_parallelism": 1.5},
        {"max_parallelism": "1.9"},
    ],
)
async def test_invalid_batch_scalars_fail_before_editor_lookup(batch_module, monkeypatch, options):
    sends, _ = install_editor(monkeypatch, batch_module, {"A@hash-a": 3})
    with pytest.raises(ValueError):
        await batch_module.batch_execute(Context("A@hash-a"), [{"tool": "read_console"}], **options)
    assert sends == []


@pytest.mark.asyncio
async def test_batch_preserves_false_zero_and_native_command_parameter_keys(
    batch_module, monkeypatch
):
    sends, _ = install_editor(monkeypatch, batch_module, {"A@hash-a": 3})
    commands = [{"tool": "manage_gameobject", "params": {"setActive": False, "layer": 0}}]
    await batch_module.batch_execute(
        Context("A@hash-a"), commands, parallel=False, fail_fast=False, max_parallelism=0
    )
    assert sends[-1][2] == {
        "commands": commands,
        "parallel": False,
        "failFast": False,
        "maxParallelism": 0,
    }


def install_editor(monkeypatch, module, limits):
    sends = []
    resource_calls = []

    async def resource(ctx):
        resource_calls.append(ctx.instance)
        return MCPResponse(
            success=True, data={"settings": {"batch_execute_max_commands": limits[ctx.instance]}}
        )

    async def send(_send_fn, instance, command, params):
        sends.append((instance, command, params))
        if command == "get_editor_state":
            return {
                "success": True,
                "data": {"settings": {"batch_execute_max_commands": limits[instance]}},
            }
        return {"success": True, "data": {"executed": len(params["commands"])}}

    monkeypatch.setattr("services.resources.editor_state.get_editor_state", resource)
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    return sends, resource_calls


@pytest.mark.asyncio
async def test_batch_limits_follow_selected_unity_instance(batch_module, monkeypatch):
    sends, _ = install_editor(monkeypatch, batch_module, {"A@hash-a": 1, "B@hash-b": 3})
    command = {"tool": "read_console", "params": {}}
    await batch_module.batch_execute(Context("A@hash-a"), [command])

    result = await batch_module.batch_execute(Context("B@hash-b"), [command] * 3)

    assert result["success"] is True
    assert sends[-1][0] == "B@hash-b"


@pytest.mark.asyncio
async def test_batch_limit_cache_refreshes_after_settings_change(batch_module, monkeypatch):
    now = [0.0]
    monkeypatch.setattr(batch_module, "time", Mock(monotonic=lambda: now[0]), raising=False)
    limits = {"A@hash-a": 1}
    install_editor(monkeypatch, batch_module, limits)
    command = {"tool": "read_console"}
    ctx = Context("A@hash-a")
    await batch_module.batch_execute(ctx, [command])
    limits[ctx.instance] = 2
    now[0] = 60.0

    result = await batch_module.batch_execute(ctx, [command, command])

    assert result["success"] is True


@pytest.mark.asyncio
async def test_batch_limit_lookup_avoids_enriched_resource_and_reuses_cached_state(
    batch_module, monkeypatch
):
    sends, resource_calls = install_editor(monkeypatch, batch_module, {"A@hash-a": 3})
    ctx = Context("A@hash-a")
    await batch_module.batch_execute(ctx, [{"tool": "read_console"}])
    await batch_module.batch_execute(ctx, [{"tool": "read_console"}])

    assert resource_calls == []
    assert [command for _, command, _ in sends] == [
        "get_editor_state",
        "batch_execute",
        "batch_execute",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "commands",
    [
        [],
        [None],
        [{"tool": "read_console", "params": []}],
        [{"tool": "read_console", "params": {"unity_instance": "B"}}],
    ],
)
async def test_invalid_batch_commands_are_rejected_before_any_unity_request(
    batch_module, monkeypatch, commands
):
    sends, resource_calls = install_editor(monkeypatch, batch_module, {"A@hash-a": 3})

    with pytest.raises(ValueError):
        await batch_module.batch_execute(Context("A@hash-a"), commands)

    assert sends == []
    assert resource_calls == []


@pytest.mark.asyncio
async def test_batch_limit_cache_is_isolated_between_users(batch_module, monkeypatch):
    limits = {"A@hash-a": 1}
    sends, _ = install_editor(monkeypatch, batch_module, limits)
    await batch_module.batch_execute(Context("A@hash-a", "user-a"), [{"tool": "read_console"}])
    limits["A@hash-a"] = 3

    result = await batch_module.batch_execute(
        Context("A@hash-a", "user-b"), [{"tool": "read_console"}] * 3
    )

    assert result["success"] is True
    assert sum(command == "get_editor_state" for _, command, _ in sends) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_limit", [True, False, 0, -1, 101, "3", None])
async def test_malformed_editor_batch_limits_use_default(batch_module, monkeypatch, invalid_limit):
    install_editor(monkeypatch, batch_module, {"A@hash-a": invalid_limit})
    result = await batch_module.batch_execute(Context("A@hash-a"), [{"tool": "read_console"}] * 2)
    assert result["success"] is True


@pytest.mark.asyncio
async def test_unresolved_instance_is_not_cached(batch_module, monkeypatch):
    limits = {None: 1}
    sends, _ = install_editor(monkeypatch, batch_module, limits)
    ctx = Context(None)
    await batch_module.batch_execute(ctx, [{"tool": "read_console"}])
    limits[None] = 2
    result = await batch_module.batch_execute(ctx, [{"tool": "read_console"}] * 2)
    assert result["success"] is True
    assert sum(command == "get_editor_state" for _, command, _ in sends) == 2


@pytest.mark.asyncio
async def test_batch_limit_cache_has_bounded_capacity(batch_module, monkeypatch):
    limits = {f"Project@hash-{i}": 3 for i in range(129)}
    sends, _ = install_editor(monkeypatch, batch_module, limits)
    for instance in limits:
        await batch_module.batch_execute(Context(instance), [{"tool": "read_console"}])
    assert len(batch_module._cached_max_commands) == 128
    await batch_module.batch_execute(Context("Project@hash-0"), [{"tool": "read_console"}])
    assert sum(command == "get_editor_state" for _, command, _ in sends) == 130
