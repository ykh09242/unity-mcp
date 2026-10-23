"""Material local validation must complete before Unity discovery."""

import copy
import importlib
from types import SimpleNamespace

from fastmcp import FastMCP
import pytest

from transport.unity_instance_middleware import UnityInstanceMiddleware


def inline(coroutine):
    """Run only coroutines that complete without an event loop."""
    try:
        coroutine.send(None)
    except StopIteration as complete:
        return complete.value
    coroutine.close()
    raise AssertionError("Unexpected coroutine suspension")


@pytest.fixture
def registered_material(monkeypatch):
    module = importlib.import_module("services.tools.manage_material")
    server = FastMCP("material-preflight-regression")
    tool = server.add_tool(module.manage_material)
    events = []
    response = {
        "success": False,
        "message": "Native rejection",
        "data": {"zero": 0, "false": False, "null": None},
    }

    async def lookup(name):
        assert name == tool.name
        return tool

    async def discover(_ctx):
        events.append("discover")
        return "Fixture@owned"

    async def send(_sender, instance, command, parameters):
        assert instance == "Fixture@owned" and command == "manage_material"
        events.append(copy.deepcopy(parameters))
        return response

    async def inject(*_args, **_kwargs):
        return None

    async def next_call(context):
        return await tool.fn(None, **context.message.arguments)

    monkeypatch.setattr(server, "get_tool", lookup)
    monkeypatch.setattr(module, "get_unity_instance_from_context", discover)
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    middleware = UnityInstanceMiddleware()
    monkeypatch.setattr(middleware, "_inject_unity_instance", inject)

    async def get_state(_key):
        return None

    def invoke(parameters):
        context = SimpleNamespace(
            fastmcp_context=SimpleNamespace(
                fastmcp=server, request_context=None, get_state=get_state
            ),
            message=SimpleNamespace(name=tool.name, arguments=parameters),
        )
        return inline(middleware.on_call_tool(context, next_call))

    return invoke, events, response


@pytest.mark.parametrize(
    "field,value",
    [
        ("color", "[]"),
        ("color", "undefined"),
        ("properties", "[]"),
        ("properties", "null"),
        ("value", "undefined"),
        ("value", "[object Object]"),
        ("render_state", "[]"),
        ("render_state", "null"),
    ],
)
def test_invalid_material_inputs_return_local_error_before_discovery(
    registered_material, field, value
):
    invoke, events, _response = registered_material
    result = invoke({"action": "create", "material_path": "Assets/Fixture.mat", field: value})
    assert result["success"] is False and field in result["message"]
    assert events == []


@pytest.mark.parametrize(
    "value", [False, 0, "", None, [0, -1, 2, 0], '{"find":"Assets/Fixture.png"}']
)
def test_valid_material_values_keep_wire_and_native_failure(registered_material, value):
    invoke, events, response = registered_material
    result = invoke(
        {
            "action": "set_material_shader_property",
            "material_path": "Assets/Fixture.mat",
            "property": "_Value",
            "value": value,
            "slot": 0,
        }
    )
    expected = {
        "action": "set_material_shader_property",
        "materialPath": "Assets/Fixture.mat",
        "property": "_Value",
        "slot": 0,
    }
    if value is not None:
        expected["value"] = (
            {"find": "Assets/Fixture.png"}
            if isinstance(value, str) and value.startswith("{")
            else value
        )
    assert events == ["discover", expected]
    assert result is response


def test_valid_material_properties_preserve_falsy_nested_values(registered_material):
    invoke, events, response = registered_material
    properties = {"enabled": False, "count": 0, "reference": None, "empty": []}
    result = invoke(
        {"action": "create", "material_path": "", "shader": "", "properties": properties}
    )
    assert events == [
        "discover",
        {"action": "create", "materialPath": "", "shader": "", "properties": properties},
    ]
    assert result is response


def test_render_state_patch_preserves_falsy_values_and_native_rejection(registered_material):
    # Given native-owned validation, including an explicitly invalid null queue.
    invoke, events, response = registered_material
    patch = {"render_queue": None, "keywords": {"FIXTURE_LOCAL": False}, "empty": 0}
    # When called through registered middleware.
    result = invoke(
        {
            "action": "set_render_state",
            "target": "Fixture",
            "slot": 0,
            "mode": "instance",
            "render_state": patch,
        }
    )
    # Then native validation sees the exact patch and its result returns unchanged.
    assert events == [
        "discover",
        {
            "action": "set_render_state",
            "target": "Fixture",
            "slot": 0,
            "mode": "instance",
            "renderState": patch,
        },
    ]
    assert result is response


@pytest.mark.parametrize("native", [False, True])
def test_render_state_read_and_edit_admission_remain_distinct(native):
    from services.operation_permissions import (
        OperationGrants,
        OperationPermissionError,
        Permission,
        authorize_operation,
        required_permissions,
    )

    # Given a requester or plugin with only read permission.
    authority = OperationGrants(frozenset({Permission.READ}), scoped=True)
    # When render-state actions are admitted at either public or native boundary.
    authorize_operation(authority, "manage_material", {"action": "get_render_state"}, native=native)
    # Then inspection needs read permission and mutation cannot pass the read grant.
    assert required_permissions(
        "manage_material", {"action": "get_render_state"}, native=native
    ) == {Permission.READ}
    assert required_permissions(
        "manage_material", {"action": "set_render_state"}, native=native
    ) == {Permission.EDIT}
    with pytest.raises(OperationPermissionError):
        authorize_operation(
            authority, "manage_material", {"action": "set_render_state"}, native=native
        )
