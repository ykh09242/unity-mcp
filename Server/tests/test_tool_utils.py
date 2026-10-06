"""Public value normalization and fake-transport boundary regressions."""

import asyncio
import importlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.tools.utils import coerce_bool, coerce_float, coerce_int, normalize_color, normalize_vector3


@pytest.mark.parametrize("parser, value", [
    (coerce_bool, value) for value in (0, 1, 0.5, [], {}, "0", "1", "yes", "flase")
] + [
    (coerce_int, value) for value in (True, False, 0.0, 1.9, "1.9", "1e2", "", "null", "1_000")
] + [
    (coerce_float, value) for value in (True, False, "NaN", "Infinity", float("nan"), float("inf"), "", "null")
])
def test_explicit_invalid_scalars_never_fall_back(parser, value):
    with pytest.raises(ValueError):
        parser(value)
    with pytest.raises(ValueError):
        parser(value, default=7)


def test_scalar_zero_false_and_exact_integer_controls():
    assert coerce_bool(False, True) is False
    assert coerce_bool(" false ", True) is False
    assert coerce_bool("TRUE") is True
    assert coerce_int(0, 7) == 0
    assert coerce_int("9007199254740993") == 9007199254740993
    assert coerce_int(" -42 ") == -42
    assert coerce_float(0, 7.0) == 0.0
    for parser in (coerce_bool, coerce_int, coerce_float):
        assert parser(None, default=7) == 7


@pytest.mark.parametrize("value", [
    [True, 0, 0], [0, False, 1], {"x": True, "y": 0, "z": 0},
    '[true,0,0]', '{"x":0,"y":false,"z":1}',
])
def test_vector_boolean_components_are_rejected(value):
    vector, error = normalize_vector3(value)
    assert vector is None and error


@pytest.mark.parametrize("value", [
    [True, 0, 0], [0, 0, 0, False], {"r": True, "g": 0, "b": 0},
    '[true,0,0]', '{"r":0,"g":false,"b":1}', [float("nan"), 0, 0],
    '["Infinity",0,0]',
])
def test_color_boolean_and_nonfinite_components_are_rejected(value):
    color, error = normalize_color(value)
    assert color is None and error


def _color_forms(components):
    keys = ("r", "g", "b", "a")[:len(components)]
    mapping = dict(zip(keys, components))
    return [components, tuple(components), mapping, json.dumps(components),
            json.dumps(mapping), "(" + ", ".join(map(str, components)) + ")"]


@pytest.mark.parametrize("value", _color_forms([255, 128, 0]))
def test_byte_rgb_defaults_to_opaque_float_alpha(value):
    assert normalize_color(value) == ([1.0, 128 / 255, 0.0, 1.0], None)


@pytest.mark.parametrize("components", [[1, 0.5, 0], [0, 0, 0], [255, 128, 0]])
def test_rgb_default_alpha_matches_each_output_range(components):
    for value in _color_forms(components):
        color, error = normalize_color(value, "int")
        assert error is None
        assert color == ([255, 128, 0, 255] if any(components) else [0, 0, 0, 255])


@pytest.mark.parametrize("components, expected", [
    ([0, 0, 0, 0], [0.0, 0.0, 0.0, 0.0]),
    ([1, 0.5, 0, 0.25], [1.0, 0.5, 0.0, 0.25]),
    ([255, 128, 0, 64], [1.0, 128 / 255, 0.0, 64 / 255]),
    ([255, 0, 0, 1], [1.0, 0.0, 0.0, 1 / 255]),
])
def test_explicit_alpha_keeps_existing_range_inference(components, expected):
    for value in _color_forms(components):
        assert normalize_color(value) == (expected, None)


@pytest.mark.parametrize("value, expected", [
    (None, None), (0, 0.0), ("0", 0.0), (" 2.5 ", 2.5),
])
def test_float_coercion_controls(value, expected):
    assert coerce_float(value) == expected


@pytest.mark.parametrize("value", [10**400, -(10**400)])
def test_float_overflow_is_invalid_even_with_default(value):
    with pytest.raises(ValueError):
        coerce_float(value)
    with pytest.raises(ValueError):
        coerce_float(value, 7.5)


@pytest.mark.parametrize("value", [
    [10**400, 0, 0], (0, -(10**400), 0), {"x": 0, "y": 0, "z": 10**400},
    json.dumps([10**400, 0, 0]), json.dumps({"x": 0, "y": 10**400, "z": 0}),
])
def test_vector_overflow_returns_validation_error(value):
    vector, error = normalize_vector3(value, "position")
    assert vector is None
    assert "position" in error and "numbers" in error


@pytest.mark.parametrize("value", _color_forms([10**400, 0, 0])[:5])
@pytest.mark.parametrize("output_range", ["float", "int"])
def test_color_overflow_returns_validation_error(value, output_range):
    color, error = normalize_color(value, output_range)
    assert color is None
    assert error


@pytest.fixture(scope="function")
def fake_tool_transport(monkeypatch):
    calls = []
    for name in ("manage_material", "manage_gameobject", "manage_camera", "manage_texture"):
        module = importlib.import_module("services.tools." + name)
        monkeypatch.setattr(module, "get_unity_instance_from_context", AsyncMock(return_value="fixture"))

        async def send(send_fn, instance, tool_name, params):
            calls.append((tool_name, params))
            return {"success": True, "message": "fixture"}

        monkeypatch.setattr(module, "send_with_unity_instance", send)
        if hasattr(module, "preflight"):
            monkeypatch.setattr(module, "preflight", AsyncMock(return_value=None))
    return calls


@pytest.mark.parametrize("value", _color_forms([255, 128, 0]))
def test_material_tool_sends_opaque_byte_rgb(value, fake_tool_transport):
    module = importlib.import_module("services.tools.manage_material")
    result = asyncio.run(module.manage_material(SimpleNamespace(), action="set_material_color", color=value))
    assert result["success"] is True
    assert fake_tool_transport == [("manage_material", {
        "action": "set_material_color", "color": [1.0, 128 / 255, 0.0, 1.0],
    })]


@pytest.mark.parametrize("tool_name, kwargs, field", [
    ("manage_material", {"action": "set_material_color", "color": json.dumps([10**400, 0, 0])}, "color"),
    ("manage_gameobject", {"action": "modify", "target": "Fixture", "position": json.dumps([10**400, 0, 0])}, "position"),
    ("manage_camera", {"action": "screenshot", "orbit_distance": 10**400}, "orbit_distance"),
    ("manage_camera", {"action": "screenshot", "orbit_elevations": [10**400]}, "orbit_elevations"),
    ("manage_camera", {"action": "screenshot", "orbit_elevations": "[true, 0]"}, "orbit_elevations"),
    ("manage_texture", {"action": "create", "path": "Assets/Fixture.png", "fill_color": json.dumps([10**400, 0, 0])}, "color"),
])
def test_tool_overflow_returns_error_before_transport(tool_name, kwargs, field, fake_tool_transport):
    module = importlib.import_module("services.tools." + tool_name)
    result = asyncio.run(getattr(module, tool_name)(SimpleNamespace(), **kwargs))
    assert result["success"] is False
    assert field in result["message"]
    assert fake_tool_transport == []


@pytest.mark.parametrize("value, expected", [
    ([1, 0.5, 0], [1.0, 0.5, 0.0, 1.0]),
    ("[0, 0, 0]", [0.0, 0.0, 0.0, 1.0]),
    ({"r": 255, "g": 0, "b": 0, "a": 0}, [1.0, 0.0, 0.0, 0.0]),
    ("[255, 0, 0, 1]", [1.0, 0.0, 0.0, 1 / 255]),
    (None, None),
])
def test_material_tool_color_controls(value, expected, fake_tool_transport):
    module = importlib.import_module("services.tools.manage_material")
    result = asyncio.run(module.manage_material(SimpleNamespace(), action="set_material_color", color=value))
    assert result["success"] is True
    params = fake_tool_transport[0][1]
    if expected is None:
        assert "color" not in params
    else:
        assert params["color"] == expected


def test_material_tool_empty_color_rejected_before_transport(fake_tool_transport):
    module = importlib.import_module("services.tools.manage_material")
    result = asyncio.run(module.manage_material(SimpleNamespace(), action="set_material_color", color=""))
    assert result["success"] is False
    assert fake_tool_transport == []


@pytest.mark.parametrize("value", [[0, -2, 3.5], {"x": 0, "y": "-2", "z": 3.5}, "0,-2,3.5"])
def test_vector_numeric_and_string_controls(value):
    assert normalize_vector3(value) == ([0.0, -2.0, 3.5], None)


def test_null_empty_and_hex_controls():
    assert normalize_vector3(None) == (None, None)
    assert normalize_vector3("")[0] is None
    assert normalize_color(None) == (None, None)
    assert normalize_color("")[0] is None
    assert normalize_color("#ff8000") == ([1.0, 128 / 255, 0.0, 1.0], None)
    assert normalize_color("#ff800000") == ([1.0, 128 / 255, 0.0, 0.0], None)
