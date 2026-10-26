"""Declared mutation lifecycles keep readiness and hard deadlines distinct."""

import ast
import asyncio
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.config import config
from services.execution_receipt_context import execution_owner
from services.registry import operation_invocation as invocation
from services.registry.operation_budget import READINESS_CALLBACKS, resolve_public_budget
from services.registry.operation_scheduler import OperationAdmission, OperationScope
from services.registry.operation_invocation import operation_session
from transport.command_budget import _CommandBudget, _HttpRequestBudget, http_request_budget


def callback(module, name=None):
    return getattr(importlib.import_module("services.tools." + module), name or module)


@pytest.mark.parametrize(
    "module,name,params,wait",
    [
        ("manage_script", "create_script", {}, True),
        ("manage_script", "delete_script", {}, True),
        ("manage_script", "apply_text_edits", {}, True),
        ("manage_script", "apply_text_edits", {"options": {"preview": True}}, False),
        ("manage_script", "apply_text_edits", {"options": {"debug_preview": True}}, False),
        ("manage_script", "apply_text_edits", {"options": {"preview": "false"}}, True),
        ("manage_script", "manage_script", {"action": "read"}, False),
        ("manage_script", "manage_script", {"action": "create"}, True),
        ("script_apply_edits", "script_apply_edits", {}, True),
        ("script_apply_edits", "script_apply_edits", {"options": {"preview": True}}, False),
        ("manage_audio", None, {"action": "set_routing"}, True),
        ("manage_audio", None, {"action": "apply_import_settings"}, True),
        ("manage_audio", None, {"action": "read_import_settings"}, False),
        ("manage_localization", None, {"action": "set_fallbacks"}, True),
        ("manage_localization", None, {"action": "inspect"}, False),
        ("manage_ugui", None, {"action": "create_control"}, True),
        ("manage_ugui", None, {"action": "set_text"}, True),
        ("manage_ugui", None, {"action": "diagnose_device_profiles"}, False),
        ("manage_ui", None, {"action": "create"}, True),
        ("manage_ui", None, {"action": "patch_source", "preview": False}, True),
        ("manage_ui", None, {"action": "patch_source", "preview": True}, False),
        ("manage_ui", None, {"action": "patch_source"}, False),
        ("manage_ui", None, {"action": "get_visual_tree"}, False),
    ],
)
def test_complete_mutation_routes_include_readiness_but_pure_routes_do_not(
    monkeypatch, module, name, params, wait
):
    monkeypatch.setenv("UNITY_MCP_EDITOR_READY_TIMEOUT", "1800")
    result = resolve_public_budget(
        callback(module, name), name or module, params, trusted_builtin=True
    )
    assert result.readiness_seconds == (1800 if wait else 0)


def test_all_actual_send_mutation_callers_have_explicit_budget_declarations():
    tools = Path(__file__).resolve().parents[1] / "src/services/tools"
    callers = set()
    for source in tools.glob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Name)
                and child.func.id == "send_mutation"
                for child in ast.walk(node)
            ):
                callers.add((source.stem, node.name))
    assert callers == READINESS_CALLBACKS


@pytest.mark.asyncio
@pytest.mark.parametrize("http", [False, True])
async def test_actual_public_script_wrapper_declares_long_readiness_before_queue(monkeypatch, http):
    from services.registry.operation_target import OperationTarget
    from services.tools import manage_script as scripts
    from transport.legacy.unity_connection import _command_deadline

    monkeypatch.setenv("UNITY_MCP_EDITOR_READY_TIMEOUT", "1800")
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(config, "command_total_timeout", 600.0)
    now = asyncio.get_running_loop().time()
    observed = []

    async def target(*args, **kwargs):
        return OperationTarget(OperationScope("local", "owned", "g"), lambda: True, True)

    async def mutation(*args, **kwargs):
        from services.registry.operation_context import active_operation

        observed.append(active_operation().request.expires_at)
        # The ordinary native timeout is retained; a long wrapper policy must
        # not inflate every status RPC to the whole readiness duration.
        assert _command_deadline("get_editor_state", {}) <= asyncio.get_running_loop().time() + 601
        return {"success": True}

    class Context:
        async def get_state(self, key, default=None):
            return "Owned@owned" if key == "unity_instance" else default

    monkeypatch.setattr(invocation, "capture_target", target)
    monkeypatch.setattr(scripts, "send_mutation", mutation)
    async with asyncio.timeout_at(now + 300) as scope:
        request = _HttpRequestBudget(now, _CommandBudget(now + 300), scope) if http else None
        token = http_request_budget.set(request)
        try:
            result = await invocation.wrap_tool(
                scripts.create_script, "create_script", "manage_script"
            )(Context(), path="Assets/Owned.cs", contents="class Owned {}")
        finally:
            http_request_budget.reset(token)
    assert result["success"]
    assert now + 1799 <= observed[0] <= now + 1801


@pytest.mark.asyncio
@pytest.mark.parametrize("ceiling", ["request", "receipt", "parent"])
async def test_existing_hard_ceilings_win_over_long_declared_readiness(monkeypatch, ceiling):
    monkeypatch.setenv("UNITY_MCP_EDITOR_READY_TIMEOUT", "1800")
    now = asyncio.get_running_loop().time()
    async with asyncio.timeout_at(now + 30) as scope:
        request = _HttpRequestBudget(
            now,
            _CommandBudget(now + 30),
            scope,
            hard_expires_at=now + 30 if ceiling == "request" else None,
        )
        token = http_request_budget.set(request)
        receipt_token = execution_owner.set(
            SimpleNamespace(expires_at=now + 30) if ceiling == "receipt" else None
        )
        admission = OperationAdmission(
            OperationScope("local", "owned", "g"), "exclusive", lambda: True, expires_at=now + 30
        )
        try:

            async def resolve():
                return invocation._deadline(
                    "create_script",
                    {},
                    function=callback("manage_script", "create_script"),
                    trusted_builtin=True,
                )

            if ceiling == "parent":
                async with operation_session(admission):
                    deadline = await resolve()
            else:
                deadline = await resolve()
            assert deadline == now + 30
            assert request.budget.expires_at == now + 30
        finally:
            execution_owner.reset(receipt_token)
            http_request_budget.reset(token)


@pytest.mark.asyncio
async def test_readiness_policy_uses_original_entry_after_elapsed_queue_time(monkeypatch):
    monkeypatch.setenv("UNITY_MCP_EDITOR_READY_TIMEOUT", "1800")
    now = asyncio.get_running_loop().time()
    started = now - 1000
    async with asyncio.timeout_at(now + 300) as scope:
        request = _HttpRequestBudget(started, _CommandBudget(now + 300), scope)
        token = http_request_budget.set(request)
        try:
            deadline = invocation._deadline(
                "create_script",
                {},
                function=callback("manage_script", "create_script"),
                trusted_builtin=True,
            )
            assert deadline == started + 1800
            assert deadline != now + 1800
        finally:
            http_request_budget.reset(token)


def test_functional_alias_uses_only_its_real_registered_source_budget(monkeypatch):
    from services.registry.capability_adapter import register_capability
    from services.registry.capability_catalog import CAPABILITIES

    monkeypatch.setenv("UNITY_MCP_EDITOR_READY_TIMEOUT", "1800")
    feature = next(item for item in CAPABILITIES if item.name == "ui_source")
    actual = callback("manage_ui")
    from services.registry import tool_registry

    monkeypatch.setattr(tool_registry, "_tool_registry", list(tool_registry._tool_registry))
    alias = register_capability(feature, actual)
    assert (
        resolve_public_budget(
            alias, "ui_source", {"action": "patch_source", "preview": False}, trusted_builtin=True
        ).readiness_seconds
        == 1800
    )
    assert (
        resolve_public_budget(
            alias, "ui_source", {"action": "patch_source", "preview": True}, trusted_builtin=True
        ).readiness_seconds
        == 0
    )
    assert (
        resolve_public_budget(
            alias, "ui_source", {"action": "unknown"}, trusted_builtin=True
        ).readiness_seconds
        == 0
    )
    assert (
        resolve_public_budget(
            actual, "manage_ui", {"action": "create"}, trusted_builtin=False
        ).readiness_seconds
        == 0
    )
    import inspect

    async def replacement(**kwargs):
        raise AssertionError("Budget resolution must not execute a captured callback")

    replacement.__signature__ = inspect.signature(actual)
    custom_alias = register_capability(feature, replacement)
    assert (
        resolve_public_budget(
            custom_alias, "ui_source", {"action": "create"}, trusted_builtin=True
        ).readiness_seconds
        == 0
    )


@pytest.mark.asyncio
async def test_explicit_whole_operation_budgets_are_not_reinterpreted_as_readiness_floors(
    monkeypatch,
):
    monkeypatch.setenv("UNITY_MCP_EDITOR_READY_TIMEOUT", "1800")
    monkeypatch.setattr(config, "command_total_timeout", 60.0)
    refresh = resolve_public_budget(
        callback("refresh_unity"), "refresh_unity", {"timeout_seconds": 5}, trusted_builtin=True
    )
    ordered = resolve_public_budget(
        callback("script_apply_edits"),
        "script_apply_edits",
        {"options": {"mixed_order": "request"}},
        trusted_builtin=True,
    )
    assert refresh.hard_seconds == 5
    assert ordered.hard_seconds == 60
    now = asyncio.get_running_loop().time()
    deadline = invocation._deadline(
        "refresh_unity",
        {"timeout_seconds": 5},
        function=callback("refresh_unity"),
        trusted_builtin=True,
    )
    assert now + 4.9 <= deadline <= now + 5.1


def test_declared_positive_test_job_wait_is_not_shortened_to_one_status_rpc():
    actual = callback("run_tests", "get_test_job")
    assert (
        resolve_public_budget(
            actual, "get_test_job", {"wait_timeout": 1800}, trusted_builtin=True
        ).readiness_seconds
        == 1800
    )
    assert (
        resolve_public_budget(
            actual, "get_test_job", {"wait_timeout": None}, trusted_builtin=True
        ).readiness_seconds
        == 0
    )
