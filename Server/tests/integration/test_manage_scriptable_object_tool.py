import pytest

from .test_helpers import DummyContext
import services.tools.manage_scriptable_object as mod


@pytest.mark.parametrize("patch", [
    {"path": "items", "op": "array_resize", "value": 2_147_483_648},
    {"path": "items", "op": "array_resize", "value": "9223372036854775808"},
    {"path": "items.Array.size", "value": -1},
    {"path": "items", "op": "array_resize", "value": True},
    {"path": "items", "op": "array_resize", "value": 1e100},
    {"path": "items[2147483647]", "value": 0},
    {"path": "items[-1]", "value": 0},
    {"path": "outer[1].inner[9999999999999999999999]", "value": 0},
])
@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.asyncio
async def test_impossible_array_numbers_fail_before_instance_or_transport(monkeypatch, patch, dry_run):
    async def forbidden(*args):
        pytest.fail("Invalid numeric array request reached routing")

    monkeypatch.setattr(mod, "get_unity_instance_from_context", forbidden)
    monkeypatch.setattr(mod, "send_with_unity_instance", forbidden)
    result = await mod.manage_scriptable_object(ctx=DummyContext(), action="modify", patches=[patch], dry_run=dry_run)
    assert result["success"] is False
    assert "Int32" in result["message"]


@pytest.mark.parametrize("patch", [
    {"path": "items", "op": "array_resize", "value": 1_500_000},
    {"path": "items[1499999]", "value": 0},
    {"path": "items", "op": "array_resize", "value": "4"},
    {"path": "items", "op": "array_resize", "value": 4.75},
    {"path": "items", "op": "array_resize", "value": "4.75"},
    {"path": "items", "op": "array_resize", "value": "4e0"},
    {"path": "nested", "value": {"numbers": [3, 4]}},
])
@pytest.mark.asyncio
async def test_state_dependent_growth_is_forwarded_to_authoritative_unity_budget(monkeypatch, patch):
    captured = {}

    async def instance(ctx):
        return None

    async def send(fn, selected, command, params):
        captured.update(params)
        return {"success": False, "message": "controlled Unity response"}

    monkeypatch.setattr(mod, "get_unity_instance_from_context", instance)
    monkeypatch.setattr(mod, "send_with_unity_instance", send)
    result = await mod.manage_scriptable_object(ctx=DummyContext(), action="modify", patches=[patch])
    assert result["message"] == "controlled Unity response"
    assert captured["patches"] == [patch]


@pytest.mark.parametrize("flag", ["dry_run", "overwrite"])
@pytest.mark.parametrize("value", ["garbage", "tru", ""])
@pytest.mark.asyncio
async def test_invalid_boolean_flag_fails_before_routing(monkeypatch, flag, value):
    calls = []

    async def fake_instance(ctx):
        calls.append("instance")
        return None

    async def fake_send(*args):
        calls.append("send")
        return {"success": True}

    monkeypatch.setattr(mod, "get_unity_instance_from_context", fake_instance)
    monkeypatch.setattr(mod, "send_with_unity_instance", fake_send)
    result = await mod.manage_scriptable_object(ctx=DummyContext(), action="modify", **{flag: value})
    assert result["success"] is False
    assert flag in result["message"]
    assert calls == []


@pytest.mark.parametrize("flag,wire", [("dry_run", "dryRun"), ("overwrite", "overwrite")])
@pytest.mark.parametrize("value,expected", [(None, None), (True, True), (False, False),
                                           ("true", True), ("false", False), ("yes", True), ("0", False)])
@pytest.mark.asyncio
async def test_supported_boolean_flags_preserve_defaults(monkeypatch, flag, wire, value, expected):
    captured = {}

    async def fake_instance(ctx):
        return None

    async def fake_send(fn, instance, command, params):
        captured.update(params)
        return {"success": False, "message": "controlled Unity failure"}

    monkeypatch.setattr(mod, "get_unity_instance_from_context", fake_instance)
    monkeypatch.setattr(mod, "send_with_unity_instance", fake_send)
    result = await mod.manage_scriptable_object(ctx=DummyContext(), action="modify", **{flag: value})
    assert result == {"success": False, "message": "controlled Unity failure"}
    if expected is None:
        assert wire not in captured
    else:
        assert captured[wire] is expected


@pytest.mark.asyncio
async def test_manage_scriptable_object_forwards_create_params(monkeypatch):
    captured = {}

    async def fake_async_send(cmd, params, **kwargs):
        captured["cmd"] = cmd
        captured["params"] = params
        return {"success": True, "data": {"ok": True}}

    monkeypatch.setattr(mod, "async_send_command_with_retry", fake_async_send)

    ctx = DummyContext()
    await ctx.set_state("unity_instance", "UnityMCPTests@dummy")

    result = await (
        mod.manage_scriptable_object(
            ctx=ctx,
            action="create",
            type_name="My.Namespace.TestDefinition",
            folder_path="Assets/Temp/Foo",
            asset_name="Bar",
            overwrite="true",
            patches='[{"propertyPath":"displayName","op":"set","value":"Hello"}]',
        )
    )

    assert result["success"] is True
    assert captured["cmd"] == "manage_scriptable_object"
    assert captured["params"]["action"] == "create"
    assert captured["params"]["typeName"] == "My.Namespace.TestDefinition"
    assert captured["params"]["folderPath"] == "Assets/Temp/Foo"
    assert captured["params"]["assetName"] == "Bar"
    assert captured["params"]["overwrite"] is True
    assert isinstance(captured["params"]["patches"], list)
    assert captured["params"]["patches"][0]["propertyPath"] == "displayName"


@pytest.mark.asyncio
async def test_manage_scriptable_object_forwards_modify_params(monkeypatch):
    captured = {}

    async def fake_async_send(cmd, params, **kwargs):
        captured["cmd"] = cmd
        captured["params"] = params
        return {"success": True, "data": {"ok": True}}

    monkeypatch.setattr(mod, "async_send_command_with_retry", fake_async_send)

    ctx = DummyContext()
    await ctx.set_state("unity_instance", "UnityMCPTests@dummy")

    result = await (
        mod.manage_scriptable_object(
            ctx=ctx,
            action="modify",
            target='{"guid":"abc"}',
            patches=[{"propertyPath": "materials.Array.size", "op": "array_resize", "value": 2}],
        )
    )

    assert result["success"] is True
    assert captured["cmd"] == "manage_scriptable_object"
    assert captured["params"]["action"] == "modify"
    assert captured["params"]["target"] == {"guid": "abc"}
    assert captured["params"]["patches"][0]["op"] == "array_resize"


@pytest.mark.asyncio
async def test_manage_scriptable_object_forwards_dry_run_param(monkeypatch):
    captured = {}

    async def fake_async_send(cmd, params, **kwargs):
        captured["cmd"] = cmd
        captured["params"] = params
        return {"success": True, "data": {"dryRun": True, "validationResults": []}}

    monkeypatch.setattr(mod, "async_send_command_with_retry", fake_async_send)

    ctx = DummyContext()
    await ctx.set_state("unity_instance", "UnityMCPTests@dummy")

    result = await (
        mod.manage_scriptable_object(
            ctx=ctx,
            action="modify",
            target='{"guid":"abc123"}',
            patches=[{"propertyPath": "intValue", "op": "set", "value": 42}],
            dry_run=True,
        )
    )

    assert result["success"] is True
    assert captured["cmd"] == "manage_scriptable_object"
    assert captured["params"]["action"] == "modify"
    assert captured["params"]["dryRun"] is True
    assert captured["params"]["target"] == {"guid": "abc123"}


@pytest.mark.asyncio
async def test_manage_scriptable_object_dry_run_string_coercion(monkeypatch):
    """Test that dry_run accepts string 'true' and coerces to boolean."""
    captured = {}

    async def fake_async_send(cmd, params, **kwargs):
        captured["cmd"] = cmd
        captured["params"] = params
        return {"success": True, "data": {"dryRun": True}}

    monkeypatch.setattr(mod, "async_send_command_with_retry", fake_async_send)

    ctx = DummyContext()
    await ctx.set_state("unity_instance", "UnityMCPTests@dummy")

    result = await (
        mod.manage_scriptable_object(
            ctx=ctx,
            action="modify",
            target={"guid": "xyz"},
            patches=[],
            dry_run="true",  # String instead of bool
        )
    )

    assert result["success"] is True
    assert captured["params"]["dryRun"] is True



