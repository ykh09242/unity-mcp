import asyncio
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from click.testing import CliRunner
from cli.commands.vfx import vfx


@pytest.mark.parametrize(
    "domain,action", [("animation", "clip_create"), ("vfx", "particle_create")]
)
@pytest.mark.parametrize("properties", ["[]", "null", "1", '{"broken":'])
def test_invalid_domain_properties_rejected_before_transport(
    monkeypatch, domain, action, properties
):
    module = importlib.import_module("services.tools.manage_" + domain)
    send = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    monkeypatch.setattr(module, "get_unity_instance_from_context", AsyncMock(return_value=None))
    response = asyncio.run(
        getattr(module, "manage_" + domain)(SimpleNamespace(), action, properties=properties)
    )
    assert response["success"] is False
    assert "properties" in response["message"]
    send.assert_not_awaited()


@pytest.mark.parametrize(
    "domain,action", [("animation", "animator_set_parameter"), ("vfx", "particle_set_main")]
)
def test_valid_object_json_retains_null_false_and_zero(monkeypatch, domain, action):
    module = importlib.import_module("services.tools.manage_" + domain)
    send = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    monkeypatch.setattr(module, "get_unity_instance_from_context", AsyncMock(return_value=None))
    response = asyncio.run(
        getattr(module, "manage_" + domain)(
            SimpleNamespace(),
            action,
            properties='{"value":null,"enabled":false,"speed":0}',
        )
    )
    assert response["success"] is True
    assert send.call_args.args[3]["properties"] == {"value": None, "enabled": False, "speed": 0}


@pytest.mark.parametrize("action", ["play", "stop", "restart", "clear"])
@pytest.mark.parametrize("with_children", [False, True])
def test_particle_cli_child_flag_is_explicit(monkeypatch, action, with_children):
    monkeypatch.setattr(
        "cli.commands.vfx.get_config", Mock(return_value=SimpleNamespace(format="json"))
    )
    send = Mock(return_value={"success": True})
    monkeypatch.setattr("cli.commands.vfx.run_command", send)
    args = ["particle", action, "Effects"] + (["--with-children"] if with_children else [])
    result = CliRunner().invoke(vfx, args)
    assert result.exit_code == 0, result.output
    assert send.call_args.args[1]["properties"]["withChildren"] is with_children
