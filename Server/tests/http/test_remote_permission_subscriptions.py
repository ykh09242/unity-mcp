"""Grant retirement composes with the existing real SDK/ASGI subscription fixture."""

import importlib

import anyio
import httpx
import pytest

from services.api_key_service import ApiKeyService, ValidationResult
from services.credential_lease import CredentialLeaseError
from services.operation_permissions import OperationPermissionError
from tests.http.prepared_tool_registration import prepared_tool_factory  # pylint: disable=unused-import
from tests.http.test_mcp_catalog_subscription_limits import (
    ListenStream,
    PluginStream,
    change_count,
    hosted_catalog,  # pylint: disable=unused-import
    register_scopes,
    wait,
)
from tests.http.test_mcp_protocol_wire import _body, _headers, _message
from transport import unity_instance_middleware


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,arguments",
    [
        ("manage_editor", {"action": "play"}),
        ("execute_menu_item", {"action": "execute", "menu_path": "Window/General/Console"}),
    ],
)
async def test_actual_public_tool_admission_denies_before_selection(
    hosted_catalog, monkeypatch, name, arguments
):
    app, _runtime = hosted_catalog
    service = ApiKeyService.get_instance()
    selected = []
    denied = []
    original_admission = unity_instance_middleware.authorize_remote_operation

    async def validate(_key):
        return ValidationResult(
            valid=True, user_id="alice", metadata={"unity_mcp_permissions": ["read"]}
        )

    async def forbidden(*_args, **_kwargs):
        selected.append(1)
        raise AssertionError("Forbidden selection/settings/native work was reached")

    async def observed_admission(operation, params, **kwargs):
        try:
            await original_admission(operation, params, **kwargs)
        except OperationPermissionError:
            denied.append(operation)
            raise

    monkeypatch.setattr(service, "_validate_external", validate)
    monkeypatch.setattr(unity_instance_middleware, "authorize_remote_operation", observed_admission)
    monkeypatch.setattr(
        unity_instance_middleware.UnityInstanceMiddleware, "_call_selected_tool", forbidden
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            headers={"Accept": "application/json, text/event-stream"},
        ) as http:
            # Given real authentication, registered schemas and actual MCP wire routing.
            # When a read credential attempts a run or executing menu alias, then admission
            # rejects it before even selecting the Unity route or entering the handler.
            response = await http.post(
                "/mcp",
                headers={**_headers("tools/call", name=name), "x-api-key": "alice-key"},
                json=_body("tools/call", {"name": name, "arguments": arguments}),
            )
            message = _message(response)
            assert "error" in message or message.get("result", {}).get("isError") is True
            assert denied == [name] and selected == []


@pytest.mark.asyncio
async def test_selected_plugin_cannot_start_public_refresh_before_its_promised_read(
    hosted_catalog, monkeypatch
):
    refresh_unity = importlib.import_module("services.tools.refresh_unity")

    app, _runtime = hosted_catalog
    service = ApiKeyService.get_instance()
    calls, denied, admissions = [], [], []
    original_admission = unity_instance_middleware.authorize_remote_operation
    claims = {"alice-key": ["read", "test", "edit", "run"], "plugin-key": ["edit", "run"]}

    async def validate(key):
        return ValidationResult(
            valid=True, user_id="alice", metadata={"unity_mcp_permissions": claims[key]}
        )

    async def forbidden(*_args, **_kwargs):
        calls.append(1)
        raise AssertionError("Public refresh reached transport before combined admission")

    async def observed_admission(operation, params, **kwargs):
        ctx = kwargs.get("ctx")
        session_id = await ctx.get_state("unity_session_id") if ctx is not None else None
        admissions.append((operation, ctx is not None, session_id, params.get("wait_for_ready")))
        try:
            await original_admission(operation, params, **kwargs)
        except OperationPermissionError:
            denied.append((operation, kwargs.get("ctx") is not None, params.get("wait_for_ready")))
            raise

    monkeypatch.setattr(service, "_validate_external", validate)
    monkeypatch.setattr(unity_instance_middleware, "authorize_remote_operation", observed_admission)
    monkeypatch.setattr(refresh_unity.unity_transport, "send_with_unity_instance", forbidden)
    async with app.router.lifespan_context(app):
        async with anyio.create_task_group() as group:
            plugin = PluginStream(app, "plugin-key", "hash", "Fixture")
            try:
                await plugin.connect(group)
                await plugin.catalog([{"name": "refresh_unity"}])
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://testserver",
                    headers={"Accept": "application/json, text/event-stream"},
                ) as http:
                    # Given real authenticated plugin registration/catalog selection and a
                    # requester that passes the early public check, the plugin lacks read.
                    body = dict(
                        _body(
                            "tools/call",
                            {
                                "name": "refresh_unity",
                                "arguments": {
                                    "wait_for_ready": True,
                                    "unity_instance": "Fixture@hash",
                                },
                            },
                        )
                    )
                    response = await http.post(
                        "/mcp",
                        headers={
                            **_headers("tools/call", name="refresh_unity"),
                            "x-api-key": "alice-key",
                        },
                        json=body,
                    )
                    message = _message(response)
                    # Then the selected plugin's complete public-operation denial occurs
                    # before refresh dispatch, not after an edit/run native send.
                    assert "error" in message or message.get("result", {}).get("isError") is True
                    assert admissions == [
                        ("refresh_unity", False, None, True),
                        ("refresh_unity", True, plugin.session_id, True),
                    ]
                    assert denied == [("refresh_unity", True, True)] and calls == []
            finally:
                await plugin.close()


@pytest.mark.asyncio
async def test_subscription_downgrade_closes_one_key_but_same_user_other_key_stays_healthy(
    hosted_catalog, monkeypatch
):
    app, runtime = hosted_catalog
    service = ApiKeyService.get_instance()
    now = [0.0]
    claims = {"alice-key": ["read"], "other-key": ["read"]}
    monkeypatch.setattr(service, "_now", lambda: now[0])
    monkeypatch.setattr(service, "_max_auth_age", 10)

    async def validate(key):
        return ValidationResult(
            valid=True, user_id="alice", metadata={"unity_mcp_permissions": claims[key]}
        )

    monkeypatch.setattr(service, "_validate_external", validate)
    async with app.router.lifespan_context(app):
        await register_scopes()
        first = ListenStream(app, "restricted", notifications={"toolsListChanged": True})
        other = ListenStream(
            app, "healthy", principal="other-key", notifications={"toolsListChanged": True}
        )
        async with anyio.create_task_group() as group:
            group.start_soon(first.run)
            group.start_soon(other.run)
            try:
                # Given two actual acknowledged SDK streams owned by different same-user credentials.
                await wait(first.acknowledged)
                await wait(other.acknowledged)
                first_lease = (await service.validate("alice-key")).lease
                assert runtime.admission.active_count == 2
                claims["alice-key"] = []
                now[0] = 10
                # When a fresh verdict removes read, then the old stream is retired, not relabelled.
                with pytest.raises(CredentialLeaseError):
                    await first_lease.check()
                await wait(first.done)
                assert runtime.admission.active_count == 1
                assert not other.done.is_set()
                runtime.publish(
                    user_id="alice", project_hash="a", session_id="alice-a", catalog_revision=2
                )
                await wait(other.changed)
                assert change_count(other) == 1 and change_count(first) == 0
                # A new same-key request with explicit empty authority cannot reacquire admission.
                denied = ListenStream(app, "denied", notifications={"toolsListChanged": True})
                group.start_soon(denied.run)
                await wait(denied.done)
                assert not denied.acknowledged.is_set()
                assert runtime.admission.active_count == 1
            finally:
                first.disconnect.set()
                other.disconnect.set()
                first.resume.set()
                other.resume.set()
                await wait(first.done)
                await wait(other.done)
        assert runtime.admission.active_count == 0 and not runtime._leases


@pytest.mark.asyncio
async def test_selected_plugin_cannot_start_opted_in_raycast_before_all_grants(
    hosted_catalog, monkeypatch
):
    ugui = importlib.import_module("services.tools.manage_ugui")
    app, _runtime = hosted_catalog
    service = ApiKeyService.get_instance()
    calls, admissions, denied = [], [], []
    original_admission = unity_instance_middleware.authorize_remote_operation
    claims = {"alice-key": ["read", "test", "edit", "run"], "plugin-key": ["read"]}

    async def validate(key):
        return ValidationResult(
            valid=True, user_id="alice", metadata={"unity_mcp_permissions": claims[key]}
        )

    async def forbidden_preflight(*_args, **_kwargs):
        calls.append("preflight")
        raise AssertionError("Opted-in raycast reached preflight before combined admission")

    async def forbidden_transport(*_args, **_kwargs):
        calls.append("transport")
        raise AssertionError("Opted-in raycast reached transport before combined admission")

    async def observed_admission(operation, params, **kwargs):
        ctx = kwargs.get("ctx")
        session_id = await ctx.get_state("unity_session_id") if ctx is not None else None
        admissions.append(
            (operation, ctx is not None, session_id, params.get("allow_custom_filters"))
        )
        try:
            await original_admission(operation, params, **kwargs)
        except OperationPermissionError:
            denied.append((operation, ctx is not None))
            raise

    monkeypatch.setattr(service, "_validate_external", validate)
    monkeypatch.setattr(unity_instance_middleware, "authorize_remote_operation", observed_admission)
    monkeypatch.setattr(ugui, "preflight", forbidden_preflight)
    monkeypatch.setattr(ugui, "send_with_unity_instance", forbidden_transport)
    async with app.router.lifespan_context(app):
        async with anyio.create_task_group() as group:
            plugin = PluginStream(app, "plugin-key", "hash", "Fixture")
            try:
                await plugin.connect(group)
                await plugin.catalog([{"name": "manage_ugui"}])
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://testserver",
                    headers={"Accept": "application/json, text/event-stream"},
                ) as http:
                    response = await http.post(
                        "/mcp",
                        headers={
                            **_headers("tools/call", name="manage_ugui"),
                            "x-api-key": "alice-key",
                        },
                        json=_body(
                            "tools/call",
                            {
                                "name": "manage_ugui",
                                "arguments": {
                                    "action": "diagnose_raycast",
                                    "target": 7,
                                    "screen_position": [12.0, 34.0],
                                    "allow_custom_filters": True,
                                    "unity_instance": "Fixture@hash",
                                },
                            },
                        ),
                    )
                    message = _message(response)
                    assert "error" in message or message.get("result", {}).get("isError") is True
                    assert admissions == [
                        ("manage_ugui", False, None, True),
                        ("manage_ugui", True, plugin.session_id, True),
                    ]
                    assert denied == [("manage_ugui", True)] and calls == []
            finally:
                await plugin.close()
