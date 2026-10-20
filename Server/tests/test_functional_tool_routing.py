"""Cross-feature dispatch, full action coverage and cancellation regression tests."""

from tests.test_tool_group_status import run_sdk_regression


def test_partition_preserves_every_existing_action_and_input_type():
    run_sdk_regression("""
        import asyncio, inspect, sys
        from collections import Counter
        from importlib import import_module
        from typing import Literal, get_args, get_origin
        sys.path.insert(0, "src")
        from fastmcp import FastMCP
        from core.config import config
        from services.registry.capability_catalog import CAPABILITIES, LEGACY_TOOLS
        from services.registry import get_registered_tools
        from services.tools import register_all_tools

        def literal_values(annotation):
            if get_origin(annotation) is Literal:
                return set(get_args(annotation))
            values = set()
            for argument in get_args(annotation):
                values.update(literal_values(argument))
            return values

        async def scenario():
            config.transport_mode = "http"
            server = FastMCP("partition")
            register_all_tools(server, project_scoped_tools=False)
            server.enable(components={"tool"})
            schemas = {tool.name: tool.parameters for tool in await server.list_tools(run_middleware=False)}
            declared = {item["name"]: item for item in get_registered_tools()}
            assert len({item.name for item in CAPABILITIES}) == len(CAPABILITIES)
            for source in LEGACY_TOOLS:
                module = import_module("services.tools." + source)
                signature = inspect.signature(getattr(module, source))
                actions = set(getattr(module, "ALL_ACTIONS", ())) or literal_values(signature.parameters["action"].annotation)
                profiles = [item for item in CAPABILITIES if item.source == source]
                counts = Counter(action for item in profiles for action in item.actions)
                assert set(counts) == actions and set(counts.values()) == {1}, (source, counts, actions)
                admitted = {name for item in profiles for name in item.parameters}
                assert admitted == set(signature.parameters) - {"ctx", "action"}, (source, admitted ^ (set(signature.parameters) - {"ctx", "action"}))
                for profile in profiles:
                    schema = schemas[profile.name]
                    assert set(schema["properties"]) == {"action", *profile.parameters}
                    for name in profile.parameters:
                        assert schema["properties"][name] == schemas[source]["properties"][name], (profile.name, name)
                    assert declared[profile.name]["unity_target"] == profile.name, profile.name
                    assert declared[profile.name]["group"] == profile.group
        asyncio.run(scenario())
    """)


def test_feature_dispatch_retains_original_payloads_and_response_processing():
    run_sdk_regression("""
        import asyncio, copy, sys
        from importlib import import_module
        from unittest.mock import AsyncMock, MagicMock
        sys.path.insert(0, "src")
        from fastmcp import FastMCP
        from core.config import config
        from services.registry import get_registered_tools
        from services.registry.capability_catalog import CAPABILITIES
        from services.tools import register_all_tools

        cases = [
            ("query_physics", {"action": "raycast", "origin": [0, 0, 0], "direction": [0, 1, 0], "max_distance": 0}),
            ("apply_physics_force", {"action": "apply_force", "target": "42", "force": [0, 1, 0], "position": [2, 3, 4]}),
            ("manage_rigidbody", {"action": "configure_rigidbody", "target": "42", "properties": {"mass": 2}}),
            ("manage_renderer_features", {"action": "feature_configure", "index": 0, "settings": {"active": False}}),
            ("manage_render_volumes", {"action": "volume_set_properties", "target": "42", "properties": {"blend_distance": 0}}),
            ("manage_cinemachine", {"action": "ensure_brain", "properties": {"camera": "42", "defaultBlendDuration": 0}}),
            ("capture_camera", {"action": "screenshot", "include_image": False, "max_resolution": 64}),
            ("gameobject_duplicate", {"action": "duplicate", "target": "42", "new_name": "Copy", "offset": [1, 0, 0]}),
            ("prefab_inspect", {"action": "get_hierarchy", "prefab_path": "Assets/Fixture.prefab", "page_size": 2}),
            ("scene_inspect", {"action": "get_active"}),
            ("asset_inspect", {"action": "search", "path": "Assets", "metadata_only": True, "page_size": 2}),
            ("ui_source", {"action": "read", "path": "Assets/Fixture.uxml"}),
            ("ui_capture", {"action": "render_ui", "target": "42", "width": 64, "height": 64}),
        ]

        async def scenario():
            config.transport_mode = "http"
            server = FastMCP("dispatch")
            register_all_tools(server, project_scoped_tools=False)
            tools = {item["name"]: item["func"] for item in get_registered_tools()}
            profiles = {item.name: item for item in CAPABILITIES}
            context = MagicMock()
            context.info = AsyncMock()
            context.warning = AsyncMock()
            context.get_state = AsyncMock(return_value=None)
            for name, arguments in cases:
                profile = profiles[name]
                module = import_module("services.tools." + profile.source)
                calls = []
                async def send(_sender, instance, command, params, **options):
                    calls.append((instance, command, copy.deepcopy(params), options))
                    await asyncio.sleep(0)
                    return {"success": True, "data": {}, "message": "fixture"}
                module.get_unity_instance_from_context = AsyncMock(return_value="Fixture@1234")
                module.send_with_unity_instance = send
                if hasattr(module, "send_mutation"):
                    module.send_mutation = send
                if hasattr(module, "preflight"):
                    module.preflight = AsyncMock(return_value=None)
                expected = await tools[profile.source](context, **copy.deepcopy(arguments))
                actual = await tools[name](context, **copy.deepcopy(arguments))
                assert len(calls) == 2, (name, calls, expected, actual)
                before, after = calls
                assert before[1] == profile.source and after[1] == name, calls
                assert before[0] == after[0] and before[2:] == after[2:], calls
                assert expected == actual, (name, expected, actual)
                if name == "asset_inspect":
                    assert after[2]["action"] == "search_metadata_v1"
        asyncio.run(scenario())
    """)


def test_command_routing_is_task_local_and_restored_after_failure_or_cancellation():
    run_sdk_regression("""
        import asyncio, inspect, sys
        sys.path.insert(0, "src")
        from services.registry.capability_adapter import capability_command, register_capability
        from services.registry.capability_catalog import CAPABILITIES
        from services.tools.manage_physics import manage_physics

        async def scenario():
            entered = asyncio.Queue()
            release = asyncio.Event()
            observed = []
            async def implementation(**arguments):
                observed.append((arguments["action"], capability_command("manage_physics")))
                assert capability_command("manage_scene") == "manage_scene"
                entered.put_nowait(arguments["action"])
                await release.wait()
                raise ValueError("intentional fixture failure")
            implementation.__signature__ = inspect.signature(manage_physics)
            profiles = {item.name: item for item in CAPABILITIES}
            query = register_capability(profiles["query_physics"], implementation)
            joints = register_capability(profiles["manage_physics_joints"], implementation)
            async def run(tool, action):
                try:
                    await tool(None, action=action)
                except (ValueError, asyncio.CancelledError):
                    pass
                assert capability_command("manage_physics") == "manage_physics"
            first = asyncio.create_task(run(query, "raycast"))
            second = asyncio.create_task(run(joints, "add_joint"))
            await entered.get()
            await entered.get()
            assert capability_command("manage_physics") == "manage_physics"
            second.cancel()
            release.set()
            await asyncio.gather(first, second)
            assert set(observed) == {("raycast", "query_physics"), ("add_joint", "manage_physics_joints")}
        asyncio.run(scenario())
    """)
