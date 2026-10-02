"""Exercise the real Click command with only its Unity transport replaced."""

from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from cli.commands.lighting import lighting
from cli.utils.config import CLIConfig


@pytest.mark.parametrize("created_id", [-204, 204])
def test_light_creation_configures_returned_id_instead_of_colliding_name(created_id):
    calls = []

    async def send(command, params, config=None, timeout=None):
        calls.append((command, params))
        if command == "manage_gameobject":
            return {"success": True, "data": {"instanceID": created_id, "name": "SharedName"}}
        return {"success": True}

    with patch("cli.commands.lighting.get_config", return_value=CLIConfig(format="json")), patch(
        "cli.utils.connection.send_command", side_effect=send
    ):
        result = CliRunner().invoke(lighting, ["create", "SharedName", "--color", "0", "0", "0", "--intensity", "0"])

    assert result.exit_code == 0, result.output
    assert len(calls) == 5
    assert all(params["target"] == created_id for _, params in calls[1:]), calls
    assert all(params["search_method"] == "by_id" for _, params in calls[1:])
    assert calls[-1][1]["value"] == 0
    assert calls[-2][1]["value"] == {"r": 0, "g": 0, "b": 0, "a": 1}


@pytest.mark.parametrize("data", [None, {}, {"instanceID": None}])
def test_light_creation_without_returned_id_stops_before_component_writes(data):
    with patch("cli.commands.lighting.get_config", return_value=CLIConfig(format="json")), patch(
        "cli.utils.connection.send_command", new_callable=AsyncMock,
        return_value={"success": True, "data": data}
    ) as send:
        result = CliRunner().invoke(lighting, ["create", "SharedName"])

    assert result.exit_code == 1
    assert send.call_count == 1
    assert "instanceID" in result.output


def test_failed_light_creation_stops_before_component_writes():
    with patch("cli.commands.lighting.get_config", return_value=CLIConfig(format="json")), patch(
        "cli.utils.connection.send_command", new_callable=AsyncMock,
        return_value={"success": False, "error": "creation failed"}
    ) as send:
        result = CliRunner().invoke(lighting, ["create", "SharedName"])

    assert send.call_count == 1
    assert "creation failed" in result.output
