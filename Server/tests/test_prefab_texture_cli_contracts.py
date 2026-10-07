"""Prefab/texture output and request contracts through the real HTTP command route."""

import json
import os
import subprocess
import sys
import textwrap

import httpx
import pytest
from click.testing import CliRunner

from cli.main import cli
from cli.utils import connection


PREFAB = "Assets/PrefabFixture.prefab"
TEXTURE = "Assets/TextureFixture.png"
COMMANDS = [
    ["prefab", "overrides", "Player", "--page-size", "5", "--property-filter", "m_"],
    ["prefab", "revert-overrides", "Player", "--override-id", "property:-42:m_IsTrigger"],
    [
        "prefab",
        "apply-overrides",
        "Player",
        "--prefab-path",
        PREFAB,
        "--override-id",
        "property:-42:m_IsTrigger",
    ],
    ["prefab", "open", PREFAB],
    ["prefab", "close"],
    ["prefab", "save"],
    ["prefab", "create", "Player", PREFAB],
    ["prefab", "modify", PREFAB, "--inactive"],
    ["prefab", "info", PREFAB, "--compact"],
    ["prefab", "hierarchy", PREFAB, "--compact"],
    ["prefab", "hierarchy", PREFAB, "--show-prefab-info"],
    ["prefab", "hierarchy", PREFAB, "--compact", "--show-prefab-info"],
    ["texture", "create", TEXTURE],
    ["texture", "sprite", TEXTURE],
    ["texture", "modify", TEXTURE, "--no-readable"],
    ["texture", "delete", TEXTURE, "--force"],
    ["texture", "set-import-settings", TEXTURE, "--linear"],
    ["texture", "create", TEXTURE, "--width", "1025", "--height", "1"],
    ["texture", "sprite", TEXTURE, "--width", "1025", "--height", "1"],
]


@pytest.fixture
def domain_transport(monkeypatch):
    response = {
        "success": True,
        "data": {
            "assetPath": PREFAB,
            "rootObjectName": "Root",
            "childCount": 0,
            "isVariant": False,
            "items": [{"name": "Root", "path": "Root", "prefab": {"isRoot": True}}],
            "total": 1,
        },
    }
    requests = []
    client_type = httpx.AsyncClient

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=response)

    monkeypatch.setattr(
        connection.httpx, "AsyncClient", lambda: client_type(transport=httpx.MockTransport(respond))
    )
    monkeypatch.setattr(connection, "_auth_headers", lambda config: {})
    return response, requests


@pytest.mark.parametrize("command", COMMANDS)
def test_json_success_is_one_native_response_document(domain_transport, command):
    response, requests = domain_transport
    result = CliRunner().invoke(
        cli, ["--instance", "Project@fixture", "--format", "json", *command]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == response
    assert len(requests) == 1 and requests[0]["unity_instance"] == "Project@fixture"


@pytest.mark.parametrize("command", COMMANDS)
@pytest.mark.parametrize("wrapped", [False, True])
def test_native_failure_exits_and_preserves_json_even_with_warning_dimensions(
    domain_transport, command, wrapped
):
    response, requests = domain_transport
    failure = {
        "success": False,
        "error": "Operation failed",
        "data": {"count": 0, "modified": False},
    }
    response.clear()
    response.update({"status": "success", "result": failure} if wrapped else failure)
    result = CliRunner().invoke(cli, ["--format", "json", *command])
    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout) == failure
    assert len(requests) == 1


@pytest.mark.parametrize(
    "command,notice",
    [
        (["prefab", "open", PREFAB], "Opened prefab:"),
        (["prefab", "info", PREFAB, "--compact"], "Children: 0"),
        (["prefab", "hierarchy", PREFAB, "--show-prefab-info"], "Root [root]"),
        (["texture", "create", TEXTURE, "--width", "1025", "--height", "1"], "Warning:"),
        (["texture", "modify", TEXTURE, "--no-readable"], "Modified texture:"),
    ],
)
def test_text_keeps_requested_summaries_and_guidance(domain_transport, command, notice):
    result = CliRunner().invoke(cli, [*command])
    assert result.exit_code == 0, result.output
    assert notice in result.output


def test_prefab_modification_keeps_numeric_name_false_and_zero(domain_transport):
    _, requests = domain_transport
    result = CliRunner().invoke(
        cli,
        [
            "prefab",
            "modify",
            PREFAB,
            "--target",
            "0",
            "--inactive",
            "--position",
            "0,0,0",
            "--set-property",
            "MyComponent.enabled=false",
            "--set-property",
            "MyComponent.count=0",
        ],
    )
    assert result.exit_code == 0, result.output
    assert requests[0]["params"] == {
        "action": "modify_contents",
        "prefabPath": PREFAB,
        "target": "0",
        "setActive": False,
        "position": [0.0, 0.0, 0.0],
        "componentProperties": {"MyComponent": {"enabled": False, "count": 0}},
    }


def test_prefab_override_cli_preserves_explicit_ids_and_nested_destination(domain_transport):
    _, requests = domain_transport
    selected = ["property:-42:m_IsTrigger", "property:-42:m_Size"]
    result = CliRunner().invoke(
        cli,
        [
            "prefab",
            "apply-overrides",
            "Outer/Nested",
            "--prefab-path",
            PREFAB,
            "--override-id",
            selected[0],
            "--override-id",
            selected[1],
        ],
    )
    assert result.exit_code == 0, result.output
    assert requests[0]["params"] == {
        "action": "apply_overrides",
        "target": "Outer/Nested",
        "prefabPath": PREFAB,
        "overrideIds": selected,
    }


@pytest.mark.parametrize(
    "command",
    [
        ["prefab", "revert-overrides", "Player"],
        ["prefab", "apply-overrides", "Player", "--override-id", "id"],
        ["prefab", "overrides", "Player", "--page-size", "501"],
    ],
)
def test_prefab_override_cli_rejects_incomplete_or_unbounded_requests(domain_transport, command):
    _, requests = domain_transport
    result = CliRunner().invoke(cli, command)
    assert result.exit_code == 2
    assert requests == []


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("null", None),
        ('{"path":"Assets/Fixture.mat"}', {"path": "Assets/Fixture.mat"}),
        ("[0,1,2]", [0, 1, 2]),
        ('"0"', "0"),
        ('""', ""),
        ("1e3", 1000.0),
        ("FALSE", False),
        ("plain text", "plain text"),
    ],
)
def test_prefab_component_property_preserves_json_values_on_wire(domain_transport, raw, expected):
    # Given a prefab target and a component property value supplied through the CLI.
    response, requests = domain_transport
    # When one headless modification is sent through the actual HTTP adapter.
    result = CliRunner().invoke(
        cli,
        [
            "--instance",
            "Project@fixture",
            "--format",
            "json",
            "prefab",
            "modify",
            PREFAB,
            "--target",
            "Parent/Child",
            "--set-property",
            f"MyComponent.reference={raw}",
        ],
    )
    # Then JSON values retain their types and the targeted operation remains one call.
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == response
    assert len(requests) == 1
    assert requests[0]["unity_instance"] == "Project@fixture"
    assert requests[0]["params"]["target"] == "Parent/Child"
    assert requests[0]["params"]["componentProperties"] == {"MyComponent": {"reference": expected}}


@pytest.mark.parametrize("option,field", [("--tag", "tag"), ("--parent", "parent")])
@pytest.mark.parametrize("surface", ["prefab", "gameobject"])
def test_empty_reset_is_preserved_on_wire(domain_transport, option, field, surface):
    # Given the native empty-string reset for an object's tag or parent.
    response, requests = domain_transport
    command = (
        ["prefab", "modify", PREFAB, "--target", "Parent/Child"]
        if surface == "prefab"
        else ["gameobject", "modify", "Parent/Child"]
    )
    # When the reset is explicitly requested through the CLI.
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            *command,
            option,
            "",
        ],
    )
    # Then omission and an explicit reset remain different native requests.
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == response
    assert len(requests) == 1
    expected = {"action": "modify", "target": "Parent/Child", field: ""}
    if surface == "prefab":
        expected.update(action="modify_contents", prefabPath=PREFAB)
    assert requests[0]["params"] == expected


def test_texture_region_and_import_flags_preserve_clipping_inputs_and_false(domain_transport):
    _, requests = domain_transport
    result = CliRunner().invoke(
        cli,
        [
            "texture",
            "modify",
            TEXTURE,
            "--set-pixels",
            '{"x":-1,"y":0,"width":2,"height":1,"color":[0,0,0,0]}',
            "--no-mipmaps",
            "--linear",
            "--no-readable",
        ],
    )
    assert result.exit_code == 0, result.output
    assert requests[0]["params"] == {
        "action": "modify",
        "path": TEXTURE,
        "setPixels": {"x": -1, "y": 0, "width": 2, "height": 1, "color": [0, 0, 0, 0]},
        "importSettings": {"mipmapEnabled": False, "sRGBTexture": False, "isReadable": False},
    }


@pytest.mark.parametrize(
    "command",
    [
        ["prefab", "modify", PREFAB, "--create-child", "[]"],
        ["prefab", "modify", PREFAB, "--position", "0,1"],
        ["texture", "modify", TEXTURE, "--set-pixels", "[]"],
        [
            "texture",
            "modify",
            TEXTURE,
            "--set-pixels",
            '{"width":2,"height":1,"pixels":[[255,0,0]]}',
        ],
    ],
)
def test_malformed_cli_payload_does_not_send(domain_transport, command):
    _, requests = domain_transport
    result = CliRunner().invoke(cli, [*command])
    assert result.exit_code != 0, result.output
    assert requests == []


def test_json_output_through_actual_cli_in_fresh_process():
    code = textwrap.dedent("""
        import json
        import httpx
        from click.testing import CliRunner
        from cli.main import cli
        from cli.utils import connection
        requests = []
        response = {"success": True,"data": {"childCount": 0,"items": [],"total": 0}}
        client_type = httpx.AsyncClient
        def respond(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200,json=response)
        connection.httpx.AsyncClient = lambda: client_type(transport=httpx.MockTransport(respond))
        connection._auth_headers = lambda config: {}
        commands = [
            ["prefab","open","Assets/Fixture.prefab"],
            ["prefab","info","Assets/Fixture.prefab","--compact"],
            ["prefab","hierarchy","Assets/Fixture.prefab","--show-prefab-info"],
            ["texture","create","Assets/Fixture.png","--width","1025","--height","1"],
            ["texture","sprite","Assets/Fixture.png","--width","1025","--height","1"],
            ["texture","modify","Assets/Fixture.png","--no-readable"],
        ]
        runner = CliRunner()
        for failure in (False,True):
            expected = {"success": False,"error": "Native operation failed","data": {"count": 0,"modified": False}} if failure else dict(response)
            response.clear()
            response.update({"status": "success","result": expected} if failure else expected)
            for command in commands:
                result = runner.invoke(cli,["--format","json",*command])
                assert result.exit_code == (1 if failure else 0), result.output
                assert json.loads(result.stdout) == expected, command
        assert len(requests) == 12
        print("fresh actual CLI/HTTP12 JSON scenarios passed")
    """)
    env = {**os.environ, "UNITY_MCP_DISABLE_TELEMETRY": "true"}
    result = subprocess.run(
        [sys.executable, "-B", "-c", code], env=env, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "fresh actual CLI/HTTP12 JSON scenarios passed" in result.stdout
