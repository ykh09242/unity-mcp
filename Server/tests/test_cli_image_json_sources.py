"""Image JSON sources pass through the actual Click and HTTP command boundaries."""

import json

import httpx
import pytest
from click.testing import CliRunner

from cli.commands.ui import ui
from cli.utils import connection
from cli.utils.config import CLIConfig
from cli.utils.parsers import MAX_JSON_INPUT_BYTES


@pytest.fixture
def image_wire(monkeypatch):
    requests = []
    client_type = httpx.AsyncClient

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"success": True})

    monkeypatch.setattr(
        connection.httpx,
        "AsyncClient",
        lambda **_options: client_type(transport=httpx.MockTransport(respond)),
    )
    monkeypatch.setattr(connection, "_auth_headers", lambda _config: {})
    monkeypatch.setattr(
        "cli.commands.ui.get_config",
        lambda: CLIConfig(format="json", unity_instance="Image@fixture"),
    )
    return requests


@pytest.mark.parametrize("source", ["inline", "file", "-", "@-"])
def test_image_sources_dispatch_identical_wire_payload(image_wire, tmp_path, source):
    properties = {"sprite": {"path": "Assets/icons.asset", "name": "상태@icon"}}
    encoded = json.dumps(properties, ensure_ascii=False).encode("utf-8")
    path = tmp_path / "image properties.json"
    path.write_bytes(b"\xef\xbb\xbf" + encoded)
    argument = {"inline": encoded.decode(), "file": "@" + str(path)}.get(source, source)
    result = CliRunner().invoke(
        ui, ["set-image", "Canvas/Icon", "--properties", argument], input=encoded
    )
    assert result.exit_code == 0, result.output
    assert len(image_wire) == 1
    assert image_wire[0]["unity_instance"] == "Image@fixture"
    assert image_wire[0]["params"] == {
        "action": "set_image",
        "target": "Canvas/Icon",
        "properties": properties,
    }


@pytest.mark.parametrize("source", ["file", "-", "@-"])
@pytest.mark.parametrize(
    "payload,exit_code",
    [
        (b"{}", 2),
        (b"null", 2),
        (b"[]", 2),
        (b'{"PRIVATE_MARKER":', 1),
        (b'{"PRIVATE_MARKER":NaN}', 1),
        (b'{"PRIVATE_MARKER":1e309}', 1),
        (b'{"PRIVATE_MARKER":"\xff"}', 1),
        (b" " * (MAX_JSON_INPUT_BYTES + 1), 1),
    ],
)
def test_invalid_image_sources_never_read_config_or_dispatch(
    monkeypatch, tmp_path, source, payload, exit_code
):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Invalid image JSON reached config or transport")

    monkeypatch.setattr("cli.commands.ui.get_config", forbidden)
    monkeypatch.setattr("cli.commands.ui.run_command", forbidden)
    path = tmp_path / "invalid.json"
    path.write_bytes(payload)
    argument = "@" + str(path) if source == "file" else source
    result = CliRunner().invoke(
        ui, ["set-image", "Canvas/Icon", "--properties", argument], input=payload
    )
    assert result.exit_code == exit_code, result.output
    assert result.stdout == ""
    assert "PRIVATE_MARKER" not in result.stderr
