"""Nonfinite numeric CLI input fails locally before HTTP encoding or dispatch."""

import json

import httpx
import pytest
from click.testing import CliRunner

from cli.main import cli
from cli.utils import connection


@pytest.fixture
def requests(monkeypatch, tmp_path):
    for name in ("HOST", "HTTP_PORT", "TIMEOUT", "FORMAT", "INSTANCE"):
        monkeypatch.delenv("UNITY_MCP_" + name, raising=False)
    monkeypatch.chdir(tmp_path)
    captured = []
    client_type = httpx.AsyncClient

    def respond(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"success": True, "data": {"count": 0}})

    monkeypatch.setattr(connection, "_auth_headers", lambda _config: {})
    monkeypatch.setattr(
        connection.httpx,
        "AsyncClient",
        lambda **_options: client_type(transport=httpx.MockTransport(respond)),
    )
    return captured


@pytest.mark.parametrize("output_format", ["json", "text", "table"])
@pytest.mark.parametrize(
    "command,path",
    [
        (["camera", "set-lens", "Main", "--fov", "nan"], "params.properties.fieldOfView"),
        (["camera", "set-lens", "Main", "--fov", "inf"], "params.properties.fieldOfView"),
        (["camera", "set-lens", "Main", "--fov", "-inf"], "params.properties.fieldOfView"),
        (["camera", "set-lens", "Main", "--near", "1e999"], "params.properties.nearClipPlane"),
        (
            [
                "gameobject",
                "move",
                "Cube",
                "--reference",
                "Origin",
                "--direction",
                "up",
                "--distance",
                "inf",
            ],
            "params.distance",
        ),
        (["gameobject", "create", "Cube", "--position", "0", "-inf", "1"], "params.position[1]"),
    ],
)
def test_nonfinite_input_is_local(requests, monkeypatch, command, path, output_format):
    # Given: HTTP client construction is forbidden for invalid numeric input.
    def forbidden_client(**_options):
        pytest.fail("Nonfinite input must fail before HTTP client construction")

    monkeypatch.setattr(connection.httpx, "AsyncClient", forbidden_client)
    # When: the complete CLI parses scalar, vector or nested numeric input.
    result = CliRunner().invoke(cli, ["--format", output_format, *command])
    # Then: the user receives a field-specific input failure, never a connection failure.
    assert result.exit_code == 1, result.output
    assert requests == []
    if output_format == "json":
        payload = json.loads(result.stdout)
        assert payload["code"] == "invalid_numeric_input"
        diagnostic = payload["error"]
        assert result.stderr == ""
    else:
        assert result.stdout == ""
        diagnostic = result.stderr
    assert path in diagnostic
    assert "finite number" in diagnostic
    assert "connection_error" not in diagnostic


@pytest.mark.parametrize("distance", ["0", "-2.5", "1e-100", "1e100"])
def test_finite_signed_distance_preserves_payload(requests, distance):
    # Given: relative movement accepts signed finite distances, including zero.
    # When: the real CLI reaches the mocked HTTP transport.
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            "gameobject",
            "move",
            "Cube",
            "--reference",
            "Origin",
            "--direction",
            "up",
            "--distance",
            distance,
        ],
    )
    # Then: exactly one request preserves the supplied numeric value.
    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert len(requests) == 1
    assert requests[0]["params"]["distance"] == float(distance)


def test_nested_finite_payload_preserves_falsey_values(requests):
    # Given: JSON input contains nested finite numbers and nonnumeric falsey values.
    properties = {"values": [0, {"weight": -2.5, "enabled": False, "ref": None, "label": "NaN"}]}
    # When: the actual CLI forwards component properties through HTTP encoding.
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            "component",
            "modify",
            "Cube",
            "Fixture",
            "--properties",
            json.dumps(properties),
        ],
    )
    # Then: validation preserves every supported JSON value exactly.
    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert len(requests) == 1
    assert requests[0]["params"]["properties"] == properties
