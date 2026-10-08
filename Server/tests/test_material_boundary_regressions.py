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

    def invoke(parameters):
        context = SimpleNamespace(
            fastmcp_context=SimpleNamespace(fastmcp=server),
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
