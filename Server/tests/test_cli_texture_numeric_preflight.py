"""Texture integer inputs retain receiver types/defaults before command admission."""

import copy
import importlib
import json
import socket

import httpx
import pytest
from click.testing import CliRunner


@pytest.fixture
def dispatch(monkeypatch):
    """Capture actual Click admission and JSON encoding with all networking denied."""

    def denied(*_args, **_kwargs):
        raise AssertionError("Network or token access prohibited")

    for name in ("connect", "connect_ex", "bind", "listen"):
        monkeypatch.setattr(socket.socket, name, denied)
    for name in ("socketpair", "create_connection", "create_server"):
        monkeypatch.setattr(socket, name, denied)
    monkeypatch.setattr(httpx.Client, "request", denied)
    monkeypatch.setattr(httpx.AsyncClient, "request", denied)
    monkeypatch.setenv("UNITY_MCP_DISABLE_TELEMETRY", "true")
    connection = importlib.import_module("cli.utils.connection")
    monkeypatch.setattr(connection, "read_local_auth_token", denied)
    state = {"calls": [], "encoded": [], "failure": False}

    def capture(tool, params, _config):
        state["calls"].append((tool, copy.deepcopy(params)))
        payload = httpx.Request(
            "POST", "http://127.0.0.1/api/command", json={"type": tool, "params": params}
        ).read()
        state["encoded"].append(json.loads(payload))
        if state["failure"]:
            raise connection.UnityCommandError(
                {
                    "success": False,
                    "error": "Owned native rejection",
                    "data": {"zero": 0, "false": False, "null": None},
                }
            )
        return {"success": True, "data": {"zero": 0, "false": False, "null": None}}

    texture = importlib.import_module("cli.commands.texture")
    monkeypatch.setattr(texture, "run_command", capture)
    return importlib.import_module("cli.main").cli, state


BAD_INTEGERS = [1.9, 1.0, True, False, [], {}, "1.0", "1e0", 2147483648, -2147483649]
IMPORT_INTEGERS = (
    ("aniso_level", "anisoLevel"),
    ("max_texture_size", "maxTextureSize"),
    ("compression_quality", "compressionQuality"),
    ("sprite_extrude", "spriteExtrude"),
)


@pytest.mark.parametrize("field", ["x", "y", "width", "height"])
@pytest.mark.parametrize("bad", BAD_INTEGERS)
@pytest.mark.parametrize("output_format", ["json", "text", "table"])
def test_region_integer_rejects_lossy_or_malformed_values_before_transport(
    dispatch, field, bad, output_format
):
    cli, state = dispatch
    value = {"color": [255, 0, 0], field: bad}
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            output_format,
            "texture",
            "modify",
            "Assets/Owned.png",
            "--set-pixels",
            json.dumps(value),
        ],
    )
    assert result.exit_code == 2, (result.exception, result.stdout, result.stderr, state)
    assert result.stdout == "" and "--set-pixels" in result.stderr and field in result.stderr
    assert state["calls"] == [] and state["encoded"] == []


@pytest.mark.parametrize("field", ["width", "height"])
@pytest.mark.parametrize("bad", BAD_INTEGERS)
def test_pixel_array_dimensions_reject_before_count_or_color_conversion(dispatch, field, bad):
    cli, state = dispatch
    value = {"width": 1, "height": 1, "pixels": [[255, 0, 0]], field: bad}
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            "texture",
            "modify",
            "Assets/Owned.png",
            "--set-pixels",
            json.dumps(value),
        ],
    )
    assert result.exit_code == 2, (result.exception, result.stdout, result.stderr, state)
    assert result.stdout == "" and "--set-pixels" in result.stderr and field in result.stderr
    assert state["calls"] == [] and state["encoded"] == []


@pytest.mark.parametrize("snake,_camel", IMPORT_INTEGERS)
@pytest.mark.parametrize("bad", BAD_INTEGERS)
@pytest.mark.parametrize("output_format", ["json", "text", "table"])
def test_snake_import_integer_rejects_lossy_values_without_transport(
    dispatch, snake, _camel, bad, output_format
):
    cli, state = dispatch
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            output_format,
            "texture",
            "create",
            "Assets/Owned.png",
            "--import-settings",
            json.dumps({snake: bad}),
        ],
    )
    assert result.exit_code == 2, (result.exception, result.stdout, result.stderr, state)
    assert result.stdout == "" and "--import-settings" in result.stderr and snake in result.stderr
    assert state["calls"] == [] and state["encoded"] == []


@pytest.mark.parametrize("field", ["x", "y", "width", "height"])
def test_region_null_keeps_native_default_on_wire(dispatch, field):
    cli, state = dispatch
    value = {"color": [255, 0, 0], field: None}
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            "texture",
            "modify",
            "Assets/Owned.png",
            "--set-pixels",
            json.dumps(value),
        ],
    )
    assert result.exit_code == 0, (result.exception, result.output)
    assert state["calls"][0][1]["setPixels"] == {**value, "color": [255, 0, 0, 255]}


@pytest.mark.parametrize("snake,camel", IMPORT_INTEGERS)
@pytest.mark.parametrize(
    "value,expected",
    [
        (None, None),
        (" +1 ", 1),
        (0, 0),
        (-1, -1),
        (2147483647, 2147483647),
        (-2147483648, -2147483648),
    ],
)
def test_snake_import_defaults_integer_strings_and_native_ranges_remain_supported(
    dispatch, snake, camel, value, expected
):
    cli, state = dispatch
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            "texture",
            "create",
            "Assets/Owned.png",
            "--import-settings",
            json.dumps({snake: value}),
        ],
    )
    assert result.exit_code == 0, (result.exception, result.output)
    assert state["calls"][0][1]["importSettings"] == {camel: expected}


@pytest.mark.parametrize(
    "raw,expected",
    [
        ({"x": " -1 ", "y": "+0", "color": [0, 0, 0, 0]}, {"x": -1, "y": 0, "color": [0, 0, 0, 0]}),
        (
            {"width": "1", "height": "+1", "pixels": [[0.5, 0, 0]]},
            {"width": 1, "height": 1, "pixels": [[128, 0, 0, 255]]},
        ),
        (
            {"x": -2147483648, "y": 2147483647, "color": [255, 0, 0]},
            {"x": -2147483648, "y": 2147483647, "color": [255, 0, 0, 255]},
        ),
    ],
)
def test_region_integer_strings_clipping_coordinates_and_pixels_stay_supported(
    dispatch, raw, expected
):
    cli, state = dispatch
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            "texture",
            "modify",
            "Assets/Owned.png",
            "--set-pixels",
            json.dumps(raw),
        ],
    )
    assert result.exit_code == 0, (result.exception, result.output)
    assert state["calls"][0][1]["setPixels"] == expected


@pytest.mark.parametrize("color", ["[1e999,0,0]", '{"r":1e999,"g":0,"b":0}'])
@pytest.mark.parametrize(
    "surface", ["create-color", "sprite-color", "create-palette", "region-color", "region-pixels"]
)
@pytest.mark.parametrize("output_format", ["json", "text", "table"])
def test_overflow_color_has_named_diagnostic_before_transport(
    dispatch, color, surface, output_format
):
    cli, state = dispatch
    flag = "color"
    if surface == "create-color":
        args = ["create", "Assets/Owned.png", "--color", color]
    elif surface == "sprite-color":
        args = ["sprite", "Assets/Owned.png", "--color", color]
    elif surface == "create-palette":
        flag = "palette"
        args = ["create", "Assets/Owned.png", "--palette", "[" + color + "]"]
    else:
        flag = "set-pixels"
        value = (
            '{"color":' + color + "}"
            if surface == "region-color"
            else '{"width":1,"height":1,"pixels":[' + color + "]}"
        )
        args = ["modify", "Assets/Owned.png", "--set-pixels", value]
    result = CliRunner().invoke(cli, ["--format", output_format, "texture", *args])
    assert result.exit_code == 2, (result.exception, result.stdout, result.stderr, state)
    assert result.stdout == "" and f"--{flag}" in result.stderr
    assert state["calls"] == [] and state["encoded"] == []


@pytest.mark.parametrize("camel", [camel for _, camel in IMPORT_INTEGERS])
@pytest.mark.parametrize("value", [None, "bad"])
def test_camel_import_pass_through_preserves_native_error_data(dispatch, camel, value):
    cli, state = dispatch
    state["failure"] = True
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            "texture",
            "create",
            "Assets/Owned.png",
            "--import-settings",
            json.dumps({camel: value}),
        ],
    )
    assert result.exit_code == 1 and result.stderr == ""
    assert state["calls"][0][1]["importSettings"] == {camel: value}
    assert json.loads(result.stdout) == {
        "success": False,
        "error": "Owned native rejection",
        "data": {"zero": 0, "false": False, "null": None},
    }


@pytest.mark.parametrize(
    "bad", [True, False, [], {}, float("nan"), float("inf"), -float("inf"), 1e100]
)
@pytest.mark.parametrize("output_format", ["json", "text", "table"])
def test_snake_ppu_rejects_invalid_native_float_without_coercion(dispatch, bad, output_format):
    cli, state = dispatch
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            output_format,
            "texture",
            "create",
            "Assets/Owned.png",
            "--import-settings",
            json.dumps({"sprite_pixels_per_unit": bad}),
        ],
    )
    assert result.exit_code == 2, (result.exception, result.stdout, result.stderr, state)
    assert (
        result.stdout == ""
        and "--import-settings" in result.stderr
        and "sprite_pixels_per_unit" in result.stderr
    )
    assert state["calls"] == [] and state["encoded"] == []


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, None),
        (0, 0),
        (-1, -1),
        (" +1e-3 ", 0.001),
        (3.4028235e38, 3.4028235e38),
        (1e-100, 1e-100),
    ],
)
def test_snake_ppu_preserves_native_defaults_strings_and_float_rounding(dispatch, raw, expected):
    cli, state = dispatch
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            "texture",
            "create",
            "Assets/Owned.png",
            "--import-settings",
            json.dumps({"sprite_pixels_per_unit": raw}),
        ],
    )
    assert result.exit_code == 0, (result.exception, result.output)
    assert state["calls"][0][1]["importSettings"] == {"spritePixelsPerUnit": expected}


@pytest.mark.parametrize("pixels", [[[255, 0, 0]], "base64:AAAAAA=="])
def test_explicit_null_pixel_dimensions_use_native_count_default_and_preserve_wire_null(
    dispatch, pixels
):
    cli, state = dispatch
    raw = {"width": None, "height": None, "pixels": pixels}
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            "texture",
            "modify",
            "Assets/Owned.png",
            "--set-pixels",
            json.dumps(raw),
        ],
    )
    assert result.exit_code == 0, (result.exception, result.output)
    expected = [[255, 0, 0, 255]] if isinstance(pixels, list) else pixels
    assert state["calls"][0][1]["setPixels"] == {**raw, "pixels": expected}


@pytest.mark.parametrize("missing", ["width", "height"])
def test_missing_pixel_dimensions_still_require_explicit_cli_dimensions(dispatch, missing):
    cli, state = dispatch
    raw = {"width": 1, "height": 1, "pixels": [[255, 0, 0]]}
    raw.pop(missing)
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            "texture",
            "modify",
            "Assets/Owned.png",
            "--set-pixels",
            json.dumps(raw),
        ],
    )
    assert result.exit_code == 2, (result.exception, result.output)
    assert "--set-pixels" in result.stderr and state["calls"] == []


@pytest.mark.parametrize("surface", ["region", "integer-import", "ppu-import"])
@pytest.mark.parametrize(
    "raw", ["1_000", "\u0661\u0662", "\uff11\uff12", "\u00a012\u00a0", "\u200312\u2003"]
)
def test_native_invalid_numeric_strings_do_not_gain_validity_from_python_parsing(
    dispatch, surface, raw
):
    cli, state = dispatch
    if surface == "region":
        flag = "set-pixels"
        args = [
            "modify",
            "Assets/Owned.png",
            "--set-pixels",
            json.dumps({"x": raw, "color": [255, 0, 0]}),
        ]
    else:
        flag = "import-settings"
        field = "aniso_level" if surface == "integer-import" else "sprite_pixels_per_unit"
        args = ["create", "Assets/Owned.png", "--import-settings", json.dumps({field: raw})]
    result = CliRunner().invoke(cli, ["--format", "json", "texture", *args])
    assert result.exit_code == 2, (result.exception, result.stdout, result.stderr, state)
    assert f"--{flag}" in result.stderr and result.stdout == ""
    assert state["calls"] == [] and state["encoded"] == []


@pytest.mark.parametrize("surface", ["region", "integer-import", "ppu-import"])
def test_ascii_numeric_whitespace_and_sign_remain_supported(dispatch, surface):
    cli, state = dispatch
    raw = " \t+12\r\n"
    if surface == "region":
        args = [
            "modify",
            "Assets/Owned.png",
            "--set-pixels",
            json.dumps({"x": raw, "color": [255, 0, 0]}),
        ]
    else:
        field = "aniso_level" if surface == "integer-import" else "sprite_pixels_per_unit"
        args = ["create", "Assets/Owned.png", "--import-settings", json.dumps({field: raw})]
    result = CliRunner().invoke(cli, ["--format", "json", "texture", *args])
    assert result.exit_code == 0, (result.exception, result.output)
    assert len(state["calls"]) == len(state["encoded"]) == 1


@pytest.mark.parametrize(
    "raw,expected",
    [("-.5", -0.5), ("1.", 1.0), (" +1E-3 ", 0.001), ("\v\f-2\v\f", -2.0), ("0.0", 0.0)],
)
def test_native_decimal_and_exponent_ppu_syntax_remains_supported(dispatch, raw, expected):
    cli, state = dispatch
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            "texture",
            "create",
            "Assets/Owned.png",
            "--import-settings",
            json.dumps({"sprite_pixels_per_unit": raw}),
        ],
    )
    assert result.exit_code == 0, (result.exception, result.output)
    assert state["calls"][0][1]["importSettings"] == {"spritePixelsPerUnit": expected}
