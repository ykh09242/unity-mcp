"""Bounded read-only reference diagnostic public contracts."""

from dataclasses import dataclass
from unittest.mock import AsyncMock

import anyio
import pytest

from services.tools import manage_asset as assets
from services.tools import manage_scene as scenes


@dataclass(frozen=True, slots=True)
class ReferenceWire:
    send: AsyncMock
    asset_gate: AsyncMock
    scene_gate: AsyncMock
    asset_context: AsyncMock
    scene_context: AsyncMock


@pytest.fixture
def wire(monkeypatch):
    # Given: public wrappers with only the Editor transport replaced.
    send = AsyncMock(return_value={"success": True, "data": {"scanComplete": False}})
    asset_gate, scene_gate = AsyncMock(return_value=None), AsyncMock(return_value=None)
    asset_context, scene_context = AsyncMock(return_value="owned"), AsyncMock(return_value="owned")
    for module, gate, context in (
        (assets, asset_gate, asset_context),
        (scenes, scene_gate, scene_context),
    ):
        monkeypatch.setattr(module, "get_unity_instance_from_context", context)
        monkeypatch.setattr(module, "preflight", gate)
        monkeypatch.setattr(module, "send_with_unity_instance", send)
    return ReferenceWire(send, asset_gate, scene_gate, asset_context, scene_context)


@pytest.mark.parametrize(
    "action", ["get_dependencies", "get_reverse_references", "preview_delete", "preview_move"]
)
def test_asset_reference_actions_forward_explicit_budgets_without_refresh(wire, action):
    async def call():
        return await assets.manage_asset(
            None,
            action=action,
            path="Assets/a.asset",
            scan_root="Assets/Owned",
            recursive=True,
            max_assets=32,
            max_work=200,
            page_size=7,
            page_number=2,
            reference_revision="mcp-ref-v1_" + "a" * 64,
            **({"destination": "Assets/Owned/b.asset"} if action == "preview_move" else {}),
        )

    # When: the caller asks for a bounded second page or explicit preview recheck.
    result = anyio.run(call)
    # Then: the declared scan and exact revision reach the native boundary once.
    assert result["success"] is True
    params = wire.send.await_args.args[3]
    assert params["action"] == action
    assert params["maxWork"] == 200 and params["maxAssets"] == 32
    assert params["scanRoot"] == "Assets/Owned"
    assert params["referenceRevision"] == "mcp-ref-v1_" + "a" * 64
    wire.asset_gate.assert_awaited_once_with(None, wait_for_no_compile=True, refresh_if_dirty=False)


@pytest.mark.parametrize(
    "extra",
    [
        {"max_assets": 4097},
        {"max_work": True},
        {"max_work": "2.5"},
        {"page_size": 101},
        {"page_number": 0},
        {"reference_revision": "guess"},
        {"scan_root": "../elsewhere"},
        {"recursive": "yes"},
        {"properties": {}},
        {"generate_preview": True},
        {"labels": []},
    ],
)
def test_asset_reference_invalid_options_are_rejected_before_io(wire, extra):
    async def call():
        return await assets.manage_asset(
            None, action="get_dependencies", path="Assets/a.asset", **extra
        )

    # When: malformed or unrelated options would change/expand the scan.
    result = anyio.run(call)
    # Then: refusal is local and no command is sent.
    assert result["success"] is False
    wire.send.assert_not_awaited()
    wire.asset_context.assert_not_awaited()


@pytest.mark.parametrize(
    "scope,path",
    [
        ("active_scene", None),
        ("loaded_scenes", None),
        ("prefab", "Assets/a.prefab"),
        ("scriptable_object", "Assets/a.asset"),
    ],
)
def test_missing_reference_scope_and_paging_are_forwarded_without_refresh(wire, scope, path):
    async def call():
        return await scenes.manage_scene(
            None,
            action="validate_references",
            reference_scope=scope,
            path=path,
            max_objects=900,
            max_work=10000,
            page_size=31,
            page_number=8,
        )

    # When: diagnosing loaded or explicitly selected asset objects.
    result = anyio.run(call)
    # Then: the scope remains explicit, bounded and read-only.
    assert result["success"] is True
    params = wire.send.await_args.args[3]
    assert params["referenceScope"] == scope and params["pageNumber"] == 8
    assert params["maxObjects"] == 900 and params["maxWork"] == 10000
    wire.scene_gate.assert_awaited_once_with(None, wait_for_no_compile=True, refresh_if_dirty=False)


@pytest.mark.parametrize(
    "extra",
    [
        {"reference_scope": "all"},
        {"reference_scope": "prefab"},
        {"max_objects": 4097},
        {"max_work": False},
        {"page_size": 101},
        {"auto_repair": True},
        {"additive": True},
        {"reference_revision": "bad"},
    ],
)
def test_missing_reference_invalid_options_never_reach_editor(wire, extra):
    async def call():
        return await scenes.manage_scene(None, action="validate_references", **extra)

    # When: the request is unbounded, contradictory or asks to repair.
    result = anyio.run(call)
    # Then: a clear local refusal precedes any Editor activity.
    assert result["success"] is False
    wire.send.assert_not_awaited()
    wire.scene_context.assert_not_awaited()


@pytest.mark.parametrize(
    "module,action,extra",
    [
        (assets, "get_info", {"path": "Assets/a", "max_assets": 1}),
        (scenes, "validate", {"reference_scope": "loaded_scenes"}),
    ],
)
def test_reference_options_are_not_silently_ignored_on_legacy_actions(wire, module, action, extra):
    async def call():
        fn = module.manage_asset if module is assets else module.manage_scene
        return await fn(None, action=action, **extra)

    # When: new diagnostic options are mixed into a legacy action.
    result = anyio.run(call)
    # Then: the wrapper refuses rather than silently executing a different scope.
    assert result["success"] is False
    wire.send.assert_not_awaited()


@pytest.mark.parametrize("action", ["move", "delete"])
def test_guarded_mutation_accepts_preview_revision_without_refresh_or_retry(wire, action):
    async def call():
        return await assets.manage_asset(
            None,
            action=action,
            path="Assets/a.asset",
            destination="Assets/b.asset" if action == "move" else None,
            reference_revision="mcp-ref-v1_" + "a" * 64,
            scan_root="Assets/Owned",
            max_assets=20,
            max_work=200,
        )

    # When: applying an observed preview through the existing actual mutation.
    result = anyio.run(call)
    # Then: the native boundary gets the exact guard and no dirty refresh/retry.
    assert result["success"] is True
    assert wire.send.await_args.args[3]["referenceRevision"] == "mcp-ref-v1_" + "a" * 64
    assert wire.send.await_args.kwargs["retry_on_reload"] is False
    wire.asset_gate.assert_awaited_once_with(None, wait_for_no_compile=True, refresh_if_dirty=False)


@pytest.mark.parametrize(
    "module,tool_name,options",
    [
        (
            assets,
            "manage_asset",
            {
                "action": "preview_move",
                "path": "Assets/a.asset",
                "destination": "Assets/b.asset",
                "scan_root": "Assets/Owned",
                "max_assets": 20,
                "max_work": 200,
            },
        ),
        (
            scenes,
            "manage_scene",
            {
                "action": "validate_references",
                "reference_scope": "scriptable_object",
                "path": "Assets/a.asset",
                "max_objects": 20,
                "max_work": 200,
            },
        ),
    ],
)
def test_sdk_schema_exposes_and_serializes_reference_actions(wire, module, tool_name, options):
    from fastmcp import Client, FastMCP

    async def call():
        # Given: the actual callable is published through FastMCP schema generation.
        server = FastMCP("reference-diagnostics-contract")
        server.tool()(getattr(module, tool_name))
        async with Client(server) as client:
            tools = await client.list_tools()
            schema = next(tool for tool in tools if tool.name == tool_name).input_schema
            assert options["action"] in schema["properties"]["action"]["enum"]
            # When: invoking the diagnostic through the SDK, not a direct Python call.
            result = await client.call_tool(tool_name, options)
            # Then: schema options survive validation and the returned partial status is intact.
            assert result.structured_content == {"success": True, "data": {"scanComplete": False}}
            assert wire.send.await_args.args[3]["maxWork"] == 200
            assert wire.send.await_args.args[3]["path"] == "Assets/a.asset"

    anyio.run(call)


@pytest.mark.parametrize(
    "module,tool_name,options",
    [
        (
            assets,
            "manage_asset",
            {
                "action": "get_dependencies",
                "path": "Assets/OwnedFixture/Resources/GameData/fixture.asset",
                "scan_root": "Assets/OwnedFixture",
            },
        ),
        (
            scenes,
            "manage_scene",
            {
                "action": "validate_references",
                "reference_scope": "scriptable_object",
                "path": "Assets/OwnedFixture/Resources/GameData/fixture.asset",
            },
        ),
    ],
)
def test_product_admission_does_not_reserve_user_folder_names(wire, module, tool_name, options):
    # Given: a synthetic path beneath an owned fixture root, with all I/O mocked.
    async def call():
        # When: an ordinary contained user folder name crosses product admission.
        return await getattr(module, tool_name)(None, **options)

    result = anyio.run(call)
    # Then: workspace editing restrictions do not become runtime product path bans.
    assert result["success"] is True
    assert wire.send.await_args.args[3]["path"] == options["path"]
