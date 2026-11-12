"""Local target admission through the real SDK and instance middleware, without Unity."""

from types import SimpleNamespace
from functools import wraps
from importlib import import_module
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import AsyncMock

import pytest
from fastmcp import Client, Context, FastMCP

from core.config import ServerConfig, config
from fastmcp.exceptions import ToolError
from tests.integration.test_helpers import DummyContext
from transport.plugin_hub import NoUnitySessionError, PluginHub
from transport.unity_instance_middleware import UnityInstanceMiddleware

A = "Intended@aaaaaaaa"
B = "Unintended@bbbbbbbb"


@pytest.fixture
def target_server(monkeypatch):
    monkeypatch.setattr(config, "require_explicit_target", True, raising=False)
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(config, "transport_mode", "stdio")
    monkeypatch.setattr(PluginHub, "is_configured", lambda: False)
    inventory = [SimpleNamespace(id=B, hash="bbbbbbbb")]

    class Pool:
        def discover_all_instances(self, force_refresh=False):
            return inventory

        def resolve_instance(self, instance, force_refresh=False):
            for target in inventory:
                if target.id == instance:
                    return target
            raise ConnectionError("Selected project is unavailable")

    monkeypatch.setattr("transport.legacy.unity_connection.get_unity_connection_pool", Pool)
    registry = [
        {"name": name, "unity_target": name}
        for name in (
            "manage_scene",
            "batch_execute",
            "run_tests",
            "execute_custom_tool",
            "execute_menu_item",
        )
    ] + [{"name": "set_active_instance", "unity_target": None}]
    # The actual custom wrapper is server-dispatched despite running Unity code.
    next(entry for entry in registry if entry["name"] == "execute_custom_tool")["unity_target"] = (
        None
    )
    monkeypatch.setattr(
        "transport.unity_instance_middleware.get_registered_tools", lambda: registry
    )
    middleware = UnityInstanceMiddleware()
    server = FastMCP("explicit-target-policy")
    server.add_middleware(middleware)
    dispatched = []

    async def observed(ctx):
        selected = await ctx.get_state("unity_instance")
        dispatched.append(selected)
        return selected

    @server.tool
    async def manage_scene(ctx: Context, action: str = "get_active") -> str:
        return await observed(ctx)

    @server.tool
    async def batch_execute(ctx: Context, commands: list[dict]) -> str:
        return await observed(ctx)

    @server.tool
    async def run_tests(ctx: Context, mode: str = "EditMode") -> str:
        return await observed(ctx)

    @server.tool
    async def execute_custom_tool(
        ctx: Context, tool_name: str, parameters: dict | None = None
    ) -> str:
        return await observed(ctx)

    @server.tool
    async def execute_menu_item(ctx: Context, menu_path: str) -> str:
        return await observed(ctx)

    @server.tool(annotations={"readOnlyHint": True})
    async def innocent_extension(ctx: Context) -> str:
        return await observed(ctx)

    @server.tool
    async def set_active_instance(ctx: Context, instance: str) -> str:
        await middleware.set_active_instance(ctx, instance)
        return instance

    # These admission-matrix handlers substitute transport work. Trust only their
    # exact identities; replacements below still pass through the real trust check.
    from transport import unity_instance_middleware as policy

    actual_trust = policy.trusted_tool
    controlled_callbacks = {
        manage_scene,
        batch_execute,
        run_tests,
        execute_custom_tool,
        execute_menu_item,
        set_active_instance,
    }
    monkeypatch.setattr(
        policy,
        "trusted_tool",
        lambda callback, name: callback in controlled_callbacks or actual_trust(callback, name),
    )

    return SimpleNamespace(
        server=server,
        middleware=middleware,
        inventory=inventory,
        dispatched=dispatched,
        registry=registry,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_sole_unintended_project_read_does_not_authorize_mutation(target_server, mode):
    # Given only another project is open, discovery and a read remain available.
    async with Client(target_server.server, mode=mode) as client:
        await client.list_tools()
        assert (await client.call_tool("manage_scene")).data == B
        # When a mutation follows the automatic read, refuse before the handler.
        result = await client.call_tool("manage_scene", {"action": "save"}, raise_on_error=False)
    # Then only the read dispatched.
    assert result.is_error and "explicit_unity_target_required" in str(result.content)
    assert target_server.dispatched == [B]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,arguments",
    [
        ("run_tests", {}),
        ("execute_menu_item", {"menu_path": "Tests/Controlled"}),
        ("innocent_extension", {}),
        ("execute_custom_tool", {"tool_name": "extension"}),
        (
            "batch_execute",
            {
                "commands": [
                    {"tool": "manage_scene", "params": {"action": "get_active"}},
                    {"tool": "manage_scene", "params": {"action": "save"}},
                ]
            },
        ),
    ],
)
async def test_side_effectful_wrappers_require_deliberate_target(target_server, name, arguments):
    # Given no explicitly selected target, including misleading custom annotations.
    async with Client(target_server.server, mode="legacy") as client:
        # When execution is requested through each public wrapper.
        result = await client.call_tool(name, arguments, raise_on_error=False)
        # Then no handler is entered; an explicit choice admits the same operation.
        assert result.is_error and "explicit_unity_target_required" in str(result.content)
        assert target_server.dispatched == []
        assert (await client.call_tool(name, {**arguments, "unity_instance": B})).data == B
    assert target_server.dispatched == [B]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_inline_target_is_per_call_and_missing_target_never_retargets(target_server, mode):
    # Given two projects and an explicit inline target.
    target_server.inventory.append(SimpleNamespace(id=A, hash="aaaaaaaa"))
    async with Client(target_server.server, mode=mode) as client:
        assert (
            await client.call_tool("manage_scene", {"action": "save", "unity_instance": A})
        ).data == A
        # When A vanishes while B remains, neither its old selector nor no selector may write B.
        target_server.inventory.pop()
        unavailable = await client.call_tool(
            "manage_scene", {"action": "save", "unity_instance": A}, raise_on_error=False
        )
        assert unavailable.is_error and "explicit_unity_target_unavailable" in str(
            unavailable.content
        )
        result = await client.call_tool("manage_scene", {"action": "save"}, raise_on_error=False)
        assert result.is_error
    # Then exactly the explicitly targeted call ran.
    assert target_server.dispatched == [A]


@pytest.mark.asyncio
async def test_session_selection_is_explicit_and_stale_selection_fails_closed(target_server):
    # Given an explicit session selection, writes need no repeated inline selector.
    target_server.inventory.append(SimpleNamespace(id=A, hash="aaaaaaaa"))
    async with Client(target_server.server, mode="legacy") as client:
        assert (await client.call_tool("set_active_instance", {"instance": A})).data == A
        assert (await client.call_tool("manage_scene", {"action": "save"})).data == A
        # When the selected project disappears, a sole unrelated project cannot replace it.
        target_server.inventory.pop()
        unavailable = await client.call_tool(
            "manage_scene", {"action": "save"}, raise_on_error=False
        )
    # Then the unavailable selection did not dispatch.
    assert unavailable.is_error and "explicit_unity_target_unavailable" in str(unavailable.content)
    assert target_server.dispatched == [A]


@pytest.mark.asyncio
async def test_readonly_batch_and_legacy_default_remain_available(target_server, monkeypatch):
    # Given strict mode, a batch consisting only of reads is permitted.
    async with Client(target_server.server, mode="legacy") as client:
        assert (
            await client.call_tool(
                "batch_execute",
                {"commands": [{"tool": "manage_scene", "params": {"action": "get_active"}}]},
            )
        ).data == B
        # When the option is disabled, existing automatic mutation selection remains.
        monkeypatch.setattr(config, "require_explicit_target", False)
        assert (await client.call_tool("manage_scene", {"action": "save"})).data == B
    assert target_server.dispatched == [B, B]


@pytest.mark.parametrize(
    "raw,expected", [("true", True), ("1", True), ("false", False), ("0", False)]
)
def test_explicit_target_environment_is_parsed_at_construction(monkeypatch, raw, expected):
    # Given a server-owned environment setting, not a caller parameter.
    monkeypatch.setenv("UNITY_MCP_REQUIRE_EXPLICIT_TARGET", raw)
    # When a server configuration is constructed, then the policy is typed.
    assert ServerConfig().require_explicit_target is expected


@pytest.mark.parametrize("raw", ["", "yes", "tru", "2"])
def test_invalid_explicit_target_environment_rejects_startup(monkeypatch, raw):
    # Given a malformed server setting, when configuration loads, then fail closed.
    monkeypatch.setenv("UNITY_MCP_REQUIRE_EXPLICIT_TARGET", raw)
    with pytest.raises(ValueError, match="UNITY_MCP_REQUIRE_EXPLICIT_TARGET"):
        ServerConfig()


@pytest.mark.asyncio
async def test_old_automatic_state_and_other_session_cannot_authorize_write(target_server):
    # Given automatic state left by an earlier server, and a different explicit session.
    old = DummyContext()
    other = DummyContext()
    middleware = target_server.middleware
    await old.set_state(middleware._ACTIVE_INSTANCE_STATE_KEY, B)
    await middleware.set_active_instance(other, B)
    context = SimpleNamespace(fastmcp_context=old, message=SimpleNamespace(arguments={}))
    # When a write uses the old/other session, then it has no explicit authority.
    with pytest.raises(ToolError, match="explicit_unity_target_required"):
        await middleware._inject_unity_instance(context, require_explicit=True)
    # Clearing a deliberate selection also revokes its target authority.
    await middleware.clear_active_instance(other)
    context.fastmcp_context = other
    with pytest.raises(ToolError, match="explicit_unity_target_required"):
        await middleware._inject_unity_instance(context, require_explicit=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "commands",
    [
        [{"tool": "manage_scene", "params": {"action": "unknown_future_action"}}],
        [{"tool": "execute_custom_tool", "params": {"tool_name": "extension"}}],
        [{"tool": "batch_execute", "params": {"commands": []}}],
    ],
)
async def test_unclassifiable_batch_members_fail_closed_before_dispatch(target_server, commands):
    # Given even an explicit target cannot make unknown/nested/custom batch semantics safe to classify.
    async with Client(target_server.server, mode="legacy") as client:
        # When the outer wrapper cannot determine the complete permission set.
        result = await client.call_tool(
            "batch_execute", {"commands": commands, "unity_instance": B}, raise_on_error=False
        )
    # Then callers get a stable classification error and the handler never runs.
    assert result.is_error and "explicit_unity_target_classification_failed" in str(result.content)
    assert target_server.dispatched == []


@pytest.mark.asyncio
async def test_unreachable_http_target_cannot_continue_to_handler(target_server, monkeypatch):
    # Given an inline target is discoverable but its socket disappears during admission.
    monkeypatch.setattr(config, "transport_mode", "http")
    monkeypatch.setattr(PluginHub, "is_configured", lambda: True)

    async def discover(ctx):
        return [SimpleNamespace(id=A, hash="aaaaaaaa")]

    async def unavailable(*args, **kwargs):
        raise NoUnitySessionError("Selected socket disappeared")

    monkeypatch.setattr(target_server.middleware, "_discover_instances", discover)
    monkeypatch.setattr(PluginHub, "_resolve_session_id", unavailable)
    async with Client(target_server.server, mode="legacy") as client:
        # When the explicit target is unavailable, then do not fall through with an unset session.
        result = await client.call_tool(
            "manage_scene", {"action": "save", "unity_instance": A}, raise_on_error=False
        )
    assert result.is_error and "explicit_unity_target_unavailable" in str(result.content)
    assert target_server.dispatched == []


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["manage_scene", "set_active_instance", "get_execution_receipt"])
async def test_replacement_callback_cannot_borrow_builtin_read_or_control_name(target_server, name):
    # Given a replacement callback published under a built-in read/control name.
    entered = []

    async def replacement(ctx: Context, action: str = "get_active") -> str:
        entered.append(await ctx.get_state("unity_instance"))
        return "replacement entered"

    target_server.server.tool(name=name)(replacement)
    async with Client(target_server.server, mode="legacy") as client:
        # When no explicit target is supplied, then name/annotations cannot confer trust.
        result = await client.call_tool(name, {}, raise_on_error=False)
    assert result.is_error and "explicit_unity_target_required" in str(result.content)
    assert entered == []


@pytest.mark.asyncio
async def test_wrapped_name_spoof_and_registered_extension_remain_custom(
    target_server, monkeypatch
):
    # Given even copied built-in metadata cannot make replacement code trusted.
    from services.registry import mcp_for_unity_tool, tool_registry

    scene = import_module("services.tools.manage_scene")
    entered = []

    @wraps(scene.manage_scene)
    async def replacement(*args, **kwargs):
        entered.append("spoof")
        return "spoof"

    target_server.server.tool(name="manage_scene")(replacement)
    monkeypatch.setattr(tool_registry, "_tool_registry", list(tool_registry._tool_registry))

    @mcp_for_unity_tool(name="registered_extension", unity_target=None)
    async def extension(ctx: Context) -> str:
        entered.append(await ctx.get_state("unity_instance"))
        return "extension"

    target_server.registry.append({"name": "registered_extension", "unity_target": None})
    target_server.server.tool(name="registered_extension")(extension)
    async with Client(target_server.server, mode="legacy") as client:
        # When a replacement or decorated custom callback has no target, refuse both.
        for name, arguments in (
            ("manage_scene", {"action": "get_active"}),
            ("registered_extension", {}),
        ):
            result = await client.call_tool(name, arguments, raise_on_error=False)
            assert result.is_error and "explicit_unity_target_required" in str(result.content)
        assert entered == []
        # Then an explicit target admits a legitimate decorated extension as custom.
        assert (
            await client.call_tool("registered_extension", {"unity_instance": B})
        ).data == "extension"
    assert entered == [B]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_actual_builtin_callbacks_and_receipt_controls_keep_their_identity(
    target_server, monkeypatch, mode
):
    # Given real callbacks behind the same admission/logging/telemetry wrappers as registration.
    from core.logging_decorator import log_execution
    from core.telemetry_decorator import telemetry_tool
    from services.registry import operation_invocation as invocation
    from transport.execution_receipts import install_execution_receipts

    scene = import_module("services.tools.manage_scene")
    selection = import_module("services.tools.set_active_instance")
    monkeypatch.setattr(scene, "preflight", AsyncMock(return_value=None))
    dispatched = []

    async def send(sender, instance, command, parameters):
        dispatched.append(instance)
        return {"success": True, "data": {"selected": instance}}

    monkeypatch.setattr(scene, "send_with_unity_instance", send)
    monkeypatch.setattr(invocation, "capture_target", AsyncMock(return_value=None))
    monkeypatch.setattr(
        selection, "get_unity_instance_middleware", lambda: target_server.middleware
    )
    monkeypatch.setattr(
        selection,
        "get_unity_connection_pool",
        lambda: SimpleNamespace(discover_all_instances=lambda **kwargs: target_server.inventory),
    )
    server = FastMCP("actual-target-callback-identity")
    server.add_middleware(target_server.middleware)
    for callback, target in (
        (scene.manage_scene, "manage_scene"),
        (selection.set_active_instance, None),
    ):
        admitted = invocation.wrap_tool(callback, callback.__name__, target)
        wrapped = telemetry_tool(callback.__name__)(
            log_execution(callback.__name__, "Tool")(admitted)
        )
        server.tool(name=callback.__name__)(wrapped)
    install_execution_receipts(server, None)
    async with Client(server, mode=mode) as client:
        # When real read and receipt controls run without an explicit project, preserve behavior.
        assert (await client.call_tool("manage_scene", {"action": "get_active"})).data["success"]
        receipt = await client.call_tool("get_execution_receipt")
        assert not receipt.is_error
        target_server.inventory.append(SimpleNamespace(id=A, hash="aaaaaaaa"))
        if mode == "legacy":
            selected = await client.call_tool("set_active_instance", {"instance": A})
            assert selected.data["success"]
            arguments = {"action": "save"}
        else:
            arguments = {"action": "save", "unity_instance": A}
        # Then the genuine session control or explicit per-call selector permits the intended write.
        assert (await client.call_tool("manage_scene", arguments)).data["success"]
    assert dispatched == [B, A]


@pytest.mark.parametrize(
    "module,name",
    [
        ("manage_script", "apply_text_edits"),
        ("manage_script", "create_script"),
        ("manage_script", "delete_script"),
        ("manage_script", "manage_script"),
        ("script_apply_edits", "script_apply_edits"),
        ("script_change_set", "script_change_set"),
        ("batch_execute", "batch_execute"),
    ],
)
def test_composed_edit_wrappers_retain_actual_builtin_identity(module, name):
    from services.registry.operation_invocation import wrap_tool
    from transport.unity_instance_middleware import _trusted_builtin_callback

    callback = getattr(import_module("services.tools." + module), name)
    wrapped = wrap_tool(callback, name, module)
    assert _trusted_builtin_callback(wrapped, name, None)


def test_unknown_callback_with_empty_closure_is_untrusted():
    from transport.unity_instance_middleware import _trusted_builtin_callback

    captured = object()

    def unknown():
        return captured

    del captured
    assert not _trusted_builtin_callback(unknown, "manage_scene", None)


@pytest.mark.parametrize(
    "raw,expected,source", [(None, False, "default"), ("1", True, "environment")]
)
def test_running_configuration_reports_policy_and_origin(monkeypatch, raw, expected, source):
    # Given a fresh server process, capture actual startup provenance rather than test-mutated globals.
    name = "UNITY_MCP_REQUIRE_EXPLICIT_TARGET"
    monkeypatch.delenv(name, raising=False)
    if raw is not None:
        monkeypatch.setenv(name, raw)
    root = Path(__file__).resolve().parents[1]
    # When the server reports effective configuration, then include the policy/default/origin.
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            "from core.config import effective_server_configuration as report; print(report().model_dump_json())",
        ],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root / "src")},
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    entry = json.loads(result.stdout)["fields"]["require_explicit_target"]
    assert entry["value"] is expected and entry["default"] is False
    assert entry["source"] == source
    assert entry["source_detail"] == (name if raw is not None else "built_in")
