import asyncio
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from click.testing import CliRunner
from cli.commands.physics import physics


@pytest.mark.parametrize(
    "dimension,force,torque", [("2d", [1, 2], [3]), ("3d", [1, 2, 3], [4, 5, 6])]
)
def test_mcp_force_preserves_torque_shape_and_unity_rejection(
    monkeypatch, dimension, force, torque
):
    module = importlib.import_module("services.tools.manage_physics")
    rejection = {"success": False, "error": "invalid torque", "data": {"target": "123"}}
    send = AsyncMock(return_value=rejection)
    monkeypatch.setattr(module, "get_unity_instance_from_context", AsyncMock(return_value=None))
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    result = asyncio.run(
        module.manage_physics(
            SimpleNamespace(),
            "apply_force",
            target="123",
            dimension=dimension,
            force=force,
            torque=torque,
            search_method="by_name",
        )
    )
    assert result == rejection
    payload = send.call_args.args[3]
    assert payload["force"] == force
    assert payload["torque"] == torque
    assert payload["search_method"] == "by_name"


@pytest.mark.parametrize(
    "dimension,force,torque,expected",
    [("2d", "1,2", "3", [3.0]), ("3d", "1,2,3", "4,5,6", [4.0, 5.0, 6.0])],
)
def test_cli_force_uses_documented_torque_array(monkeypatch, dimension, force, torque, expected):
    monkeypatch.setattr(
        "cli.commands.physics.get_config", Mock(return_value=SimpleNamespace(format="json"))
    )
    send = Mock(return_value={"success": True})
    monkeypatch.setattr("cli.commands.physics.run_command", send)
    result = CliRunner().invoke(
        physics,
        [
            "apply-force",
            "--target",
            "123",
            "--dimension",
            dimension,
            "--force",
            force,
            "--torque",
            torque,
            "--force-mode",
            "Impulse",
        ],
    )
    assert result.exit_code == 0, result.output
    assert send.call_args.args[1]["torque"] == expected
    assert send.call_args.args[1]["force_mode"] == "Impulse"
