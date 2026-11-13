"""Reflection member output retains overload signatures and parameter defaults."""

import json

import pytest
from click.testing import CliRunner

from cli.main import cli
from cli.commands import reflect


@pytest.mark.parametrize("output_format", ["text", "json"])
def test_member_preserves_overload_details(monkeypatch, output_format):
    # Given: overload metadata whose optional defaults exceed the compact list limit.
    signature = "Boolean Raycast(Vector3 origin, Vector3 direction, Single maxDistance)"
    response = {
        "success": True,
        "data": {
            "overloads": [
                {
                    "signature": signature,
                    "parameters": [
                        {"name": "maxDistance", "type": "Single", "default": "Infinity"},
                        {"name": "layerMask", "type": "Int32", "default": -5},
                    ],
                },
                {"signature": "Boolean Raycast(Ray ray)", "parameters": []},
            ]
        },
    }
    calls = []

    def respond(command, params, _config):
        calls.append((command, params))
        return response

    monkeypatch.setattr(reflect, "run_command", respond)
    # When: the actual CLI renders member details.
    result = CliRunner().invoke(
        cli, ["--format", output_format, "reflect", "member", "Physics", "Raycast"]
    )
    # Then: both overloads and each parameter/default remain inspectable.
    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert calls == [
        (
            "unity_reflect",
            {"action": "get_member", "class_name": "Physics", "member_name": "Raycast"},
        )
    ]
    if output_format == "json":
        assert json.loads(result.stdout) == response
    else:
        for value in (
            signature,
            "Boolean Raycast(Ray ray)",
            "maxDistance",
            "Infinity",
            "layerMask",
            "-5",
        ):
            assert value in result.stdout
