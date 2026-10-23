"""Optional missing-target MRTR uses authenticated HTTP and the ordinary tool once."""

from tests.timing import watchdog_timeout

from contextlib import asynccontextmanager
import copy
import json
import secrets
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import anyio
import httpx
import pytest

from core.config import config
from core.local_auth import LOCAL_AUTH_HEADER
from services.api_key_service import ApiKeyService, ValidationResult
from tests.http.test_mcp_catalog_subscription_limits import PluginStream
from tests.http.test_mcp_protocol_wire import _body, _headers
from transport.plugin_hub import PluginHub


class ScenePeer(PluginStream):
    """Existing owned WebSocket lifecycle plus observable, bounded command replies."""

    def __init__(self, app, principal, project_hash, name, token=None):
        self.actual_app = app
        self.token = token
        self.commands = []
        self.loads = []
        self.dispatched = anyio.Event()
        self.release = anyio.Event()
        self.reply_done = anyio.Event()
        self.reply_done.set()
        self.hold_load = False
        self.load_result = {
            "success": True,
            "message": "fixture loaded",
            "data": {"path": "Assets/Scenes/A.unity"},
        }
        self.command_send, self.command_receive = anyio.create_memory_object_stream(16)
        super().__init__(self.observed_app, principal, project_hash, name)

    async def observed_app(self, scope, receive, send):
        if self.token:
            scope = {
                **scope,
                "headers": [
                    *scope["headers"],
                    (LOCAL_AUTH_HEADER.lower().encode(), self.token.encode()),
                ],
            }

        async def observed(message):
            if message["type"] == "websocket.send":
                payload = json.loads(message["text"])
                if payload["type"] == "execute":
                    self.commands.append(payload)
                    await self.command_send.send(payload)
            await send(message)

        await self.actual_app(scope, receive, observed)

    async def respond(self):
        async with self.command_receive:
            async for command in self.command_receive:
                self.reply_done = anyio.Event()
                if command["name"] in {"manage_scene", "scene_lifecycle", "scene_inspect"}:
                    self.loads.append(command)
                    self.dispatched.set()
                    if self.hold_load:
                        await self.release.wait()
                    result = self.load_result
                elif command["name"] == "get_editor_state":
                    result = {
                        "success": True,
                        "data": {
                            "schema_version": "1",
                            "observed_at_unix_ms": int(time.time() * 1000),
                            "sequence": 1,
                            "compilation": {"is_compiling": False},
                            "assets": {"external_changes_dirty": False},
                        },
                    }
                elif command["name"] == "ping":
                    result = {"status": "success", "result": {"message": "pong"}}
                else:
                    raise AssertionError(f"Unexpected native command {command['name']}")
                await self.send_json(
                    {"type": "command_result", "id": command["id"], "result": result}
                )
                self.reply_done.set()

    async def finish(self):
        self.release.set()
        try:
            with anyio.fail_after(watchdog_timeout()):
                await self.reply_done.wait()
            await self.close()
        finally:
            await self.command_send.aclose()
            await self.command_receive.aclose()


@pytest.fixture
def flow_server(monkeypatch, prepared_tool_factory, request):
    from fastmcp.tools.base import Tool
    from main import create_mcp_server
    import services.tools.preflight as preflight_module

    settings = getattr(request, "param", True)
    hosted, enabled = settings[:2] if isinstance(settings, tuple) else (settings, True)
    grants = settings[2] if isinstance(settings, tuple) and len(settings) == 3 else None
    monkeypatch.setattr(config, "http_remote_hosted", hosted)
    monkeypatch.setattr(config, "http_behind_tls_proxy", True)
    monkeypatch.setattr(config, "transport_mode", "http")
    monkeypatch.setattr(config, "local_auth_token", secrets.token_urlsafe(32))
    monkeypatch.setenv("UNITY_MCP_SCENE_LOAD_INPUTS", "1" if enabled else "0")
    monkeypatch.setenv("DISABLE_TELEMETRY", "1")
    monkeypatch.setenv("UNITY_MCP_SKIP_STARTUP_CONNECT", "1")
    monkeypatch.setattr(ApiKeyService, "_instance", None)
    monkeypatch.setattr(config, "api_key_validation_url", None)
    # Exercise the real preflight instead of its repository-wide pytest shortcut.
    monkeypatch.setattr(preflight_module, "_in_pytest", lambda: False)

    def create():
        # Reopened servers need a fresh service; a prior lifespan closes its captured service.
        service = ApiKeyService("https://fixture.invalid/validate")

        async def validate(key):
            users = {"alice-key": "alice", "bob-key": "bob"}
            return ValidationResult(
                valid=key in users,
                user_id=users.get(key),
                metadata={"unity_mcp_permissions": grants} if grants is not None else None,
            )

        monkeypatch.setattr(service, "_validate_external", validate)
        with monkeypatch.context() as patch:
            patch.setattr(Tool, "from_function", prepared_tool_factory)
            server = create_mcp_server(False)
            server.enable(names={"manage_scene"}, components={"tool"})
            return server

    server = create()
    server._fixture_factory = create
    return server, hosted


@asynccontextmanager
async def connected(flow_server, *, tools=None):
    server, hosted = flow_server
    app = server.http_app(path="/mcp", stateless_http=True, json_response=True)
    async with app.router.lifespan_context(app):
        async with anyio.create_task_group() as group:
            peer = ScenePeer(
                app, "alice-key", "a", "A", None if hosted else config.local_auth_token
            )
            try:
                await peer.connect(group)
                group.start_soon(peer.respond)
                await peer.catalog(
                    tools
                    if tools is not None
                    else [{"name": "manage_scene"}, {"name": "get_editor_state"}]
                )
                headers = {"Accept": "application/json, text/event-stream"}
                headers.update(
                    {"x-api-key": "alice-key"}
                    if hosted
                    else {LOCAL_AUTH_HEADER: config.local_auth_token}
                )
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://fixture",
                    headers=headers,
                ) as client:
                    yield server, client, peer, group
            finally:
                with anyio.CancelScope(shield=True):
                    await peer.finish()
                group.cancel_scope.cancel()


async def request(
    client,
    *,
    state=None,
    answer=None,
    capabilities=None,
    arguments=None,
    request_id="initial",
    headers=None,
    name="manage_scene",
):
    body = copy.deepcopy(
        _body(
            "tools/call",
            {
                "name": name,
                "arguments": arguments
                if arguments is not None
                else {"action": "load", "additive": True, "unity_instance": "A@a"},
            },
        )
    )
    body["id"] = request_id
    body["params"]["_meta"]["io.modelcontextprotocol/clientCapabilities"] = (
        capabilities if capabilities is not None else {"elicitation": {"form": {}}}
    )
    if state is not None:
        body["params"]["requestState"] = state
    if answer is not None:
        body["params"]["inputResponses"] = answer
    return await client.post(
        "/mcp", json=body, headers={**_headers("tools/call", name=name), **(headers or {})}
    )


def accepted(path="Assets/Scenes/A.unity"):
    return {"scene_path": {"action": "accept", "content": {"path": path}}}


@pytest.mark.asyncio
@pytest.mark.parametrize("flow_server", [True, False], indirect=True, ids=["hosted", "local"])
async def test_missing_scene_target_is_a_sealed_input_round_before_any_native_work(
    flow_server, monkeypatch
):
    # Given: an authenticated selected live instance and a genuinely missing load target.
    import core.telemetry_decorator as telemetry

    milestone = Mock()
    monkeypatch.setattr(telemetry, "record_milestone", milestone)
    async with connected(flow_server) as (_, client, peer, _):
        # When: a modern form-capable caller requests that incomplete load.
        response = await request(client)
        message = response.json()
        # Then: a real input-required leg, no preflight/mutation, and no success milestone.
        assert response.status_code == 200, message
        result = message["result"]
        assert result["resultType"] == "input_required", result
        assert list(result["inputRequests"]) == ["scene_path"]
        assert result["inputRequests"]["scene_path"]["method"] == "elicitation/create"
        assert result["requestState"].startswith("v1.")
        assert "nonce" not in result["requestState"]
        assert not peer.commands
        milestone.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("flow_server", [True, False], indirect=True, ids=["hosted", "local"])
async def test_accept_retries_original_arguments_and_runs_the_existing_load_once(flow_server):
    # Given: a complete first MRTR leg, with original flags and explicit selector.
    async with connected(flow_server) as (_, client, peer, _):
        first = (await request(client)).json()["result"]
        assert first["resultType"] == "input_required", first
        # When: the independent retry echoes state and submits only the missing field.
        response = await request(
            client, state=first["requestState"], answer=accepted(), request_id="retry"
        )
        message = response.json()
        # Then: ordinary SDK/tool/preflight/native conversion receives exactly that load.
        assert response.status_code == 200, message
        assert message["id"] == "retry"
        result = message["result"]
        assert result["resultType"] == "complete"
        assert result["structuredContent"]["success"] is True
        assert len(peer.loads) == 1
        assert peer.loads[0]["params"] == {
            "action": "load",
            "path": "Assets/Scenes/A.unity",
            "additive": True,
        }
        assert any(command["name"] == "get_editor_state" for command in peer.commands)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["decline", "cancel"])
async def test_decline_and_cancel_do_no_native_work(flow_server, action):
    # Given: a sealed pending input question, without any executed native command.
    async with connected(flow_server) as (_, client, peer, _):
        first = (await request(client)).json()["result"]
        # When: the client explicitly declines or dismisses the input.
        response = await request(
            client,
            state=first["requestState"],
            answer={"scene_path": {"action": action}},
            request_id="refusal",
        )
        # Then: a terminal domain outcome is not mistaken for consent or an operation.
        result = response.json()["result"]
        assert result["resultType"] == "complete"
        assert result["structuredContent"]["success"] is False
        assert not peer.commands


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "capabilities,wants_input",
    [
        ({}, False),
        ({"elicitation": {}}, True),
        ({"elicitation": {"form": {}}}, True),
        ({"elicitation": {"url": {}}}, False),
    ],
)
async def test_per_request_form_capabilities_control_the_optional_path(
    flow_server, capabilities, wants_input
):
    # Given: the same incomplete request with independent per-request capabilities.
    async with connected(flow_server) as (_, client, peer, _):
        # When: a declared empty/form/URL-only/absent capability reaches the real SDK.
        response = await request(client, capabilities=capabilities)
        # Then: only form support permits an ask; ordinary fallback keeps zero dispatch.
        result = response.json()["result"]
        assert (result["resultType"] == "input_required") == wants_input
        if not wants_input:
            assert result["structuredContent"]["success"] is False
        assert not peer.commands


@pytest.mark.asyncio
@pytest.mark.parametrize("flow_server", [(True, False)], indirect=True)
async def test_default_disabled_keeps_original_missing_target_result(flow_server):
    # Given: the normal server configuration without optional input flow.
    async with connected(flow_server) as (_, client, peer, _):
        # When: even a form-capable modern client omits the scene target.
        response = await request(client)
        # Then: original ordinary validation remains available with no state or command.
        result = response.json()["result"]
        assert result["resultType"] == "complete"
        assert result["structuredContent"]["message"] == "load requires name, path, or build_index."
        assert "requestState" not in result
        assert not peer.commands


@pytest.mark.asyncio
async def test_complete_request_never_repeats_authorized_consent(flow_server):
    # Given: a fully supplied authorized load with a form-capable client.
    async with connected(flow_server) as (_, client, peer, _):
        # When: the target is already known in the original arguments.
        response = await request(
            client,
            arguments={
                "action": "load",
                "path": "Assets/Scenes/A.unity",
                "additive": True,
                "unity_instance": "A@a",
            },
        )
        # Then: ordinary native execution happens directly, without an input round.
        result = response.json()["result"]
        assert result["resultType"] == "complete"
        assert result["structuredContent"]["success"] is True
        assert len(peer.loads) == 1


@pytest.mark.asyncio
async def test_unrelated_read_does_not_invalidate_the_logical_scene_question(flow_server):
    # Given: a scene-path prompt for one live selected socket generation.
    async with connected(flow_server) as (_, client, peer, _):
        first = (await request(client)).json()["result"]
        # When: an independent read advances observation/command state while the human answers.
        read_body = _body("resources/read", {"uri": "mcpforunity://editor/state"})
        read_body["params"]["_meta"]["unity_instance"] = "A@a"
        observed = await client.post(
            "/mcp",
            json=read_body,
            headers=_headers("resources/read", name="mcpforunity://editor/state"),
        )
        assert observed.status_code == 200, observed.text
        assert json.loads(observed.json()["result"]["contents"][0]["text"])["success"] is True
        response = await request(
            client, state=first["requestState"], answer=accepted(), request_id="after-read"
        )
        # Then: logical instance/socket identity is stable and the accepted target runs once.
        assert response.json()["result"]["structuredContent"]["success"] is True
        assert len(peer.loads) == 1


@pytest.mark.asyncio
async def test_missing_answer_reasks_without_dispatching_or_implicitly_accepting(flow_server):
    # Given: one pending question and the original signed tool arguments.
    async with connected(flow_server) as (_, client, peer, _):
        first = (await request(client)).json()["result"]
        # When: the client supplies no response for the requested field.
        second = (
            await request(client, state=first["requestState"], answer={}, request_id="no-answer")
        ).json()["result"]
        # Then: the same question is asked again without any native command.
        assert second["resultType"] == "input_required"
        assert list(second["inputRequests"]) == ["scene_path"]
        assert not peer.commands
        final = (
            await request(
                client, state=second["requestState"], answer=accepted(), request_id="answered"
            )
        ).json()["result"]
        assert final["structuredContent"]["success"] is True
        assert len(peer.loads) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["tamper", "principal", "arguments", "orphan"])
async def test_state_integrity_owner_and_original_arguments_precede_dispatch(flow_server, change):
    # Given: a valid pending state bound to Alice's original load and explicit target.
    async with connected(flow_server) as (_, client, peer, _):
        first = (await request(client)).json()["result"]
        state = first["requestState"]
        kwargs = {"state": state, "answer": accepted(), "request_id": "attack"}
        if change == "tamper":
            kwargs["state"] = state[:-1] + ("A" if state[-1] != "A" else "B")
        elif change == "principal":
            kwargs["headers"] = {"x-api-key": "bob-key"}
        elif change == "arguments":
            kwargs["arguments"] = {"action": "load", "additive": False, "unity_instance": "A@a"}
        else:
            kwargs["state"] = None
        # When: retry state is modified, cross-user/cross-args, or absent.
        response = await request(client, **kwargs)
        # Then: the actual SDK/application boundary refuses before native work.
        assert response.status_code == 400, response.text
        assert response.json()["error"]["code"] == -32602
        assert not peer.commands


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        None,
        1,
        True,
        "",
        "../outside.unity",
        "Packages/P/A.unity",
        "Assets/../Outside.unity",
        "Assets\\A.unity",
        "Assets/A.txt",
        "Assets//A.unity",
        "/Assets/A.unity",
        "Assets/./A.unity",
        "Assets/\x00A.unity",
        "C:/Assets/A.unity",
        "Assets/" + "a" * 501 + ".unity",
    ],
)
async def test_accepted_field_is_validated_before_any_native_work(flow_server, path):
    # Given: a valid requestState whose submitted target does not satisfy the flat form.
    async with connected(flow_server) as (_, client, peer, _):
        first = (await request(client)).json()["result"]
        # When: a client bypasses form UI/schema validation.
        response = await request(
            client, state=first["requestState"], answer=accepted(path), request_id="invalid-path"
        )
        # Then: server validation refuses rather than interpreting or searching a target.
        assert response.status_code == 400, response.text
        assert response.json()["error"]["code"] == -32602
        assert not peer.commands


@pytest.mark.asyncio
async def test_concurrent_and_later_retries_never_resend_the_load(flow_server):
    # Given: one sealed nonce and a real native load reply deliberately held at the peer.
    async with connected(flow_server) as (_, client, peer, _):
        first = (await request(client)).json()["result"]
        peer.hold_load = True
        replies = []

        async def accept_original():
            replies.append(
                await request(
                    client, state=first["requestState"], answer=accepted(), request_id="accepted"
                )
            )

        async with anyio.create_task_group() as requests:
            requests.start_soon(accept_original)
            try:
                with anyio.fail_after(watchdog_timeout()):
                    await peer.dispatched.wait()
                # When: another retry races the active native operation.
                concurrent = await request(
                    client, state=first["requestState"], answer=accepted(), request_id="concurrent"
                )
                assert concurrent.json()["result"]["structuredContent"]["success"] is False
            finally:
                peer.release.set()
        assert replies[0].json()["result"]["structuredContent"]["success"] is True
        later = await request(
            client, state=first["requestState"], answer=accepted(), request_id="later"
        )
        # Then: one-attempt status is truthful and only one mutation command exists.
        assert later.json()["result"]["structuredContent"]["success"] is False
        assert len(peer.loads) == 1


@pytest.mark.asyncio
async def test_public_sdk_drives_the_input_round_over_authenticated_http(flow_server):
    # Given: a real modern SDK client using its installed elicitation callback and retry driver.
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport
    import httpx2

    async with connected(flow_server) as (_, _, peer, _):
        submitted = []
        calls = []

        async def observe(request):
            body = json.loads(request.content)
            if body["method"] == "tools/call":
                calls.append(body)

        def factory(**kwargs):
            return httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=peer.actual_app),
                event_hooks={"request": [observe]},
                **kwargs,
            )

        async def elicit(_message, _response_type, params, _context):
            submitted.append(params)
            return {"path": "Assets/Scenes/A.unity"}

        transport = StreamableHttpTransport(
            "http://fixture/mcp", headers={"x-api-key": "alice-key"}, httpx_client_factory=factory
        )
        # When: the user-provided callback answers the missing field once.
        async with Client(transport, mode="2026-07-28", elicitation_handler=elicit) as client:
            result = await client.call_tool(
                "manage_scene", {"action": "load", "additive": True, "unity_instance": "A@a"}
            )
        # Then: the SDK preserves original arguments, changes request IDs, and executes once.
        assert result.is_error is False
        assert len(submitted) == 1
        assert len(calls) == 2
        assert calls[0]["id"] != calls[1]["id"]
        assert calls[0]["params"]["arguments"] == calls[1]["params"]["arguments"]
        assert len(peer.loads) == 1


@pytest.mark.asyncio
async def test_legacy_sdk_keeps_the_ordinary_missing_target_result(flow_server):
    # Given: an actual initialized legacy client that also declares elicitation support.
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport
    import httpx2

    async with connected(flow_server) as (_, _, peer, _):
        asked = []

        def factory(**kwargs):
            return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=peer.actual_app), **kwargs)

        async def elicit(*_args):
            asked.append(True)
            return {"path": "Assets/Scenes/A.unity"}

        transport = StreamableHttpTransport(
            "http://fixture/mcp", headers={"x-api-key": "alice-key"}, httpx_client_factory=factory
        )
        # When: the same incomplete load reaches the legacy protocol.
        async with Client(transport, mode="legacy", elicitation_handler=elicit) as client:
            result = await client.call_tool(
                "manage_scene", {"action": "load", "unity_instance": "A@a"}, raise_on_error=False
            )
        # Then: no reverse prompt or native command is introduced by the optional feature.
        assert result.is_error is True
        assert result.structured_content["success"] is False
        assert not asked
        assert not peer.commands


@pytest.mark.asyncio
async def test_reopened_runtime_refuses_the_previous_ephemeral_key(flow_server):
    # Given: an actual sealed input from one runtime that has completely closed.
    server, hosted = flow_server
    async with connected(flow_server) as (_, client, peer, _):
        old = (await request(client)).json()["result"]["requestState"]
        assert not peer.commands
    other = server._fixture_factory()
    # When: a fresh server with its own SDK key receives the original echo.
    async with connected((other, hosted)) as (_, client, peer, _):
        response = await request(client, state=old, answer=accepted(), request_id="old-runtime")
        # Then: it fails closed before the new instance can receive any command.
        assert response.status_code == 400, response.text
        assert response.json()["error"]["code"] == -32602
        assert not peer.commands


@pytest.mark.asyncio
async def test_current_session_replacement_refuses_pending_input(flow_server):
    # Given: one question for a session that is then replaced by the same user's Editor.
    async with connected(flow_server) as (_, client, peer, group):
        first = (await request(client)).json()["result"]
        replacement = ScenePeer(peer.actual_app, "alice-key", "a", "A")
        try:
            await replacement.connect(group)
            group.start_soon(replacement.respond)
            await replacement.catalog([{"name": "manage_scene"}])
            assert replacement.session_id != peer.session_id
            # When: a retry carries valid SDK state for the superseded generation.
            response = await request(
                client, state=first["requestState"], answer=accepted(), request_id="replaced"
            )
            # Then: fresh authenticated routing cannot silently redirect the prompt.
            assert response.status_code == 400, response.text
            assert response.json()["error"]["code"] == -32602
            assert not replacement.commands
        finally:
            with anyio.CancelScope(shield=True):
                await replacement.finish()


@pytest.mark.asyncio
async def test_other_project_and_foreign_tenant_cannot_consume_the_question(flow_server):
    # Given: Alice owns two projects and Bob owns a different authenticated project.
    async with connected(flow_server) as (_, client, peer, group):
        first = (await request(client)).json()["result"]
        others = [
            ScenePeer(peer.actual_app, "alice-key", "b", "B"),
            ScenePeer(peer.actual_app, "bob-key", "c", "C"),
        ]
        try:
            for other in others:
                await other.connect(group)
                group.start_soon(other.respond)
                await other.catalog([{"name": "manage_scene"}])
            # When: a valid echo is sent with changed project or foreign credentials.
            for selector, headers in [("B@b", {}), ("C@c", {"x-api-key": "bob-key"})]:
                response = await request(
                    client,
                    state=first["requestState"],
                    answer=accepted(),
                    arguments={"action": "load", "additive": True, "unity_instance": selector},
                    headers=headers,
                    request_id=selector,
                )
                assert response.status_code == 400, response.text
                assert response.json()["error"]["code"] == -32602
            # Then: all three independently scoped Editors have zero commands.
            assert not peer.commands
            assert all(not other.commands for other in others)
        finally:
            for other in others:
                with anyio.CancelScope(shield=True):
                    await other.finish()


@pytest.mark.asyncio
@pytest.mark.parametrize("flow_server", [False], indirect=True)
async def test_stale_local_auth_is_rejected_before_request_state_lookup(flow_server):
    # Given: an authenticated local runtime and an original sealed question.
    async with connected(flow_server) as (_, client, peer, _):
        first = (await request(client)).json()["result"]
        # When: a stale local credential accompanies even a malformed state echo.
        response = await request(
            client,
            state=first["requestState"] + "tamper",
            answer=accepted(),
            headers={LOCAL_AUTH_HEADER: "stale-local-credential"},
        )
        # Then: current authentication wins over state validation, without dispatch.
        assert response.status_code == 401, response.text
        assert not peer.commands


@pytest.mark.asyncio
@pytest.mark.parametrize("unavailable", ["version", "policy-class", "question-budget"])
async def test_unreviewed_optional_sdk_leaves_the_normal_server_available(
    flow_server, monkeypatch, unavailable
):
    # Given: the optional gate cannot establish the reviewed SDK contract.
    from transport import scene_load_inputs

    if unavailable == "version":
        monkeypatch.setattr(scene_load_inputs, "version", lambda _: "unreviewed")
    elif unavailable == "policy-class":
        import mcp.server.request_state as sdk_state

        monkeypatch.delattr(sdk_state, "RequestStateSecurity")
    else:
        # The app-owned ask has no room beyond the unchanged 32768-byte reserve.
        monkeypatch.setattr(scene_load_inputs, "MAX_RESPONSE_BYTES", 32_768)
    server, hosted = flow_server
    fallback = server._fixture_factory()
    assert fallback._scene_load_inputs is None
    # When: an authenticated caller uses the ordinary missing-target request.
    async with connected((fallback, hosted)) as (_, client, peer, _):
        result = (await request(client)).json()["result"]
        # Then: default SDK behavior remains usable and does not pretend to ask.
        assert result["resultType"] == "complete"
        assert result["structuredContent"]["success"] is False
        assert not peer.commands


@pytest.mark.asyncio
async def test_missing_optional_elicit_result_keeps_an_unrelated_ordinary_tool_usable(
    flow_server, monkeypatch
):
    # Given: a missing optional public response export in an otherwise usable base SDK.
    import mcp_types

    server, hosted = flow_server
    async with connected(flow_server) as (_, client, peer, _):
        ordinary = await request(
            client,
            name="set_active_instance",
            arguments={"instance": "A@a", "unity_instance": "A@a"},
            request_id="ordinary-reference",
        )
        expected = ordinary.json()["result"]
        assert not peer.commands
    monkeypatch.delattr(mcp_types, "ElicitResult")
    fallback = server._fixture_factory()
    async with connected((fallback, hosted)) as (_, client, peer, _):
        # When: a real authenticated call uses an unrelated existing server-only tool.
        response = await request(
            client,
            name="set_active_instance",
            arguments={"instance": "A@a", "unity_instance": "A@a"},
            request_id="ordinary-without-optional-hook",
        )
        body = response.json()
        # Then: ordinary SDK behavior survives and the optional middleware stays absent.
        assert "result" in body, body
        assert body["result"] == expected
        assert fallback._scene_load_inputs is None
        assert not peer.commands


@pytest.mark.asyncio
async def test_missing_answer_reseal_cannot_refresh_app_expiry(flow_server):
    # Given: the app's separate monotonic input clock, while SDK envelopes remain current.
    from transport.scene_load_inputs import InputLedger

    server, _ = flow_server
    now = [100.0]
    server._scene_load_inputs.ledger = InputLedger(clock=lambda: now[0])
    async with connected(flow_server) as (_, client, peer, _):
        first = (await request(client)).json()["result"]
        now[0] = 219.0
        refreshed = (
            await request(client, state=first["requestState"], answer={}, request_id="reask")
        ).json()["result"]
        assert refreshed["resultType"] == "input_required"
        # When: a freshly sealed second round crosses the original app deadline.
        now[0] = 220.0
        response = await request(
            client, state=refreshed["requestState"], answer=accepted(), request_id="expired"
        )
        # Then: no native work runs and expired inactive capacity is reclaimed.
        assert response.status_code == 400, response.text
        assert response.json()["error"]["code"] == -32602
        assert server._scene_load_inputs.ledger.record_count == 0
        assert not peer.commands


@pytest.mark.asyncio
async def test_sdk_wall_clock_expiry_precedes_continuation_with_live_app_record(
    flow_server, monkeypatch
):
    # Given: an actual SDK envelope and an independently controlled SDK wall clock.
    import mcp.server.request_state as sdk_state

    now = [1000.0]
    monkeypatch.setattr(sdk_state, "time", SimpleNamespace(time=lambda: now[0]))
    server, _ = flow_server
    async with connected(flow_server) as (_, client, peer, _):
        first = (await request(client)).json()["result"]
        claims = json.loads(server._scene_load_inputs.policy.codec.unseal(first["requestState"]))
        assert server._scene_load_inputs.ledger.record_count == 1
        # When: the signed SDK lifetime expires while the app monotonic record remains live.
        now[0] = claims["exp"]
        response = await request(
            client, state=first["requestState"], answer=accepted(), request_id="sdk-expired"
        )
        # Then: the SDK refuses the envelope before the application can claim native work.
        assert response.status_code == 400, response.text
        assert response.json()["error"]["code"] == -32602
        assert server._scene_load_inputs.ledger.record_count == 1
        assert not peer.commands


@pytest.mark.asyncio
async def test_capacity_refusal_preserves_ordinary_fallback_and_existing_question(flow_server):
    # Given: one admitted question occupying a deliberately small ledger.
    from transport.scene_load_inputs import InputLedger

    server, _ = flow_server
    server._scene_load_inputs.ledger = InputLedger(total=1, principal=1)
    async with connected(flow_server) as (_, client, peer, _):
        first = (await request(client)).json()["result"]
        # When: a separate initial call cannot reserve its question.
        refused = (await request(client, request_id="full")).json()["result"]
        assert refused["resultType"] == "complete"
        assert refused["structuredContent"]["success"] is False
        assert not peer.commands
        # Then: fallback has not evicted the previously admitted valid continuation.
        final = (
            await request(
                client, state=first["requestState"], answer=accepted(), request_id="existing"
            )
        ).json()["result"]
        assert final["structuredContent"]["success"] is True
        assert len(peer.loads) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("success", [True, False], ids=["success", "native-domain-error"])
async def test_accepted_continuation_matches_the_whole_ordinary_modern_result(flow_server, success):
    # Given: a real native response used for both the completed input and direct calls.
    async with connected(flow_server) as (_, client, peer, _):
        if not success:
            peer.load_result = {
                "success": False,
                "error": "scene_not_found",
                "message": "fixture missing",
            }
        first = (await request(client)).json()["result"]
        # When: a valid input is added only to the typed continuation.
        augmented = await request(
            client, state=first["requestState"], answer=accepted(), request_id="augmented"
        )
        ordinary = await request(
            client,
            arguments={
                "action": "load",
                "path": "Assets/Scenes/A.unity",
                "additive": True,
                "unity_instance": "A@a",
            },
            request_id="ordinary",
        )
        # Then: all content, structured error/success and modern SDK metadata remain equal.
        assert augmented.json()["result"] == ordinary.json()["result"]
        assert len(peer.loads) == 2
        replay = await request(
            client, state=first["requestState"], answer=accepted(), request_id="replay"
        )
        assert (
            replay.json()["result"]["structuredContent"]["error"] == "elicitation_already_attempted"
        )
        assert len(peer.loads) == 2


@pytest.mark.asyncio
async def test_actual_sdk_sealed_question_and_envelope_fit_the_reserved_budget(flow_server):
    # Given: the final SDK-sealed output, beyond the middleware's pre-admitted plaintext.
    from models.response_limits import MAX_RESPONSE_BYTES, response_size

    server, _ = flow_server
    async with connected(flow_server) as (_, client, peer, _):
        response = await request(client)
        result = response.json()["result"]
        # When: the SDK's public codec verifies and reveals the app-owned nonce.
        plaintext = server._scene_load_inputs.policy.codec.unseal(result["requestState"])
        claims = json.loads(plaintext)
        # Then: bounded purpose/nonce state and full final framing are both admitted.
        assert len(claims["s"].encode("utf-8")) <= 512
        assert claims["aud"] == "unity-mcp/scene-load-input/v1"
        assert claims["m"] == "tools/call"
        assert "alice-key" not in plaintext.decode()
        assert len(response.content) < MAX_RESPONSE_BYTES
        assert response_size(result, max_bytes=MAX_RESPONSE_BYTES - 32_768) is not None
        assert not peer.commands


@pytest.mark.asyncio
async def test_accepted_postdispatch_deadline_is_unknown_and_never_retries(
    flow_server, monkeypatch
):
    # Given: an ordinary short command budget and a native load whose reply is held.
    original = PluginHub._command_timeouts
    monkeypatch.setattr(
        PluginHub,
        "_command_timeouts",
        classmethod(
            lambda cls, name, args: (0.2, 0.2) if name == "manage_scene" else original(name, args)
        ),
    )
    async with connected(flow_server) as (_, client, peer, _):
        first = (await request(client)).json()["result"]
        peer.hold_load = True
        try:
            # When: the accepted native command is dispatched before its reply deadline.
            response = await request(
                client, state=first["requestState"], answer=accepted(), request_id="timeout"
            )
            result = response.json()["result"]["structuredContent"]
            assert peer.dispatched.is_set()
            # Then: the ordinary uncertainty is truthful; later input replay cannot resend.
            assert result["data"]["reason"] == "outcome_unknown"
            replay = await request(
                client, state=first["requestState"], answer=accepted(), request_id="after-timeout"
            )
            assert (
                replay.json()["result"]["structuredContent"]["error"]
                == "elicitation_already_attempted"
            )
            assert len(peer.loads) == 1
        finally:
            peer.release.set()


@pytest.mark.asyncio
async def test_cancelled_accepted_request_remains_attempted_without_resending(flow_server):
    # Given: one accepted mutation whose native peer has received but not answered it.
    async with connected(flow_server) as (server, client, peer, _):
        first = (await request(client)).json()["result"]
        peer.hold_load = True
        scopes = []
        exited = anyio.Event()

        async def caller():
            with anyio.CancelScope() as scope:
                scopes.append(scope)
                try:
                    await request(
                        client,
                        state=first["requestState"],
                        answer=accepted(),
                        request_id="cancelled",
                    )
                finally:
                    exited.set()

        try:
            async with anyio.create_task_group() as requests:
                requests.start_soon(caller)
                with anyio.fail_after(watchdog_timeout()):
                    await peer.dispatched.wait()
                # When: the HTTP caller abandons its already-dispatched request.
                scopes[0].cancel()
                with anyio.fail_after(watchdog_timeout()):
                    await exited.wait()
            # Then: cancellation does not restore consent or claim native termination.
            replay = await request(
                client, state=first["requestState"], answer=accepted(), request_id="after-cancel"
            )
            assert (
                replay.json()["result"]["structuredContent"]["error"]
                == "elicitation_already_attempted"
            )
            assert len(peer.loads) == 1
            assert server._scene_load_inputs.ledger.record_count == 1
        finally:
            peer.release.set()


@pytest.mark.asyncio
async def test_close_keeps_active_admission_until_the_real_ordinary_handler_exits(flow_server):
    # Given: an accepted ordinary continuation held at a real fake-plugin reply.
    async with connected(flow_server) as (server, client, peer, _):
        first = (await request(client)).json()["result"]
        peer.hold_load = True
        replies = []

        async def caller():
            replies.append(
                await request(
                    client, state=first["requestState"], answer=accepted(), request_id="active"
                )
            )

        async with anyio.create_task_group() as requests:
            requests.start_soon(caller)
            try:
                with anyio.fail_after(watchdog_timeout()):
                    await peer.dispatched.wait()
                # When: the exact lifecycle close hook rejects additional input work.
                server._scene_load_inputs.close()
                # Then: actual active ownership remains charged until continuation finally.
                assert server._scene_load_inputs.ledger.record_count == 1
            finally:
                peer.release.set()
        assert replies[0].json()["result"]["structuredContent"]["success"] is True
        assert server._scene_load_inputs.ledger.record_count == 0


@pytest.mark.asyncio
async def test_original_budget_expiring_before_claim_never_dispatches_mutation(
    flow_server, monkeypatch
):
    # Given: a real outer HTTP deadline while the guard's selected-scope lookup is held.
    server, _ = flow_server
    original = PluginHub._command_timeouts
    monkeypatch.setattr(
        PluginHub,
        "_command_timeouts",
        classmethod(
            lambda cls, name, args: (0.05, 0.05) if name == "manage_scene" else original(name, args)
        ),
    )
    entered = anyio.Event()

    async def hold_scope(*_args):
        entered.set()
        await anyio.sleep_forever()

    monkeypatch.setattr(server._scene_load_inputs, "_scope", hold_scope)
    async with connected(flow_server) as (_, client, peer, _):
        # When: the existing request deadline expires before a prompt or accepted claim.
        response = await request(client)
        # Then: the ordinary pre-dispatch result is truthful and no ledger/native charge exists.
        assert entered.is_set()
        assert (
            response.json()["result"]["structuredContent"]["data"]["reason"]
            == "command_deadline_exceeded"
        )
        assert server._scene_load_inputs.ledger.record_count == 0
        assert not peer.commands


@pytest.mark.asyncio
async def test_unknown_answer_keys_do_not_change_original_load_arguments(flow_server):
    # Given: a valid scene answer plus an unrelated response entry controlled by the client.
    async with connected(flow_server) as (_, client, peer, _):
        first = (await request(client)).json()["result"]
        answers = {
            **accepted(),
            "unrequested": {
                "action": "accept",
                "content": {"path": "Assets/Other.unity", "additive": False},
            },
        }
        # When: the typed retry includes both keys.
        result = (await request(client, state=first["requestState"], answer=answers)).json()[
            "result"
        ]
        # Then: only the requested flat field is added to the original action and flag.
        assert result["structuredContent"]["success"] is True
        assert peer.loads[0]["params"] == {
            "action": "load",
            "path": "Assets/Scenes/A.unity",
            "additive": True,
        }


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [{"additive": "true"}, {"additive": "invalid"}, {"page_size": 1}])
async def test_unreviewed_original_argument_combinations_delegate_ordinary_validation(
    flow_server, extra
):
    # Given: a valid schema-level call outside the narrowly reviewed missing-input arguments.
    async with connected(flow_server) as (_, client, peer, _):
        # When: coercing flags or an unrelated valid optional field accompany the load.
        response = await request(
            client, arguments={"action": "load", "unity_instance": "A@a", **extra}
        )
        # Then: the original handler explains the incomplete/invalid request without an ask.
        result = response.json()["result"]
        assert result["resultType"] == "complete"
        assert result["structuredContent"]["success"] is False
        assert not peer.commands


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["manage_scene", "scene_lifecycle"])
async def test_replaced_builtin_is_not_interpreted_as_the_repository_scene_load(
    flow_server, tool_name
):
    # Given: a real public FunctionTool replacement under the same tool name.
    from fastmcp.tools.base import Tool

    server, _ = flow_server
    calls = []

    async def replacement(action: str, additive: bool = False):
        calls.append((action, additive))
        return {"success": False, "error": "fixture_replacement"}

    server.add_tool(Tool.from_function(replacement, name=tool_name))
    async with connected(
        flow_server, tools=[{"name": tool_name}, {"name": "get_editor_state"}]
    ) as (_, client, peer, _):
        # When: the form-capable request targets the replacement instead of the reviewed function.
        response = await request(client, name=tool_name)
        # Then: its own semantics are retained with no prompt or native invocation.
        assert response.json()["result"]["structuredContent"]["error"] == "fixture_replacement"
        assert calls == [("load", True)]
        assert not peer.commands


@pytest.mark.asyncio
async def test_current_catalog_withdrawal_is_checked_before_pending_input(flow_server):
    # Given: a valid pending question followed by a real committed catalog withdrawal.
    async with connected(flow_server) as (_, client, peer, _):
        first = (await request(client)).json()["result"]
        await peer.catalog([])
        # When: an otherwise valid retry arrives after the tool became unavailable.
        response = await request(
            client, state=first["requestState"], answer=accepted(), request_id="withdrawn"
        )
        ordinary = await request(client, request_id="ordinary-withdrawn")
        # Then: existing current-tenant visibility refuses before native continuation.
        assert response.json()["error"] == ordinary.json()["error"]
        assert not peer.commands


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "flow_server", [(True, True, ["read", "edit", "run"]), False], indirect=True
)
async def test_focused_scene_input_resumes_once_without_enabling_legacy_tool(flow_server):
    catalog = [{"name": "scene_lifecycle"}, {"name": "get_editor_state"}]
    async with connected(flow_server, tools=catalog) as (_, client, peer, _):
        first = (await request(client, name="scene_lifecycle")).json()["result"]
        assert first["resultType"] == "input_required"
        assert not peer.commands
        args = dict(name="scene_lifecycle", state=first["requestState"], answer=accepted())
        completed = (await request(client, **args, request_id="focused-resume")).json()["result"]
        assert completed["structuredContent"]["success"] is True
        assert len(peer.loads) == 1 and peer.loads[0]["name"] == "scene_lifecycle"
        assert peer.loads[0]["params"] == {
            "action": "load",
            "path": "Assets/Scenes/A.unity",
            "additive": True,
        }
        await request(client, **args, request_id="focused-replay")
        assert len(peer.loads) == 1


@pytest.mark.asyncio
async def test_focused_scene_state_cannot_switch_names_or_survive_withdrawal(flow_server):
    catalog = [{"name": name} for name in ("scene_lifecycle", "manage_scene", "get_editor_state")]
    async with connected(flow_server, tools=catalog) as (_, client, peer, _):
        first = (await request(client, name="scene_lifecycle")).json()["result"]
        assert first["resultType"] == "input_required"
        switched = await request(
            client, name="manage_scene", state=first["requestState"], answer=accepted()
        )
        assert "error" in switched.json()
        assert not peer.commands
        await peer.catalog([{"name": "get_editor_state"}])
        withdrawn = await request(
            client, name="scene_lifecycle", state=first["requestState"], answer=accepted()
        )
        assert "error" in withdrawn.json()
        assert not peer.commands


@pytest.mark.asyncio
@pytest.mark.parametrize("flow_server", [(True, True, ["read"])], indirect=True)
async def test_focused_scene_sdk_and_native_dispatch_honor_read_only_authority(flow_server):
    catalog = [{"name": name} for name in ("scene_inspect", "scene_lifecycle", "get_editor_state")]
    async with connected(flow_server, tools=catalog) as (_, client, peer, _):
        inspected = await request(
            client,
            name="scene_inspect",
            arguments={"action": "get_active", "unity_instance": "A@a"},
        )
        assert inspected.json()["result"]["structuredContent"]["success"] is True
        assert peer.loads[-1]["name"] == "scene_inspect"
        before = len(peer.commands)
        denied = await request(
            client,
            name="scene_lifecycle",
            arguments={"action": "load", "path": "Assets/Scenes/A.unity", "unity_instance": "A@a"},
        )
        payload = denied.json()
        assert "error" in payload or payload.get("result", {}).get("isError")
        assert len(peer.commands) == before
