"""Recovery guidance must not turn a stale generation into a mutation replay."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.registry import operation_invocation as mod
from services.registry.operation_scheduler import OperationScope, OperationScopeChanged
from services.registry.operation_target import OperationTarget
from transport.command_budget import _CommandBudget


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "action", "apply", "evidence"),
    [
        (
            "manage_scene_edits",
            "preview",
            "apply",
            {
                "edits": [
                    {
                        "target_id": 25,
                        "target_path": "Root/Collider",
                        "component_type": "UnityEngine.BoxCollider",
                        "component_index": 0,
                        "property": "m_IsTrigger",
                        "before": True,
                        "after": False,
                    }
                ]
            },
        ),
        (
            "manage_scene_edits",
            "preview_structure",
            "apply_structure",
            {
                "scope_id": 25,
                "operations": [
                    {
                        "op": "create",
                        "name": "Child",
                        "temp_id": "$child",
                        "parent": 25,
                        "index": 0,
                        "affected_ids": ["$child"],
                    }
                ],
            },
        ),
        (
            "prefab_contents",
            "preview_modify_contents",
            "apply_preview",
            {
                "prefab_path": "Assets/Panel.prefab",
                "atomicity": "compare_before_save",
                "impact": [
                    {
                        "index": 0,
                        "target": "root",
                        "property": "setActive",
                        "before": True,
                        "after": False,
                    }
                ],
            },
        ),
        (
            "manage_prefabs",
            "preview_modify_contents",
            "apply_preview",
            {
                "prefab_path": "Assets/Panel.prefab",
                "atomicity": "compare_before_save",
                "impact": [
                    {
                        "index": 0,
                        "target": "root",
                        "property": "setActive",
                        "before": True,
                        "after": False,
                    }
                ],
            },
        ),
    ],
)
async def test_preview_continuation_uses_native_plan_once_without_repeating_payload(
    monkeypatch, name, action, apply, evidence
):
    from services.registry import preview_continuation as preview
    from services.tools.manage_prefabs import _prefab_plan_apply
    from services.tools.manage_scene_edits import _scene_plan_apply
    from transport.unity_instance_middleware import EXPLICIT_REQUEST_INSTANCE_STATE

    instance = "Fixture@12345678"
    plan = "b" * 32
    contract = _scene_plan_apply if name == "manage_scene_edits" else _prefab_plan_apply
    target = OperationTarget(OperationScope("local", "12345678", "generation"), lambda: True)
    schema = SimpleNamespace(
        parameters={
            "properties": dict.fromkeys(["action", "unity_instance", "preview_id", "undo_name"], {})
        }
    )
    ctx = SimpleNamespace(
        fastmcp=SimpleNamespace(get_tool=AsyncMock(return_value=schema)),
        get_state=AsyncMock(
            side_effect=lambda key: instance if key == EXPLICIT_REQUEST_INSTANCE_STATE else None
        ),
    )
    params = {
        "action": action,
        "prefab_path": "assets\\Panel.prefab" if name == "manage_prefabs" else "Panel.prefab",
        "target": 25,
        "undo_name": "Restore Panel",
        "set_active": False,
        "operations": [{"op": "create", "name": "Child", "temp_id": "$child", "parent": 25}],
        "edits": [
            {
                "target": 25,
                "component_type": "BoxCollider",
                "property": "m_IsTrigger",
                "value": False,
            }
        ],
    }
    value = {
        "success": True,
        "message": "Preview ready.",
        "data": {"preview_id": plan, "expires_in_seconds": 600.0, **evidence},
    }
    allowed = AsyncMock(return_value=True)
    monkeypatch.setattr(preview, "remote_operation_allowed", allowed)
    monkeypatch.setattr(preview.config, "require_explicit_target", True)
    binding = preview.PreviewBinding.capture(schema)
    result = await preview.with_preview_continuation(
        value, name, contract, params, schema, ctx, instance, target, binding=binding
    )
    args = {"action": apply, "preview_id": plan}
    if name == "manage_scene_edits":
        args["undo_name"] = params["undo_name"]
    allowed.assert_awaited_once_with(name, args, ctx=ctx)
    follow = result["data"]["follow_up"]
    assert result["message"].startswith(value["message"])
    assert "data.follow_up" in result["message"]
    assert follow["next_call"] == {"tool": name, "arguments": {**args, "unity_instance": instance}}
    assert follow["execution"] == {
        "via": "tools/call",
        "executor": "unity_editor",
        "request_unity_instance": instance,
        "client_filesystem_access": "not_required",
        "host_approval": "not_observed",
    }
    assert follow["verify"]["check"]
    for rejected in (
        {"success": False, "data": value["data"]},
        {"success": True, "data": {**value["data"], "preview_id": "not issued"}},
        {"success": True, "data": {**value["data"], "expires_in_seconds": 0}},
    ):
        assert (
            await preview.with_preview_continuation(
                rejected, name, contract, params, schema, ctx, instance, target, binding=binding
            )
            is rejected
        )
    assert (
        await preview.with_preview_continuation(
            value, name, contract, params, schema, ctx, "Other@87654321", target, binding=binding
        )
        is value
    )
    assert (
        await preview.with_preview_continuation(
            value,
            name,
            contract,
            params,
            SimpleNamespace(parameters={}),
            ctx,
            instance,
            target,
            binding=binding,
        )
        is value
    )
    allowed.return_value = False
    assert (
        await preview.with_preview_continuation(
            value, name, contract, params, schema, ctx, instance, target, binding=binding
        )
        is value
    )


def test_unbound_prefab_preview_and_other_operations_cannot_propose_apply():
    from services.registry.tool_registry import get_registered_tools
    from services.tools import manage_prefabs, manage_scene_edits

    for callback, action in (
        (manage_prefabs.manage_prefabs, "modify_contents"),
        (manage_scene_edits.manage_scene_edits, "undo"),
    ):
        entry = next(tool for tool in get_registered_tools() if tool["func"] is callback)
        assert action not in entry["preview_contracts"]


@pytest.mark.asyncio
async def test_unseen_family_publishes_its_contract_through_a_new_alias(monkeypatch):
    from typing import Literal
    from fastmcp import Context
    from services.registry import tool_registry
    from services.registry.capability_adapter import register_capability
    from services.registry.capability_catalog import MUTATING_EFFECTS
    from services.registry.preview_continuation import (
        PreviewApply,
        PreviewBinding,
        PreviewRegistration,
    )
    from transport.unity_instance_middleware import EXPLICIT_REQUEST_INSTANCE_STATE

    monkeypatch.setattr(tool_registry, "_tool_registry", [])

    def publish(params, data):
        return PreviewApply(
            {"action": "publish", "revision": data["observed"]},
            {"check": "Check the published revision."},
        )

    @tool_registry.mcp_for_unity_tool(preview_contracts={"plan": publish})
    async def future_family(
        ctx: Context, action: Literal["plan", "publish"], revision: str | None = None
    ):
        if replace_on_preview:
            ctx.fastmcp.get_tool.return_value = replacement
        return {"success": True, "data": {"observed": "revision-5"}}

    feature = SimpleNamespace(
        name="future_alias",
        source="future_family",
        actions=("plan", "publish"),
        parameters=("revision",),
        description="Fixture.",
        title="Fixture",
        group="core",
        maturity="stable",
        effects=MUTATING_EFFECTS,
    )
    alias = register_capability(feature, future_family)
    entry = next(tool for tool in tool_registry.get_registered_tools() if tool["func"] is alias)
    assert entry["preview_contracts"] == {"plan": publish}
    # The test-only callback stands in for a future locally trusted shipped tool.
    monkeypatch.setattr(
        mod, "trusted_tool", lambda function, name: function is alias and name == feature.name
    )
    instance = "Sample@12345678"
    target = OperationTarget(OperationScope("local", "12345678", "generation"), lambda: True)
    monkeypatch.setattr(mod, "capture_target", AsyncMock(return_value=target))
    schema = {
        "properties": {
            "action": {"enum": ["plan", "publish"]},
            "revision": {"type": "string"},
            "unity_instance": {"type": "string"},
        }
    }
    definition = SimpleNamespace(
        parameters=schema, fn=alias, key=feature.name, auth=None, version="1"
    )
    replace_on_preview = False
    ctx = SimpleNamespace(
        get_state=AsyncMock(
            side_effect=lambda key: (
                instance if key in {"unity_instance", EXPLICIT_REQUEST_INSTANCE_STATE} else None
            )
        ),
        fastmcp=SimpleNamespace(get_tool=AsyncMock(return_value=definition)),
    )
    invoke = mod.wrap_tool(
        alias,
        feature.name,
        feature.source,
        preview_registration=PreviewRegistration(
            entry["preview_contracts"], PreviewBinding.capture(definition)
        ),
    )
    result = await invoke(ctx=ctx, action="plan")
    assert "execution" not in result["data"]["follow_up"]
    assert result["data"]["follow_up"]["next_call"] == {
        "tool": feature.name,
        "arguments": {"action": "publish", "revision": "revision-5", "unity_instance": instance},
    }
    schema["properties"]["action"]["enum"] = ["plan"]
    assert "follow_up" not in (await invoke(ctx=ctx, action="plan"))["data"]
    schema["properties"]["action"]["enum"].append("publish")
    replacement = SimpleNamespace(parameters=schema, fn=lambda: None, key=feature.name, auth=None)
    # The SDK already selected this invocation; the first awaited catalog lookup
    # may already see a replacement while this wrapper still executes.
    ctx.fastmcp.get_tool.return_value = replacement
    assert "follow_up" not in (await invoke(ctx=ctx, action="plan"))["data"]
    ctx.fastmcp.get_tool.return_value = definition
    ctx.request_context = SimpleNamespace(meta={"fastmcp": {"version": "2"}})
    assert "follow_up" not in (await invoke(ctx=ctx, action="plan"))["data"]
    ctx.request_context.meta["fastmcp"]["version"] = "1"
    assert "follow_up" in (await invoke(ctx=ctx, action="plan"))["data"]
    entered, resume = asyncio.Event(), asyncio.Event()

    async def paused_authorization(*args, **kwargs):
        entered.set()
        await resume.wait()

    with monkeypatch.context() as scoped:
        scoped.setattr(mod, "authorize_remote_operation", paused_authorization)
        inflight = asyncio.create_task(invoke(ctx=ctx, action="plan"))
        await entered.wait()
        ctx.fastmcp.get_tool.return_value = replacement
        resume.set()
        assert "follow_up" not in (await inflight)["data"]
    replace_on_preview = True
    for replacement in (
        None,
        SimpleNamespace(parameters=schema, fn=lambda: None, key=feature.name, auth=None),
    ):
        ctx.fastmcp.get_tool.return_value = definition
        assert "follow_up" not in (await invoke(ctx=ctx, action="plan"))["data"]


@pytest.mark.asyncio
async def test_source_write_template_preserves_large_values_and_rejects_mismatched_evidence(
    monkeypatch,
):
    from services.registry.preview_continuation import PreviewBinding, with_preview_continuation
    from services.tools.manage_ui import _source_patch_apply
    from transport.unity_instance_middleware import EXPLICIT_REQUEST_INSTANCE_STATE

    instance = "Sample@12345678"
    changes = {"text": "a" * 4096, "tooltip": "마"}
    params = {
        "action": "patch_source",
        "path": "Assets/Panel.uxml",
        "source_changes": changes,
        "source_selector": "#caption",
    }
    data = {
        "preview": True,
        "path": params["path"],
        "persistence": "source",
        "reimport_requested": False,
        "source_hash": "a" * 64,
        "result_hash": "b" * 64,
        "changes": [
            {"property": key, "before": "", "after": value} for key, value in changes.items()
        ],
    }
    target = OperationTarget(OperationScope("local", "12345678", "generation"), lambda: True)
    ctx = SimpleNamespace(
        get_state=AsyncMock(
            side_effect=lambda key: instance if key == EXPLICIT_REQUEST_INSTANCE_STATE else None
        )
    )
    fields = [
        "action",
        "unity_instance",
        "preview",
        "path",
        "source_selector",
        "source_changes",
        "expected_source_hash",
    ]
    schema = SimpleNamespace(parameters={"properties": dict.fromkeys(fields, {})})
    ctx.fastmcp = SimpleNamespace(get_tool=AsyncMock(return_value=schema))
    outcome = await with_preview_continuation(
        {"success": True, "data": data},
        "ui_source",
        _source_patch_apply,
        params,
        schema,
        ctx,
        instance,
        target,
        binding=PreviewBinding.capture(schema),
    )
    follow = outcome["data"]["follow_up"]
    assert follow["execution"]["executor"] == "unity_editor"
    assert follow["execution"]["request_unity_instance"] == instance
    assert "next_call" not in follow
    assert follow["copy_from_preview_arguments"] == ["source_changes"]
    assert "source_changes" not in follow["call_template"]["arguments"]
    assert follow["call_template"]["arguments"]["expected_source_hash"] == data["source_hash"]
    for changed in (
        {"path": "Assets/Other.uxml"},
        {"source_hash": ""},
        {"result_hash": data["source_hash"]},
        {"changes": [{}]},
        {"preview": False},
    ):
        assert _source_patch_apply(params, {**data, **changed}) is None
    assert _source_patch_apply({**params, "expected_source_hash": "c" * 64}, data) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("inside_call", [False, True])
async def test_generation_refusal_explains_when_the_tool_may_have_run(monkeypatch, inside_call):
    async def authorize(*args, **kwargs):
        pass

    async def target(*args, **kwargs):
        return OperationTarget(OperationScope("local", "owned", "old"), lambda: inside_call, True)

    async def callback(ctx):
        raise OperationScopeChanged()

    monkeypatch.setattr(mod, "authorize_remote_operation", authorize)
    monkeypatch.setattr(mod, "capture_target", target)
    monkeypatch.setattr(mod, "trusted_tool", lambda *_: True)
    monkeypatch.setattr("services.tools.get_unity_instance_from_context", authorize)
    result = await mod.wrap_tool(callback, "prefab_contents", "manage_prefabs")(SimpleNamespace())
    assert result["error"] == "operation_target_changed"
    assert result["data"]["dispatch"] == ("unknown" if inside_call else "not_dispatched")
    assert result["hint"] == ("inspect_state_before_retry" if inside_call else "select_instance")
    assert "mcpforunity://instances" in result["message"]
    assert result["data"]["automatic_retry"] is False


def test_reported_ui_bytes_match_the_requested_text_and_native_xml_escaping():
    from services.tools.manage_ui import _source_value_matches

    text = """&<>"'\t\n\r"""
    for encoded in ("&amp;&lt;&gt;&quot;'&#9;&#10;&#13;", '&amp;&lt;&gt;"&apos;&#9;&#10;&#13;'):
        assert _source_value_matches("Assets/Panel.uxml", text, {"before": "old", "after": encoded})
    for incorrect in (
        text,
        False,
        "&amp;lt;",
        "different",
        "&amp;&lt;&gt;&quot;&apos;&#9;&#10;&#13;",
    ):
        assert not _source_value_matches(
            "Assets/Panel.uxml", text, {"before": "old", "after": incorrect}
        )
    assert _source_value_matches("Assets/Panel.uss", "&<", {"before": "old", "after": "&<"})
    assert not _source_value_matches(
        "Assets/Panel.uss", "&<", {"before": "old", "after": "&amp;&lt;"}
    )


def test_scene_plan_integrity_checks_normalized_identity_values_and_order():
    from services.tools.manage_scene_edits import _same_scene_fields, _same_structure_plan

    requested = [
        {
            "target": 10,
            "component_type": "Transform",
            "property": "localPosition",
            "value": [0.1, 2, 3],
        },
        {
            "target": "Panel",
            "search_method": "by_name",
            "component_type": "BoxCollider",
            "property": "m_IsTrigger",
            "expected_old": True,
            "value": False,
        },
    ]
    rows = [
        {
            "target_id": 42,
            "target_path": "Root/Panel",
            "component_type": "UnityEngine.BoxCollider",
            "component_index": 0,
            "property": "m_IsTrigger",
            "before": True,
            "after": False,
        },
        {
            "target_id": 10,
            "target_path": "Root",
            "component_type": "UnityEngine.Transform",
            "component_index": 0,
            "property": "m_LocalPosition",
            "before": [0, 0, 0],
            "after": [0.10000000149011612, 2, 3],
        },
    ]
    assert _same_scene_fields(requested, rows)
    leaf = {**rows[0], "target_path": "Root/Panel/Label"}

    def by_path(path):
        return {**requested[1], "search_method": "by_path", "target": path}

    for path in ("Panel/Label", "Label", "Root/Panel/Label", "/Root/Panel/Label"):
        assert _same_scene_fields([by_path(path)], [leaf])
    for path in ("/Panel/Label", "anel/Label", "Different/Label", "Panel/label"):
        assert not _same_scene_fields([by_path(path)], [leaf])
    duplicate_suffix = {**rows[1], "target_path": "Other/Panel/Label"}
    # Target resolution happens before component filtering; a different
    # component on the second candidate still makes the selector ambiguous.
    assert not _same_scene_fields([by_path("Panel/Label"), requested[0]], [leaf, duplicate_suffix])
    assert _same_scene_fields(
        [by_path("/Root/Panel/Label"), requested[0]], [leaf, duplicate_suffix]
    )
    for change in (
        {"after": 0},
        {"after": True},
        {"before": False},
        {"property": "m_Enabled"},
        {"target_path": "Root/Other"},
        {"component_index": 1},
        {"component_type": "UnityEngine.SphereCollider"},
    ):
        assert not _same_scene_fields(requested, [{**rows[0], **change}, rows[1]])
    assert not _same_scene_fields(requested, [rows[0], rows[0]])
    create = {"op": "create", "name": "Child", "temp_id": "$test", "parent": 25}
    reparent = {"op": "reparent", "target": 10, "parent": "$test"}
    plan = {"target": 25, "operations": [create, reparent]}
    native = [
        {**create, "index": 0, "affected_ids": ["$test"]},
        {**reparent, "index": 1, "affected_ids": ["10", "11"]},
    ]
    data = {"scope_id": 25, "operations": native, "impact": native}
    assert _same_structure_plan(plan, data)
    for invalid in (
        [None, reparent],
        [{"op": "delete", "target": 25}, reparent],
        list(reversed(native)),
        [{**native[0], "affected_ids": ["10"]}, native[1]],
        [{**create, "parent": True}, reparent],
    ):
        assert not _same_structure_plan(plan, {"scope_id": 25, "operations": invalid})
    assert not _same_structure_plan(plan, {**data, "impact": []})


def test_prefab_plan_rejects_wrong_targets_values_fields_and_duplicate_impact():
    from services.tools.manage_prefabs import _prefab_plan_apply

    expected = {
        "prefab_path": "Assets/Box.prefab",
        "position": {"x": 0.1, "y": 2, "z": 3},
        "set_active": False,
    }
    move = {
        "index": 0,
        "target": "root",
        "property": "position",
        "before": [0, 0, 0],
        "after": [0.1, 2, 3],
    }
    active = {"index": 1, "target": "root", "property": "setActive", "before": True, "after": False}
    data = {
        "preview_id": "a" * 32,
        "expires_in_seconds": 600,
        "atomicity": "compare_before_save",
        "prefab_path": "Assets/Box.prefab",
        "impact": [move, active],
    }
    assert _prefab_plan_apply(expected, data)
    for rows in (
        [move, move],
        [move],
        [{**move, "after": [2, 1, 3]}, active],
        [move, {**active, "target": "child"}],
        [move, {**active, "after": 0}],
        [move, {**active, "property": "scale"}],
    ):
        assert _prefab_plan_apply(expected, {**data, "impact": rows}) is None


@pytest.mark.parametrize(
    ("component", "spelling", "canonical"),
    [
        ("BoxCollider", "isTrigger", "m_IsTrigger"),
        ("Rigidbody", "Use Gravity", "m_UseGravity"),
        ("SphereCollider", " eNABLed ", "m_Enabled"),
        ("CapsuleCollider", "M_ISTRIGGER", "m_IsTrigger"),
    ],
)
def test_scene_continuation_requires_one_verifiable_binding_per_requested_field(
    component, spelling, canonical
):
    from services.tools.manage_scene_edits import _scene_plan_apply

    edit = {"target": 42, "component_type": component, "property": spelling, "value": True}
    row = {
        "target_id": 42,
        "target_path": "Root/Item",
        "component_type": "UnityEngine." + component,
        "component_index": 0,
        "requested_property": spelling,
        "property": canonical,
        "before": False,
        "after": True,
    }
    params = {"action": "preview", "edits": [edit]}
    data = {"preview_id": "a" * 32, "expires_in_seconds": 600, "edits": [row]}
    assert _scene_plan_apply(params, data).arguments == {
        "action": "apply",
        "preview_id": data["preview_id"],
    }
    assert _scene_plan_apply(
        {"action": "preview", "edits": [{**edit, "property": canonical}]},
        {**data, "edits": [{**row, "requested_property": canonical}]},
    )
    dimension = {**row, "property": "m_Size", "before": [3, 4, 5], "after": [1, 1, 1]}
    for rows in (
        [dimension],
        [row, dimension],
        [{**row, "property": "m_OtherField"}],
        [{**row, "property": spelling}],
        [{**row, "requested_property": canonical}],
        [{key: value for key, value in row.items() if key != "requested_property"}],
    ):
        assert _scene_plan_apply(params, {**data, "edits": rows}) is None
    assert (
        _scene_plan_apply(
            {**params, "edits": [edit, {**edit, "property": canonical}]},
            {**data, "edits": [row, {**row, "requested_property": canonical}]},
        )
        is None
    )


def test_console_readiness_timeout_guides_wait_without_extending_the_clock():
    budget = _CommandBudget(123, phase="readiness", command="read_console")
    result = budget.timeout_response("read_console")
    assert result["data"]["dispatch"] == "not_dispatched"
    assert result["data"]["next_action"] == "wait_for_editor_ready"
    assert "mcpforunity://editor/state" in result["message"]
    assert "Play Mode" in result["message"]
    assert budget.expires_at == 123


def test_console_dispatched_timeout_never_promises_safe_replay():
    result = _CommandBudget(123, phase="response", dispatched=True).timeout_response("read_console")
    assert result["hint"] == "inspect_state_before_retry"
    assert result["data"]["reason"] == "outcome_unknown"
    assert "next_action" not in result["data"]


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["retry", "ready"])
@pytest.mark.parametrize(
    ("command_wait", "reason"), [(10, "editor_not_ready"), (0.5, "command_deadline_exceeded")]
)
async def test_console_probe_expiry_is_also_an_explicit_undispatched_wait(
    monkeypatch, outcome, command_wait, reason
):
    from transport import plugin_hub as hub

    loop = asyncio.get_running_loop()
    clock = loop.time
    offset = 0

    async def probe(*args):
        nonlocal offset
        offset = 2
        return outcome

    monkeypatch.setattr(hub.config, "http_remote_hosted", False)
    monkeypatch.setattr(loop, "time", lambda: clock() + offset)
    monkeypatch.setenv("UNITY_MCP_SESSION_READY_WAIT_SECONDS", "1")
    monkeypatch.setattr(
        hub.PluginHub,
        "_resolve_session",
        AsyncMock(return_value=SimpleNamespace(session_id="owned")),
    )
    monkeypatch.setattr(hub.PluginHub, "_ensure_live_connection", AsyncMock(return_value=True))
    monkeypatch.setattr(hub.PluginHub, "_read_identity", AsyncMock(return_value=None))
    monkeypatch.setattr(hub.PluginHub, "_readiness_probe", probe)
    dispatch = AsyncMock()
    monkeypatch.setattr(hub.PluginHub, "send_command", dispatch)
    budget = _CommandBudget(clock() + command_wait, command="read_console")
    result = await hub.PluginHub._send_command_for_instance(
        "Owned@hash", "read_console", {"action": "clear"}, None, True, budget=budget
    )
    assert result["data"]["reason"] == reason
    assert result["data"]["dispatch"] == "not_dispatched"
    assert result["data"]["next_action"] == "wait_for_editor_ready"
    dispatch.assert_not_awaited()
