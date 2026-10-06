"""Real SDK dispatch checks without the suite's preflight monkeypatches."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


def run_dispatch_scenario(source: str, tmp_path: Path) -> None:
    environment = dict(os.environ)
    environment["UNITY_MCP_DISABLE_TELEMETRY"] = "1"
    environment["UNITY_MCP_LOG_DIR"] = str(tmp_path)
    result = subprocess.run(
        [sys.executable, "-B", "-c", textwrap.dedent(source)],
        cwd=Path(__file__).resolve().parents[1], env=environment,
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
def test_sdk_rejects_invalid_shapes_before_routing_state_or_editor_io(tmp_path, mode):
    run_dispatch_scenario('''
        import asyncio, socket, sys
        from types import SimpleNamespace
        from typing import Annotated
        from unittest.mock import AsyncMock, Mock
        sys.path.insert(0, "src")
        from fastmcp import Client, Context, FastMCP
        from pydantic import StrictBool, StrictInt
        from core.config import config
        from services.registry import get_registered_tools
        from transport.plugin_hub import PluginHub
        from transport.unity_instance_middleware import UnityInstanceMiddleware
        import transport.legacy.unity_connection as connection
        import services.tools.batch_execute as batch
        import importlib
        batch = importlib.import_module("services.tools.batch_execute")
        importlib.import_module("services.tools.manage_tools")
        importlib.import_module("services.tools.execute_custom_tool")

        def deny_outbound(*args, **kwargs):
            raise AssertionError("Unexpected outbound connection")

        async def scenario():
            socket.socket.connect = deny_outbound
            config.transport_mode = "stdio"
            config.http_remote_hosted = False
            PluginHub._registry = None
            events = []
            payloads = []
            pool = Mock()
            def discover(**kwargs):
                events.append("discover")
                return [SimpleNamespace(id="Project@hash", hash="hash", port=6401)]
            pool.discover_all_instances.side_effect = discover
            connection.get_unity_connection_pool = lambda: pool
            original_set_state = Context.set_state
            async def set_state(self, key, value, **kwargs):
                events.append("state")
                return await original_set_state(self, key, value, **kwargs)
            Context.set_state = set_state
            async def send(*args, **kwargs):
                events.append(args[2])
                payloads.append((args[2], args[3]))
                return {"success": True, "data": {"settings": {"batch_execute_max_commands": 25}}}
            batch.send_with_unity_instance = send
            server = FastMCP("early-sdk-validation")
            server.add_middleware(UnityInstanceMiddleware())
            for module in ("manage_components", "manage_physics", "manage_vfx", "manage_graphics", "manage_material", "manage_scene"):
                importlib.import_module("services.tools." + module)
            for name in ("batch_execute", "manage_tools", "execute_custom_tool", "manage_components", "manage_physics", "manage_vfx", "manage_graphics", "manage_material", "manage_scene"):
                metadata = next(tool for tool in get_registered_tools() if tool["name"] == name)
                server.tool(name=name)(metadata["func"])
            @server.tool
            async def strict_fixture(ctx: Context, flag: StrictBool, count: StrictInt=3) -> dict:
                events.append("handler")
                return {"flag": flag, "count": count}

            async with Client(server, mode=MODE) as client:
                for name, payload in (
                    ("strict_fixture", {"flag": 1}),
                    ("strict_fixture", {}),
                    ("strict_fixture", {"flag": False, "count": True}),
                    ("batch_execute", {"commands": "bad"}),
                    ("batch_execute", {"commands": [], "parallel": 1}),
                    ("manage_tools", {"action": "invalid"}),
                    ("execute_custom_tool", {"tool_name": "fixture", "parameters": []}),
                    ("strict_fixture", {"flag": False, "unity_instance": True}),
                    ("strict_fixture", {"flag": False, "unity_instance": []}),
                    ("manage_components", {"action": "remove", "target": "Player", "component_type": "Transform", "component_index": True}),
                    ("manage_components", {"action": "remove", "target": "Player", "component_type": "Transform", "component_index": 1.5}),
                    ("manage_physics", {"action": "ping", "component_index": True}),
                    ("manage_vfx", {"action": "ping", "component_index": 1.5}),
                    ("manage_graphics", {"action": "ping", "index": True}),
                    ("manage_material", {"action": "ping", "slot": 1.5}),
                    ("manage_scene", {"action": "load", "build_index": True}),
                ):
                    events.clear()
                    result = await client.call_tool(name, payload, raise_on_error=False)
                    assert result.is_error, (name, payload)
                    assert events == [], (name, payload, events)
                    if name == "strict_fixture" and "unity_instance" not in payload:
                        error = " ".join(block.text for block in result.content if hasattr(block, "text"))
                        assert ("count" if payload.get("count") is True else "flag") in error, error
                        assert ("int_type" if payload.get("count") is True else
                                "missing_argument" if "flag" not in payload else "bool_type") in error, error
                events.clear()
                valid = await client.call_tool("strict_fixture", {"flag": False, "count": 0})
                assert not valid.is_error and valid.structured_content == {"flag": False, "count": 0}
                assert "discover" in events and "handler" in events
                events.clear()
                result = await client.call_tool("batch_execute", {"commands": [
                    {"tool": "manage_gameobject", "params": {"action": "modify", "setActive": False}},
                ]})
                assert not result.is_error
                assert "get_editor_state" in events and "batch_execute" in events
                assert payloads[-1][0] == "batch_execute"
                assert payloads[-1][1]["commands"][0]["params"] == {"action": "modify", "setActive": False}
        asyncio.run(scenario())
    '''.replace("MODE", repr(mode)), tmp_path)


def test_meta_local_errors_do_not_probe_editor_state(tmp_path):
    run_dispatch_scenario('''
        import asyncio, importlib, sys
        from unittest.mock import AsyncMock, Mock
        sys.path.insert(0, "src")
        from core.config import config
        batch = importlib.import_module("services.tools.batch_execute")
        custom = importlib.import_module("services.tools.execute_custom_tool")
        select = importlib.import_module("services.tools.set_active_instance")
        async def scenario():
            config.transport_mode = "stdio"
            config.http_remote_hosted = False
            context = Mock()
            context.get_state = AsyncMock(return_value="Project@hash")
            state = AsyncMock(side_effect=AssertionError("Editor state queried for invalid input"))
            batch._get_max_commands_from_editor_state = state
            for commands in ([{"tool": "   "}], [{"tool": "batch_execute"}], [{"tool": "BATCH_EXECUTE"}]):
                try:
                    await batch.batch_execute(context, commands)
                except ValueError:
                    pass
                else:
                    raise AssertionError("Invalid batch accepted")
                state.assert_not_awaited()
            custom.get_unity_instance_from_context = state
            for name, parameters in (("", {}), ("   ", {}), ("fixture", [])):
                result = await custom.execute_custom_tool(context, name, parameters)
                assert result.success is False
                state.assert_not_awaited()
            select.is_sessionless = lambda context: False
            select.get_unity_connection_pool = Mock(side_effect=AssertionError("Blank instance triggers discovery"))
            result = await select.set_active_instance(context, "   ")
            assert result["success"] is False
            select.get_unity_connection_pool.assert_not_called()
        asyncio.run(scenario())
    ''', tmp_path)


def test_argument_guard_preserves_aliases_and_runs_callbacks_only_in_sdk(tmp_path):
    run_dispatch_scenario('''
        import asyncio, sys
        from enum import Enum
        from typing import Annotated, TypeAliasType
        sys.path.insert(0, "src")
        from fastmcp import Client, FastMCP
        from pydantic import AfterValidator, BeforeValidator, Discriminator, Field, StrictBool, StrictInt, Tag
        from fastmcp.tools.function_tool import FunctionTool
        from pydantic_core import SchemaValidator
        from transport.tool_input_validation import _structural_schema, validate_tool_arguments

        callbacks = []
        def before(value):
            callbacks.append("before")
            return value == "accepted" if isinstance(value, str) else value
        def after(value):
            callbacks.append("after")
            return value
        server = FastMCP("callback-contract")
        @server.tool
        async def fixture(
            enabled: Annotated[StrictBool, BeforeValidator(before)],
            count: Annotated[StrictInt, AfterValidator(after), Field(alias="itemCount")],
        ) -> dict:
            return {"enabled": enabled, "count": count}

        async def scenario():
            tool = await server.get_tool("fixture")
            assert isinstance(tool, FunctionTool)
            validate_tool_arguments(tool, {"enabled": "accepted", "itemCount": 0})
            assert callbacks == []
            async with Client(server) as client:
                result = await client.call_tool("fixture", {"enabled": "accepted", "itemCount": 0})
                assert result.structured_content == {"enabled": True, "count": 0}
            assert callbacks == ["before", "after"]
            callbacks.clear()
            try:
                validate_tool_arguments(tool, {"enabled": True, "itemCount": True})
            except Exception as error:
                assert "itemCount" in str(error) and "int_type" in str(error), error
            else:
                raise AssertionError("Invalid strict alias accepted")
            assert callbacks == []
            @server.tool
            async def union_fixture(value: Annotated[int, AfterValidator(after), Tag("number")] | Annotated[str, Tag("text")]) -> dict:
                return {"value": value}
            union_tool = await server.get_tool("union_fixture")
            validate_tool_arguments(union_tool, {"value": 3})
            assert callbacks == []
            async with Client(server) as client:
                result = await client.call_tool("union_fixture", {"value": 3})
                assert result.structured_content == {"value": 3}
            assert callbacks == ["after"]
            callbacks.clear()
            def choose(value):
                callbacks.append("choose")
                return "number" if isinstance(value, int) else "text"
            Choice = Annotated[
                Annotated[StrictInt, AfterValidator(after), Tag("number")] | Annotated[str, Tag("text")],
                Discriminator(choose),
            ]
            @server.tool
            async def choice_fixture(value: Choice) -> dict:
                return {"value": value}
            choice_tool = await server.get_tool("choice_fixture")
            validate_tool_arguments(choice_tool, {"value": 3})
            assert callbacks == []
            async with Client(server) as client:
                result = await client.call_tool("choice_fixture", {"value": 3})
                assert result.structured_content == {"value": 3}
            assert callbacks == ["choose", "after"]
            callbacks.clear()
            class ChoiceEnum(Enum):
                FIRST = "first"
                @classmethod
                def _missing_(cls, value):
                    callbacks.append("enum_missing")
                    return cls.FIRST
            @server.tool
            async def enum_fixture(value: ChoiceEnum) -> dict:
                return {"value": value.value}
            enum_tool = await server.get_tool("enum_fixture")
            validate_tool_arguments(enum_tool, {"value": "compat"})
            assert callbacks == []
            async with Client(server) as client:
                result = await client.call_tool("enum_fixture", {"value": "compat"})
                assert result.structured_content == {"value": "first"}
            assert callbacks == ["enum_missing"]
            callbacks.clear()
            Alias = TypeAliasType("Alias", Annotated[StrictInt, AfterValidator(after)])
            @server.tool
            async def alias_fixture(values: list[Alias], backup: Alias) -> dict:
                return {"values": values, "backup": backup}
            alias_tool = await server.get_tool("alias_fixture")
            validate_tool_arguments(alias_tool, {"values": [3], "backup": 0})
            assert callbacks == []
            async with Client(server) as client:
                result = await client.call_tool("alias_fixture", {"values": [3], "backup": 0})
                assert result.structured_content == {"values": [3], "backup": 0}
            assert callbacks == ["after", "after"]
            callbacks.clear()
            try:
                validate_tool_arguments(alias_tool, {"values": [True], "backup": 0})
            except Exception as error:
                assert "values[0]" in str(error) and "int_type" in str(error), error
            else:
                raise AssertionError("Invalid strict alias type accepted")
            assert callbacks == []
            FactoryAlias = TypeAliasType("FactoryAlias", Annotated[StrictInt, Field(default_factory=lambda: callbacks.append("factory") or 3)])
            @server.tool
            async def factory_fixture(values: list[FactoryAlias], backup: FactoryAlias) -> dict:
                return {"values": values, "backup": backup}
            factory_tool = await server.get_tool("factory_fixture")
            validate_tool_arguments(factory_tool, {"values": [3], "backup": 0})
            assert callbacks == []
            try:
                validate_tool_arguments(factory_tool, {"values": [True], "backup": 0})
            except Exception as error:
                assert "values[0]" in str(error) and "int_type" in str(error), error
            else:
                raise AssertionError("Invalid supplied default-factory alias accepted")
            assert callbacks == []
            factory_schema = {
                "type": "arguments", "arguments_schema": [{
                    "name": "value", "mode": "positional_or_keyword",
                    "schema": {"type": "default", "schema": {"type": "int"},
                               "default_factory": lambda: callbacks.append("factory") or 3},
                }],
            }
            validator = SchemaValidator(_structural_schema(factory_schema))
            validator.validate_python({})
            assert callbacks == []
        asyncio.run(scenario())
    ''', tmp_path)


def test_all_registered_tools_have_safe_bounded_argument_guards(tmp_path):
    run_dispatch_scenario('''
        import asyncio, sys
        sys.path.insert(0, "src")
        from fastmcp import FastMCP
        from fastmcp.tools.function_tool import FunctionTool
        from services.tools import register_all_tools
        from transport.tool_input_validation import _argument_validator
        from core.config import config
        config.transport_mode = "stdio"
        config.http_remote_hosted = False
        server = FastMCP("catalog-guard")
        register_all_tools(server)
        async def scenario():
            tools = await server.list_tools(run_middleware=False)
            assert len(tools) >= 51
            _argument_validator.cache_clear()
            for tool in tools:
                assert isinstance(tool, FunctionTool), tool.name
                _argument_validator(tool.fn)
            assert _argument_validator.cache_info().currsize == len(tools)
            def first(value: int):
                raise AssertionError("Handler ran")
            def replacement(value: str):
                raise AssertionError("Handler ran")
            assert _argument_validator(first) is not _argument_validator(replacement)
            for index in range(130):
                def distinct(value: int):
                    raise AssertionError("Handler ran")
                _argument_validator(distinct)
            assert _argument_validator.cache_info().currsize == 128
        asyncio.run(scenario())
    ''', tmp_path)
