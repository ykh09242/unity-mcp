"""Reserved native backend commands stay out of public catalogs; raw admission survives."""

import pytest


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
@pytest.mark.parametrize("hosted", [False, True])
def test_real_native_catalog_does_not_publish_backend_aliases(tmp_path, mode, hosted):
    # Given actual hub registration and CustomToolService publication in a source-guarded child.
    import os
    from pathlib import Path
    import subprocess
    import sys
    import textwrap

    root = Path(__file__).resolve().parents[2]
    source = root / "Server/src"
    program = r"""
import importlib, json, pathlib, sys
from types import SimpleNamespace
from unittest.mock import AsyncMock
import anyio
from fastmcp import Client, FastMCP
from core.config import config
from transport.models import RegisterMessage, RegisterToolsMessage
from transport.plugin_hub import PluginHub
from transport.plugin_registry import PluginRegistry
from contextlib import AsyncExitStack
from tests.remote_credentials import remote_credentials
import services.custom_tool_service as custom
console = importlib.import_module("services.tools.read_console")
source = pathlib.Path(sys.argv[1]).resolve()
assert pathlib.Path(console.__file__).resolve() == source / "services/tools/read_console.py"
assert pathlib.Path(custom.__file__).resolve() == source / "services/custom_tool_service.py"
mode, hosted = sys.argv[2], sys.argv[3] == "true"
aliases = {"tail_console", "inspect_menu_item", "execute_menu_item_observed", "manage_components_selected", "manage_object_handles_v1"}
parents = {"read_console": console}
for name in ("execute_menu_item", "manage_components"):
    parents[name] = importlib.import_module("services.tools." + name)
    assert pathlib.Path(parents[name].__file__).resolve() == source / ("services/tools/" + name + ".py")
if (source / "services/tools/manage_object_handles.py").is_file():
    parents["manage_object_handles"] = importlib.import_module("services.tools.manage_object_handles")
    assert pathlib.Path(parents["manage_object_handles"].__file__).resolve() == source / "services/tools/manage_object_handles.py"
    handle_wrapper = parents["manage_object_handles"].manage_object_handles
else:
    # The standalone fix precedes DEV11; only its publication name/target is needed here.
    from services.registry import mcp_for_unity_tool
    @mcp_for_unity_tool(name="manage_object_handles", unity_target="manage_object_handles_v1")
    async def handle_wrapper():
        raise AssertionError("Publication-only placeholder must never execute")
public_wrapper_names = set(parents) | {"manage_object_handles"}
async def scenario():
    config.transport_mode = "http"
    config.http_remote_hosted = hosted
    app = FastMCP("owned-tail-catalog")
    for name, parent in parents.items():
        app.tool(name=name)(getattr(parent, name))
    if "manage_object_handles" not in parents:
        app.tool(name="manage_object_handles")(handle_wrapper)
    service = custom.CustomToolService(app)
    registry = PluginRegistry()
    PluginHub.configure(registry, mcp=app)
    hub = PluginHub({"type": "websocket"}, AsyncMock(), AsyncMock())
    PluginHub._notify_mcp_tool_list_changed = AsyncMock()
    user = "synthetic-owner" if hosted else None
    async with AsyncExitStack() as credentials:
        if hosted:
            auth = await credentials.enter_async_context(remote_credentials({"requester:owner": "synthetic-owner", "plugin:owned": "synthetic-owner"}))
            await credentials.enter_async_context(auth.requester("requester:owner"))
        credentials.push_async_callback(PluginHub.shutdown)
        socket = AsyncMock()
        socket.state = SimpleNamespace(user_id=user)
        if hosted:
            await auth.attach_socket(socket, "plugin:owned")
        await hub._handle_register(socket, RegisterMessage(project_name="Owned", project_hash="owned"))
        await hub._handle_register_tools(socket, RegisterToolsMessage(tools=[
            {"name": name} for name in sorted(aliases | {"read_console", "execute_menu_item", "manage_components", "owned_public"})
        ]))
        # When the actual local MCP and project/hosted public catalogs are listed.
        async with Client(app, mode=mode) as client:
            public_names = {t.name for t in await client.list_tools()}
        project_names = {t.name for t in await service.list_registered_tools("owned", user_id=user)}
        raw_names = {t.name for t in await PluginHub.get_tools_for_project("owned", user_id=user)}
        resource = importlib.import_module("services.resources.custom_tools")
        resource.get_unity_instance_from_context = AsyncMock(return_value="Owned@owned")
        resource.resolve_project_id_for_unity_instance = lambda value: "owned"
        resource.get_user_id_from_context = AsyncMock(return_value=user)
        resource_result = await resource.get_custom_tools(SimpleNamespace())
        assert resource_result.data.tool_count == len(project_names)
        assert {t.name for t in resource_result.data.tools} == project_names
        # Then the alias remains in raw native metadata but is absent from both public views.
        failures = []
        if aliases & public_names: failures.append("backend aliases published in MCP list_tools: " + repr(sorted(aliases & public_names)))
        if aliases & project_names: failures.append("backend aliases exposed by public project list: " + repr(sorted(aliases & project_names)))
        if "owned_public" not in project_names: failures.append("ordinary project custom tool disappeared")
        if not aliases.issubset(raw_names): failures.append("raw native alias descriptors were removed")
        if not hosted and "owned_public" not in public_names: failures.append("ordinary global custom tool disappeared")
        if not public_wrapper_names.issubset(public_names): failures.append("parent public wrappers disappeared: " + repr(sorted(public_wrapper_names - public_names)))
        print("PLAY04_CATALOG", json.dumps({"mode":mode,"hosted":hosted,"public":sorted(public_names),"project":sorted(project_names),"raw":sorted(raw_names),"failures":failures}))
        assert not failures, failures
anyio.run(scenario)
"""
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join((str(source.parent), str(source))),
        "PYTHONDONTWRITEBYTECODE": "1",
        "UNITY_MCP_DISABLE_TELEMETRY": "true",
    }
    for name in (
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "TEMP",
        "TMP",
        "XDG_DATA_HOME",
        "XDG_CACHE_HOME",
        "UNITY_MCP_LOG_DIR",
    ):
        env[name] = str(tmp_path)
    env.pop("PYTEST_CURRENT_TEST", None)
    # When only synthetic definitions enter the real catalog pipeline (no command execution).
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            textwrap.dedent(program),
            str(source),
            mode,
            str(hosted).lower(),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=40,
    )
    # Then the real publication assertions, not a handregistered-only SDK surrogate, hold.
    assert result.returncode == 0, result.stdout + result.stderr


def test_exact_backend_reservation_preserves_raw_catalog_and_atomic_foreign_ownership(tmp_path):
    # Given task-isolated real service/provider objects and legacy-published alias ownership.
    import os
    from pathlib import Path
    import subprocess
    import sys
    import textwrap

    source = Path(__file__).resolve().parents[1] / "src"
    program = r"""
import anyio, pathlib, sys
from fastmcp import FastMCP
from core.config import config
from models.models import ToolDefinitionModel
import services.custom_tool_service as module
assert pathlib.Path(module.__file__).resolve() == pathlib.Path(sys.argv[1]).resolve() / "services/custom_tool_service.py"
reserved = frozenset({"tail_console", "inspect_menu_item", "execute_menu_item_observed", "manage_components_selected", "manage_object_handles_v1"})
def definitions(): return [ToolDefinitionModel(name=name) for name in sorted(reserved | {"owned_public"})]
async def scenario():
    config.http_remote_hosted = False
    config.transport_mode = "stdio"
    # Manual scoped and stdio scoped catalogs must hide only the exact backend name.
    for kind in ("manual", "stdio"):
        service = module.CustomToolService(FastMCP("owned-tail-" + kind), project_scoped_tools=True)
        service.replace_project_catalog("owned", definitions(), kind=kind, publish_global=False)
        names = {t.name for t in await service.list_registered_tools("owned")}
        assert names == {"owned_public"}, (kind, names)
        for name in reserved:
            assert (await service.get_tool_definition("owned", name)).name == name
    # Reconciliation must retire a formerly published owned alias, including rollback.
    for failure in ("remove", "visibility"):
        app = FastMCP("owned-tail-retire-" + failure)
        service = module.CustomToolService(app)
        module._BACKEND_ONLY_TOOL_NAMES = frozenset()  # Seed the pre-reservation upgrade state.
        service.replace_project_catalog("owned", definitions(), kind="stdio", generation="old")
        alias = service._owned_tools["tail_console"]
        source_before = service._sources
        owners_before = service._owned_tools
        provider = app._local_provider
        original_remove = provider._remove_component
        module._BACKEND_ONLY_TOOL_NAMES = reserved
        rolled_back = []
        def visibility_failure(): raise RuntimeError("Synthetic visibility failure")
        def remove_failure(key):
            original_remove(key)
            if key == alias.key: raise RuntimeError("Synthetic removal failure")
        if failure == "remove": provider._remove_component = remove_failure
        failed = False
        try:
            service.replace_project_catalog("owned", definitions(), kind="stdio", generation="new",
                visibility=(visibility_failure, lambda: rolled_back.append(True)) if failure == "visibility" else None)
        except RuntimeError:
            failed = True
        finally:
            provider._remove_component = original_remove
        assert failed, (failure, "reserved owned alias was not reconciled")
        assert service._sources is source_before and service._owned_tools is owners_before
        assert provider._components[alias.key] is alias
        if failure == "visibility": assert rolled_back == [True]
        service.replace_project_catalog("owned", definitions(), kind="stdio", generation="success")
        assert not reserved.intersection(service._owned_tools) and alias.key not in provider._components
        assert "owned_public" in service._owned_tools
        for name in reserved:
            assert (await service.get_tool_definition("owned", name)).name == name
        # An unrelated SDK owner at the reserved key must never be deleted during retirement.
        module._BACKEND_ONLY_TOOL_NAMES = frozenset()
        service.replace_project_catalog("owned", definitions(), kind="stdio", generation="legacy-again")
        alias = service._owned_tools["tail_console"]
        @app.tool(name="tail_console")
        async def foreign() -> dict: return {"synthetic_foreign": True}
        foreign_component = provider._components[alias.key]
        module._BACKEND_ONLY_TOOL_NAMES = reserved
        service.replace_project_catalog("owned", definitions(), kind="stdio", generation="retire-foreign")
        assert provider._components[alias.key] is foreign_component
        assert not reserved.intersection(service._owned_tools)
        assert "owned_public" in service._owned_tools
    print("PLAY04_RESERVED_EXACT_MANUAL_STDIO_ROLLBACK_FOREIGN=PASS")
anyio.run(scenario)
"""
    env = {
        **os.environ,
        "PYTHONPATH": str(source),
        "PYTHONDONTWRITEBYTECODE": "1",
        "UNITY_MCP_DISABLE_TELEMETRY": "true",
    }
    for name in (
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "TEMP",
        "TMP",
        "XDG_DATA_HOME",
        "XDG_CACHE_HOME",
        "UNITY_MCP_LOG_DIR",
    ):
        env[name] = str(tmp_path)
    env.pop("PYTEST_CURRENT_TEST", None)
    # When exact-name filtering and owned reconciliation are exercised without native calls.
    result = subprocess.run(
        [sys.executable, "-B", "-c", textwrap.dedent(program), str(source)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=40,
    )
    # Then raw admission and foreign/provider identities survive all public-list changes.
    assert result.returncode == 0, result.stdout + result.stderr
