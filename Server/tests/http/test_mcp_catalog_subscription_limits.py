"""Actual ASGI subscription streams preserve auth, scope and bounded ownership."""

import json
import secrets
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import anyio
import httpx
import pytest
from starlette.types import ASGIApp

from core.config import config
from core.local_auth import LOCAL_AUTH_HEADER
from services.api_key_service import ApiKeyService, ValidationResult
from tests.http.prepared_tool_registration import prepared_tool_factory  # pylint: disable=unused-import
from tests.http.test_mcp_protocol_wire import _body, _headers
from tests.http.test_request_body_limit import scope_for
from transport.catalog_subscriptions import EDITOR_STATE_URI, SubscriptionAdmission, WatchScope
from tests.test_editor_state_store import message as editor_state_message
from transport.plugin_hub import PluginHub


@dataclass
class ListenStream:
    """Real HTTP ASGI receive/send boundary with an optional slow-network gate."""

    app: ASGIApp
    request_id: str
    principal: str = "alice-key"
    local_token: str | None = field(default=None, repr=False)
    instance: str | None = "A@a"
    stall_changes: bool = False
    stall_ack: bool = False
    shield_send: bool = False
    notifications: dict = field(
        default_factory=lambda: {
            "toolsListChanged": True,
            "resourcesListChanged": True,
            "promptsListChanged": True,
            "resourceSubscriptions": ["fixture://unsupported"],
        }
    )
    frames: list = field(default_factory=list)
    acknowledged: anyio.Event = field(default_factory=anyio.Event)
    changed: anyio.Event = field(default_factory=anyio.Event)
    disconnect: anyio.Event = field(default_factory=anyio.Event)
    resume: anyio.Event = field(default_factory=anyio.Event)
    done: anyio.Event = field(default_factory=anyio.Event)

    async def run(self):
        body = dict(_body("subscriptions/listen", {"notifications": self.notifications}))
        body["id"] = self.request_id
        if self.instance is not None:
            body["params"]["_meta"]["unity_instance"] = self.instance
        first = True

        async def receive():
            nonlocal first
            if first:
                first = False
                return {"type": "http.request", "body": json.dumps(body).encode()}
            await self.disconnect.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            self.frames.append(message)
            content = message.get("body", b"")
            if b"notifications/subscriptions/acknowledged" in content:
                self.acknowledged.set()
                if self.stall_ack:
                    await self.resume.wait()
            if (
                b"notifications/tools/list_changed" in content
                or b"notifications/resources/updated" in content
            ):
                self.changed.set()
                if self.stall_changes:
                    with anyio.CancelScope(shield=self.shield_send):
                        await self.resume.wait()

        headers = [
            (name.lower().encode(), value.encode())
            for name, value in _headers("subscriptions/listen").items()
        ]
        headers += [
            (b"x-api-key", self.principal.encode()),
            (b"content-type", b"application/json"),
            (b"accept", b"application/json, text/event-stream"),
        ]
        if self.local_token is not None:
            headers.append((LOCAL_AUTH_HEADER.lower().encode(), self.local_token.encode()))
        try:
            await self.app(scope_for(headers, "/mcp"), receive, send)
        finally:
            self.done.set()

    def messages(self):
        content = b"".join(frame.get("body", b"") for frame in self.frames)
        if content.startswith(b"{"):
            return [json.loads(content)]
        return [
            json.loads(line[6:])
            for frame in self.frames
            for line in frame.get("body", b"").splitlines()
            if line.startswith(b"data: ")
        ]


class PluginStream:
    """Bounded authenticated ASGI websocket peer; next receive is a processing barrier."""

    def __init__(self, app, principal, project_hash, project_name, *, capabilities=()):
        self.app = app
        self.principal = principal
        self.project_hash = project_hash
        self.project_name = project_name
        self.capabilities = capabilities
        self.started = False
        self.welcome = anyio.Event()
        self.registered = anyio.Event()
        self.done = anyio.Event()
        self.session_id = None
        self.close_codes = []

    async def run(self, *, task_status=anyio.TASK_STATUS_IGNORED):
        processed = None

        async def receive():
            nonlocal processed
            # The Hub invokes receive again only after its previous frame's
            # handler returned. Readiness before queue consumption is not that
            # barrier, particularly for its pre-registration receive Task.
            if processed is not None:
                processed.set()
            message, processed = await self.reader.receive()
            return message

        async def send(message):
            if message["type"] == "websocket.send":
                payload = json.loads(message["text"])
                if payload["type"] == "welcome":
                    self.welcome.set()
                elif payload["type"] == "registered":
                    self.session_id = payload["session_id"]
                    self.registered.set()
            elif message["type"] == "websocket.close":
                self.close_codes.append(message["code"])
                await self.input.send(({"type": "websocket.disconnect", "code": 1000}, None))

        scope = scope_for(
            [(b"x-api-key", self.principal.encode())], "/hub/plugin", scope_type="websocket"
        )
        scope.update(scheme="ws", subprotocols=[])
        try:
            # Peers constructed after an earlier failing connection never start,
            # so they must not allocate streams that have no cleanup owner.
            self.input, self.reader = anyio.create_memory_object_stream[
                tuple[dict, anyio.Event | None]
            ](1)
            async with self.input, self.reader:
                self.started = True
                task_status.started()
                await self.app(scope, receive, send)
        finally:
            self.done.set()

    async def _wait(self, event):
        """Fail promptly on actual peer exit, retaining the existing wait bound."""
        finished = anyio.Event()

        async def watch(signal):
            await signal.wait()
            finished.set()

        with anyio.fail_after(3):
            async with anyio.create_task_group() as group:
                group.start_soon(watch, event)
                group.start_soon(watch, self.done)
                await finished.wait()
                group.cancel_scope.cancel()
        assert event.is_set(), "Plugin peer exited before the expected processing boundary"

    async def connect(self, group):
        await group.start(self.run)
        await self.input.send(({"type": "websocket.connect"}, None))
        await self._wait(self.welcome)
        await self.send_json(
            {
                "type": "register",
                "project_hash": self.project_hash,
                "project_name": self.project_name,
                "unity_version": "6000.0",
                "capabilities": list(self.capabilities),
            }
        )
        await self._wait(self.registered)
        assert self.session_id is not None

    async def send_json(self, payload):
        processed = anyio.Event()
        await self.input.send(
            ({"type": "websocket.receive", "text": json.dumps(payload)}, processed)
        )
        await self._wait(processed)

    async def catalog(self, tools):
        await self.send_json({"type": "register_tools", "tools": tools})

    async def close(self):
        if self.started and not self.done.is_set():
            await self.input.send(({"type": "websocket.disconnect", "code": 1000}, None))
            await wait(self.done)


def change_count(stream):
    return sum(
        message.get("method") == "notifications/tools/list_changed" for message in stream.messages()
    )


@pytest.fixture
def hosted_catalog(monkeypatch, prepared_tool_factory, request):
    from fastmcp.tools.base import Tool
    from main import create_mcp_server

    mode = getattr(request, "param", True)
    hosted, project_scoped = mode if isinstance(mode, tuple) else (mode, False)
    monkeypatch.setattr(config, "http_remote_hosted", hosted)
    monkeypatch.setattr(config, "local_auth_token", secrets.token_urlsafe(32))
    monkeypatch.setattr(config, "http_behind_tls_proxy", True)
    monkeypatch.setattr(config, "transport_mode", "http")
    monkeypatch.setenv("DISABLE_TELEMETRY", "1")
    monkeypatch.setenv("UNITY_MCP_SKIP_STARTUP_CONNECT", "1")
    validation = AsyncMock(
        side_effect=lambda key, **kwargs: ValidationResult(
            valid=key in {"alice-key", "bob-key"},
            user_id={"alice-key": "alice", "bob-key": "bob"}.get(key),
        )
    )
    # Stable catalog identities use normal positive caching. Cold-miss source
    # admission and TTL0 semantics have dedicated real-service auth scenarios.
    service = ApiKeyService("https://fixture.invalid/validate")
    monkeypatch.setattr(service, "_validate_external", validation)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(Tool, "from_function", prepared_tool_factory)
        server = create_mcp_server(project_scoped)
    return server.http_app(json_response=True), getattr(server, "_catalog_subscriptions")


async def wait(event):
    with anyio.fail_after(3):
        await event.wait()


async def register_scopes():
    registry = PluginHub._registry
    await registry.register("alice-a", "A", "a", "fixture", user_id="alice")
    await registry.register("alice-other", "Other", "other", "fixture", user_id="alice")
    await registry.register("bob-a", "A", "a", "fixture", user_id="bob")


@pytest.mark.asyncio
async def test_authenticated_scopes_ack_filter_route_and_cleanup(hosted_catalog):
    # Given: two tenants, including two authorized projects for Alice.
    app, runtime = hosted_catalog
    async with app.router.lifespan_context(app):
        await register_scopes()
        streams = [
            ListenStream(app, "a"),
            ListenStream(app, "other", instance="Other@other"),
            ListenStream(app, "b", principal="bob-key"),
        ]
        async with anyio.create_task_group() as group:
            for stream in streams:
                group.start_soon(stream.run)
            for stream in streams:
                await wait(stream.acknowledged)
                ack = stream.messages()[0]
                assert ack["params"]["notifications"] == {"toolsListChanged": True}
                assert (
                    ack["params"]["_meta"]["io.modelcontextprotocol/subscriptionId"]
                    == stream.request_id
                )
                assert b"mcp-session-id" not in dict(stream.frames[0]["headers"])

            # When: a committed catalog changes only for Alice's project A.
            await PluginHub._notify_mcp_tool_list_changed(
                user_id="alice", project_hash="a", session_id="alice-a", catalog_revision=1
            )
            await wait(streams[0].changed)
            await anyio.wait_all_tasks_blocked()

            # Then: the other project and tenant receive no event, and disconnect releases slots.
            assert not streams[1].changed.is_set()
            assert not streams[2].changed.is_set()
            for stream in streams:
                stream.disconnect.set()
                await wait(stream.done)
        assert not runtime._leases
        assert not runtime.bus.scopes


@pytest.mark.asyncio
@pytest.mark.parametrize("hosted_catalog", [(True, True)], indirect=True)
async def test_hosted_catalog_wire_commits_route_and_relist_across_generations(
    hosted_catalog, monkeypatch
):
    # Given: real authenticated websocket peers and HTTP subscribers for two tenants/projects.
    from services.custom_tool_service import CustomToolService

    app, runtime = hosted_catalog
    async with app.router.lifespan_context(app):
        async with anyio.create_task_group() as group:
            alice = PluginStream(app, "alice-key", "a", "A")
            other = PluginStream(app, "alice-key", "other", "Other")
            bob = PluginStream(app, "bob-key", "a", "A")
            foreign = PluginStream(app, "bob-key", "foreign", "Foreign")
            for plugin in (alice, other, bob, foreign):
                await plugin.connect(group)
            await other.catalog([{"name": "other_only"}, {"name": "run_tests"}])
            await bob.catalog(
                [
                    {
                        "name": "tenant_live",
                        "parameters": [{"name": "bob_value", "type": "string"}],
                    },
                    {"name": "read_console"},
                ]
            )
            await foreign.catalog([{"name": "foreign_only"}])

            denied = ListenStream(app, "foreign-selector", instance="Foreign@foreign")
            await denied.run()
            assert denied.frames[0]["status"] == 400
            assert not denied.acknowledged.is_set()
            assert not runtime._leases

            streams = [
                ListenStream(app, "alice-composed", stall_ack=True),
                ListenStream(app, "other-composed", instance="Other@other"),
                ListenStream(app, "bob-composed", principal="bob-key"),
            ]
            for stream in streams:
                group.start_soon(stream.run)
            for stream in streams:
                await wait(stream.acknowledged)

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
                headers={"Accept": "application/json, text/event-stream"},
            ) as http:

                async def listing(principal="alice-key", instance="A@a"):
                    body = dict(_body("tools/list"))
                    body["params"]["_meta"]["unity_instance"] = instance
                    response = await http.post(
                        "/mcp",
                        headers={**_headers("tools/list"), "x-api-key": principal},
                        json=body,
                    )
                    assert response.status_code == 200
                    return {
                        tool["name"]: tool["inputSchema"]
                        for tool in response.json()["result"]["tools"]
                    }

                async def descriptors(principal="alice-key", instance="A@a"):
                    body = dict(_body("resources/read", {"uri": "mcpforunity://custom-tools"}))
                    body["params"]["_meta"]["unity_instance"] = instance
                    response = await http.post(
                        "/mcp",
                        headers={
                            **_headers("resources/read", name="mcpforunity://custom-tools"),
                            "x-api-key": principal,
                        },
                        json=body,
                    )
                    assert response.status_code == 200, response.text
                    result = response.json()
                    assert "error" not in result, result
                    resource = json.loads(result["result"]["contents"][0]["text"])
                    assert resource["success"] is True, resource
                    return {tool["name"]: tool for tool in resource["data"]["tools"]}

                async def settled_counts(expected):
                    await anyio.wait_all_tasks_blocked()
                    assert [change_count(stream) for stream in streams] == expected
                    for stream in streams:
                        for message in stream.messages():
                            assert (
                                message["params"]["_meta"]["io.modelcontextprotocol/subscriptionId"]
                                == stream.request_id
                            )

                first = [
                    {
                        "name": "tenant_live",
                        "parameters": [{"name": "old_value", "type": "integer"}],
                    },
                    {"name": "removed_tool"},
                    {"name": "manage_scene"},
                    {"name": "read_console"},
                ]
                # A real commit while the initial ack send is suspended remains buffered behind that ack.
                await alice.catalog(first)
                await settled_counts([0, 0, 0])
                assert (await descriptors())["tenant_live"]["parameters"][0]["type"] == "integer"
                available = await listing()
                assert {"manage_scene", "read_console"} <= available.keys()
                assert "action" in available["manage_scene"]["properties"]
                assert (
                    "tenant_live" not in available
                )  # Hosted custom schemas use the scoped resource.
                streams[0].resume.set()
                await wait(streams[0].changed)
                assert (
                    streams[0].messages()[0]["method"] == "notifications/subscriptions/acknowledged"
                )
                await settled_counts([1, 0, 0])

                # No-op and rejected/failed preparation must not emit or replace the published generation.
                await alice.catalog(first)
                await alice.catalog([{"name": "duplicate"}, {"name": "duplicate"}])
                session = await PluginHub._registry.get_session(alice.session_id)
                tools, revision = session.tools, session.tools_revision
                failure = Mock(side_effect=RuntimeError("composition preparation failure"))
                with monkeypatch.context() as patch:
                    patch.setattr(CustomToolService._instance, "_prepare_global_tool", failure)
                    await alice.catalog([{"name": "failed_tool"}])
                failure.assert_called_once()
                current = await PluginHub._registry.get_session(alice.session_id)
                assert current.tools is tools and current.tools_revision == revision
                await settled_counts([1, 0, 0])
                assert (await descriptors())["tenant_live"]["parameters"][0]["name"] == "old_value"

                # Change, partial removal, explicit empty, reconnect reset and new-generation add each invalidate once.
                changed = [
                    {
                        "name": "tenant_live",
                        "parameters": [{"name": "new_value", "type": "string"}],
                    },
                    {"name": "removed_tool"},
                    {"name": "manage_scene"},
                    {"name": "read_console"},
                ]
                for rows, expected in ((changed, 2), ([changed[0], changed[2]], 3), ([], 4)):
                    streams[0].changed = anyio.Event()
                    await alice.catalog(rows)
                    await wait(streams[0].changed)
                    await settled_counts([expected, 0, 0])
                    found = await descriptors()
                    assert set(found) == {row["name"] for row in rows}
                    inventory = await listing()
                    assert {"manage_scene", "read_console"} & inventory.keys() == {
                        row["name"]
                        for row in rows
                        if row["name"] in {"manage_scene", "read_console"}
                    }
                    if rows:
                        assert found["tenant_live"]["parameters"][0]["name"] == "new_value"
                        assert found["tenant_live"]["parameters"][0]["type"] == "string"

                streams[0].changed = anyio.Event()
                replacement = PluginStream(app, "alice-key", "a", "A")
                await replacement.connect(group)
                await wait(alice.done)
                assert replacement.session_id != alice.session_id
                await wait(streams[0].changed)
                await settled_counts([5, 0, 0])
                assert await descriptors() == {}
                streams[0].changed = anyio.Event()
                await replacement.catalog(
                    [
                        {
                            "name": "tenant_live",
                            "parameters": [{"name": "reconnected", "type": "boolean"}],
                        }
                    ]
                )
                await wait(streams[0].changed)
                await settled_counts([6, 0, 0])
                assert (await descriptors())["tenant_live"]["parameters"][0][
                    "name"
                ] == "reconnected"
                assert (await descriptors("bob-key"))["tenant_live"]["parameters"][0][
                    "name"
                ] == "bob_value"
                assert "other_only" in await descriptors(instance="Other@other")
                assert "foreign_only" in await descriptors("bob-key", "Foreign@foreign")

                # The same-hash tenant and the second Alice project commit through the same real seam.
                streams[2].changed = anyio.Event()
                await bob.catalog(
                    [
                        {
                            "name": "tenant_live",
                            "parameters": [{"name": "bob_replaced", "type": "boolean"}],
                        },
                        {"name": "read_console"},
                    ]
                )
                await wait(streams[2].changed)
                await settled_counts([6, 0, 1])
                assert (await descriptors("bob-key"))["tenant_live"]["parameters"][0][
                    "name"
                ] == "bob_replaced"
                assert (await descriptors())["tenant_live"]["parameters"][0][
                    "name"
                ] == "reconnected"
                streams[1].changed = anyio.Event()
                await other.catalog([{"name": "other_changed"}, {"name": "run_tests"}])
                await wait(streams[1].changed)
                await settled_counts([6, 1, 1])
                assert "other_changed" in await descriptors(instance="Other@other")
                assert "other_changed" not in await descriptors()

                streams[0].changed = anyio.Event()
                await replacement.close()
                await wait(streams[0].changed)
                await settled_counts([7, 1, 1])
                # A client's current instances omit the withdrawn project; the other tenant stays readable.
                instances = await http.post(
                    "/mcp",
                    headers={
                        **_headers("resources/read", name="mcpforunity://instances"),
                        "x-api-key": "alice-key",
                    },
                    json=_body("resources/read", {"uri": "mcpforunity://instances"}),
                )
                assert instances.status_code == 200
                visible = json.loads(instances.json()["result"]["contents"][0]["text"])
                assert {instance["hash"] for instance in visible["instances"]} == {"other"}
                assert (await descriptors("bob-key"))["tenant_live"]["parameters"][0][
                    "name"
                ] == "bob_replaced"

            for stream in streams:
                stream.disconnect.set()
                await wait(stream.done)
            for plugin in (other, bob, foreign):
                await plugin.close()
        assert not runtime._leases
        assert not runtime.bus.scopes


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("principal", "instance", "status"),
    [
        ("invalid-key", "A@a", 401),
        ("alice-key", None, 400),
        ("alice-key", "missing", 400),
        ("alice-key", "6401", 400),
    ],
)
async def test_invalid_subscription_scope_never_admits(hosted_catalog, principal, instance, status):
    # Given: an unauthenticated or unauthorized/malformed selector.
    app, runtime = hosted_catalog
    async with app.router.lifespan_context(app):
        await register_scopes()
        stream = ListenStream(app, "denied", principal=principal, instance=instance)
        # When: the real auth/SDK/selector boundary evaluates the request.
        await stream.run()
        # Then: it fails before acknowledgment or admission.
        assert stream.frames[0]["status"] == status
        assert not stream.acknowledged.is_set()
        assert not runtime._leases
        assert not runtime.bus.scopes


@pytest.mark.asyncio
async def test_slow_network_does_not_release_admission_or_block_healthy_stream(
    hosted_catalog, monkeypatch
):
    # Given: a one-slot-per-project policy and a stalled response writer in project A.
    app, runtime = hosted_catalog
    runtime.admission = SubscriptionAdmission(total=2, principal=2, project=1)
    dispatched = anyio.Event()
    publish = runtime.bus.publish

    async def observed_publish(event):
        await publish(event)
        dispatched.set()

    monkeypatch.setattr(runtime.bus, "publish", observed_publish)
    async with app.router.lifespan_context(app):
        await register_scopes()
        slow = ListenStream(app, "slow", stall_changes=True)
        healthy = ListenStream(app, "healthy", instance="Other@other")
        async with anyio.create_task_group() as group:
            group.start_soon(slow.run)
            group.start_soon(healthy.run)
            await wait(slow.acknowledged)
            await wait(healthy.acknowledged)
            # When: enough distinct commits overflow the SDK backlog behind the stalled send.
            for revision in range(80):
                dispatched = anyio.Event()
                runtime.publish(
                    user_id="alice",
                    project_hash="a",
                    session_id="alice-a",
                    catalog_revision=revision,
                )
                await wait(dispatched)
            assert slow.changed.is_set()
            assert len(runtime.handler._streams) == 1
            denied = ListenStream(app, "excess")
            await denied.run()
            assert (
                denied.messages()[-1]["error"]["message"] == "Catalog subscription capacity reached"
            )
            assert not denied.acknowledged.is_set()
            assert runtime.admission.active_count == 2
            assert len(runtime._dirty) <= 2
            runtime.publish(
                user_id="alice", project_hash="other", session_id="alice-other", catalog_revision=1
            )
            await wait(healthy.changed)
            # Then: only actual stream exit releases the charged slot, including shutdown cancellation.
            await runtime.close(grace_seconds=0)
            slow.disconnect.set()
            healthy.disconnect.set()
            await wait(slow.done)
            await wait(healthy.done)
        assert runtime.admission.active_count == 0


@pytest.mark.asyncio
async def test_coalescing_and_session_revision_identity(hosted_catalog):
    # Given: an acknowledged project stream and a batch of duplicate committed signals.
    app, runtime = hosted_catalog
    async with app.router.lifespan_context(app):
        await register_scopes()
        stream = ListenStream(app, "coalesced")
        async with anyio.create_task_group() as group:
            group.start_soon(stream.run)
            await wait(stream.acknowledged)
            for revision in range(1000):
                runtime.publish(
                    user_id="alice", project_hash="a", session_id="first", catalog_revision=revision
                )
            assert len(runtime._dirty) == 1
            await wait(stream.changed)
            await anyio.wait_all_tasks_blocked()
            assert len(stream.messages()) == 2
            # When: the same generation repeats or a replacement session restarts its revision.
            runtime.publish(
                user_id="alice", project_hash="a", session_id="first", catalog_revision=999
            )
            runtime.publish()  # Missing hosted scope must never broadcast.
            await anyio.wait_all_tasks_blocked()
            assert len(stream.messages()) == 2
            stream.changed = anyio.Event()
            runtime.publish(
                user_id="alice", project_hash="a", session_id="replacement", catalog_revision=0
            )
            await wait(stream.changed)
            # Then: the new session invalidates even with a smaller revision.
            assert len(stream.messages()) == 3
            stream.disconnect.set()
            await wait(stream.done)
        assert not runtime._stamps
        assert not runtime._dirty


@pytest.mark.asyncio
async def test_unsupported_filters_and_noop_failed_commits_never_emit(hosted_catalog):
    app, runtime = hosted_catalog
    async with app.router.lifespan_context(app):
        await register_scopes()
        supported = ListenStream(app, "supported")
        unsupported = ListenStream(app, "unsupported", notifications={"resourcesListChanged": True})
        async with anyio.create_task_group() as group:
            group.start_soon(supported.run)
            group.start_soon(unsupported.run)
            await wait(supported.acknowledged)
            await wait(unsupported.acknowledged)
            assert unsupported.messages()[0]["params"]["notifications"] == {}
            scope = {
                "user_id": "alice",
                "project_hash": "a",
                "session_id": "alice-a",
                "catalog_revision": 1,
            }
            for outcome in (None, {"changed": False}):
                await PluginHub._notify_committed_catalog(outcome, scope)
            await anyio.wait_all_tasks_blocked()
            assert not supported.changed.is_set()
            await PluginHub._notify_committed_catalog({"changed": True}, scope)
            await wait(supported.changed)
            await anyio.wait_all_tasks_blocked()
            assert not unsupported.changed.is_set()
            for stream in (supported, unsupported):
                stream.disconnect.set()
                await wait(stream.done)
        assert not runtime._leases


@pytest.mark.asyncio
@pytest.mark.parametrize("hosted_catalog", [False], indirect=True)
async def test_local_global_winner_invalidates_every_project_watch(hosted_catalog):
    # Given: authenticated local-global and explicit-project subscribers.
    app, runtime = hosted_catalog
    async with app.router.lifespan_context(app):
        registry = PluginHub._registry
        await registry.register("local-a", "A", "a", "fixture")
        await registry.register("local-b", "B", "b", "fixture")
        streams = [
            ListenStream(app, "all", instance=None, local_token=config.local_auth_token),
            ListenStream(app, "a", local_token=config.local_auth_token),
            ListenStream(app, "b", instance="B@b", local_token=config.local_auth_token),
        ]
        async with anyio.create_task_group() as group:
            for stream in streams:
                group.start_soon(stream.run)
            for stream in streams:
                await wait(stream.acknowledged)
            # A source-only change affects its project and the local global watch.
            await PluginHub._notify_committed_catalog(
                {"changed": True, "global_component_changed": False},
                {
                    "user_id": None,
                    "project_hash": "a",
                    "session_id": "local-a",
                    "catalog_revision": 1,
                },
            )
            await wait(streams[0].changed)
            await wait(streams[1].changed)
            await anyio.wait_all_tasks_blocked()
            assert not streams[2].changed.is_set()
            for stream in streams:
                stream.changed = anyio.Event()
            # When: the SDK's global winner changes, every local project sees the invalidation.
            await PluginHub._notify_committed_catalog(
                {"changed": True, "global_component_changed": True},
                {
                    "user_id": None,
                    "project_hash": "a",
                    "session_id": "local-a",
                    "catalog_revision": 2,
                },
            )
            for stream in streams:
                await wait(stream.changed)
                stream.disconnect.set()
                await wait(stream.done)
        assert not runtime._leases


@pytest.mark.asyncio
async def test_noncooperative_transport_keeps_charge_across_runtime_retirement(hosted_catalog):
    # Given: a send deliberately shields cancellation, modelling a stuck in-process transport.
    app, runtime = hosted_catalog
    pool = SubscriptionAdmission(total=1, principal=1, project=1)
    runtime.admission = pool
    async with app.router.lifespan_context(app):
        await register_scopes()
        stream = ListenStream(app, "retired", stall_changes=True, shield_send=True)
        async with anyio.create_task_group() as group:
            group.start_soon(stream.run)
            await wait(stream.acknowledged)
            runtime.publish(user_id="alice", project_hash="a")
            await wait(stream.changed)
            # When: graceful drain expires, cancellation cannot forcibly exit the shielded send.
            await runtime.close(grace_seconds=0)
            assert not stream.done.is_set()
            assert pool.active_count == 1
            from mcp.shared.exceptions import MCPError

            with pytest.raises(MCPError):
                pool.acquire(WatchScope("bob", "new-runtime"))
            # Then: only actual transport teardown releases the process-wide budget.
            stream.resume.set()
            await wait(stream.done)
        assert pool.active_count == 0


@pytest.mark.asyncio
async def test_graceful_close_finishes_sdk_result_before_releasing_admission(hosted_catalog):
    app, runtime = hosted_catalog
    async with app.router.lifespan_context(app):
        await register_scopes()
        stream = ListenStream(app, "graceful")
        async with anyio.create_task_group() as group:
            group.start_soon(stream.run)
            await wait(stream.acknowledged)
            await runtime.close(grace_seconds=1)
            await wait(stream.done)
        final = stream.messages()[-1]
        assert final["id"] == "graceful"
        assert final["result"]["resultType"] == "complete"
        assert final["result"]["_meta"]["io.modelcontextprotocol/subscriptionId"] == "graceful"
        assert not runtime._leases
        assert not runtime.bus.scopes


@pytest.mark.asyncio
async def test_disconnect_during_ack_send_releases_listener_and_admission(hosted_catalog):
    app, runtime = hosted_catalog
    async with app.router.lifespan_context(app):
        await register_scopes()
        stream = ListenStream(app, "cancel-ack", stall_ack=True)
        async with anyio.create_task_group() as group:
            group.start_soon(stream.run)
            await wait(stream.acknowledged)
            assert runtime._leases
            stream.disconnect.set()
            await wait(stream.done)
        assert not runtime._leases
        assert not runtime.bus.scopes


def test_admission_limits_are_global_principal_and_project_scoped():
    # Given: an admission pool shared across current and retired runtimes.
    pool = SubscriptionAdmission(total=3, principal=2, project=1)
    a = pool.acquire(WatchScope("alice", "a"))
    b = pool.acquire(WatchScope("alice", "b"))
    from mcp.shared.exceptions import MCPError

    # When: attempts exceed each independent budget.
    with pytest.raises(MCPError):
        pool.acquire(WatchScope("alice", "c"))
    with pytest.raises(MCPError):
        pool.acquire(WatchScope("alice", "a"))
    c = pool.acquire(WatchScope("bob", "a"))
    with pytest.raises(MCPError):
        pool.acquire(WatchScope("carol", "a"))
    # Then: releasing actual owners restores exactly that capacity.
    for token in (a, b, c):
        pool.release(token)
    assert pool.active_count == 0


def resource_notifications(stream):
    return [
        item
        for item in stream.messages()
        if item.get("method") == "notifications/resources/updated"
    ]


def resource_filter():
    return {"resourceSubscriptions": [EDITOR_STATE_URI]}


def publish_state(runtime, *, project="a", user="alice", session="session", revision=1):
    runtime.publish_editor_state(
        user_id=user,
        project_hash=project,
        session_id=session,
        generation=session,
        revision=revision,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("hosted_catalog", [(True, True)], indirect=True)
async def test_editor_state_wire_events_are_scoped_committed_and_generation_owned(
    hosted_catalog,
):
    # Given: actual authenticated plugin sockets for two Alice editors and Bob's same project.
    app, runtime = hosted_catalog
    peers = [
        PluginStream(app, "alice-key", "a", "A", capabilities=("editor_state_v1",)),
        PluginStream(app, "alice-key", "other", "Other", capabilities=("editor_state_v1",)),
        PluginStream(app, "bob-key", "a", "A", capabilities=("editor_state_v1",)),
    ]
    async with app.router.lifespan_context(app):
        async with anyio.create_task_group() as group:
            for peer in peers:
                await peer.connect(group)
            streams = [
                ListenStream(
                    app,
                    "state-a",
                    notifications={
                        "resourceSubscriptions": [
                            "fixture://unsupported",
                            EDITOR_STATE_URI,
                            EDITOR_STATE_URI,
                        ],
                        "resourcesListChanged": True,
                        "promptsListChanged": True,
                    },
                ),
                ListenStream(
                    app, "state-other", instance="Other@other", notifications=resource_filter()
                ),
                ListenStream(
                    app, "state-bob", principal="bob-key", notifications=resource_filter()
                ),
                ListenStream(app, "tools-only", notifications={"toolsListChanged": True}),
                ListenStream(
                    app,
                    "unsupported",
                    notifications={"resourceSubscriptions": [EDITOR_STATE_URI + "/"]},
                ),
            ]
            for stream in streams:
                group.start_soon(stream.run)
                await wait(stream.acknowledged)
            ack = streams[0].messages()[0]
            assert ack["params"]["notifications"] == resource_filter()
            assert streams[-1].messages()[0]["params"]["notifications"] == {}
            assert ack["params"]["_meta"]["io.modelcontextprotocol/subscriptionId"] == "state-a"
            payload = editor_state_message(observed=int(time.time() * 1000))

            # When: Alice A commits a complete snapshot through the actual plugin handler.
            await peers[0].send_json(payload)
            await wait(streams[0].changed)
            await anyio.wait_all_tasks_blocked()
            event = resource_notifications(streams[0])[0]
            assert event["params"] == {
                "uri": EDITOR_STATE_URI,
                "_meta": {"io.modelcontextprotocol/subscriptionId": "state-a"},
            }
            assert all(not stream.changed.is_set() for stream in streams[1:])
            first_count = len(resource_notifications(streams[0]))

            # Then: malformed, duplicate, unchanged heartbeat and out-of-order frames do not publish.
            for frame in (
                {"type": "editor_state"},
                payload,
                editor_state_message(observed=payload["observed_at_unix_ms"] + 1),
                editor_state_message(sequence=0, observed=payload["observed_at_unix_ms"]),
                {**payload, "session_id": peers[1].session_id},
            ):
                await peers[0].send_json(frame)
            await anyio.wait_all_tasks_blocked()
            assert len(resource_notifications(streams[0])) == first_count
            socket = PluginHub._connections[peers[0].session_id]

            # A rejected epoch change invalidates the old cache and still notifies its own scope.
            streams[0].changed = anyio.Event()
            await peers[0].send_json(
                editor_state_message(
                    epoch="replacement-domain", observed=payload["observed_at_unix_ms"] + 2
                )
            )
            await wait(streams[0].changed)
            assert PluginHub._editor_states.get(peers[0].session_id, "alice") is None
            streams[0].changed = anyio.Event()
            replacement = PluginStream(
                app, "alice-key", "a", "A", capabilities=("editor_state_v1",)
            )
            await replacement.connect(group)
            await wait(streams[0].changed)
            await anyio.wait_all_tasks_blocked()
            before = len(resource_notifications(streams[0]))
            await PluginHub._handle_editor_state(PluginHub.__new__(PluginHub), socket, payload)
            await anyio.wait_all_tasks_blocked()
            assert len(resource_notifications(streams[0])) == before
            streams[0].changed = anyio.Event()
            await replacement.send_json(
                editor_state_message(
                    sequence=0, epoch="new-session", observed=int(time.time() * 1000)
                )
            )
            await wait(streams[0].changed)
            assert PluginHub._editor_states.get(replacement.session_id, "alice")["sequence"] == 0
            assert all(not resource_notifications(stream) for stream in streams[1:])
            streams[0].changed = anyio.Event()
            await replacement.close()
            await wait(streams[0].changed)
            for peer in peers:
                await peer.close()
            for stream in streams:
                stream.disconnect.set()
                await wait(stream.done)
        assert not runtime._resource_dirty
        assert not runtime._resource_stamps
        assert not runtime._leases


@pytest.mark.asyncio
@pytest.mark.parametrize("hosted_catalog", [(True, True)], indirect=True)
async def test_foreign_socket_principal_closes_before_editor_state_commit(
    hosted_catalog, monkeypatch
):
    # Given: three authenticated peers with committed state and scoped subscribers.
    app, runtime = hosted_catalog
    peers = [
        PluginStream(app, "alice-key", "a", "A", capabilities=("editor_state_v1",)),
        PluginStream(app, "alice-key", "other", "Other", capabilities=("editor_state_v1",)),
        PluginStream(app, "bob-key", "a", "A", capabilities=("editor_state_v1",)),
    ]
    streams = [
        ListenStream(app, "foreign-a", notifications=resource_filter()),
        ListenStream(app, "foreign-other", instance="Other@other", notifications=resource_filter()),
        ListenStream(app, "foreign-bob", principal="bob-key", notifications=resource_filter()),
    ]
    async with app.router.lifespan_context(app):
        async with anyio.create_task_group() as group:
            for peer in peers:
                await peer.connect(group)
            for stream in streams:
                group.start_soon(stream.run)
                await wait(stream.acknowledged)
            for peer, stream in zip(peers, streams):
                await peer.send_json(editor_state_message(observed=int(time.time() * 1000)))
                await wait(stream.changed)
            await anyio.wait_all_tasks_blocked()
            counts = [len(resource_notifications(stream)) for stream in streams]
            for stream in streams:
                stream.changed = anyio.Event()
            healthy = {
                peer.session_id: PluginHub._connections[peer.session_id] for peer in peers[1:]
            }
            healthy_states = {
                peer.session_id: PluginHub._editor_states.get(peer.session_id, user)
                for peer, user in zip(peers[1:], ("alice", "bob"))
            }
            socket = PluginHub._connections[peers[0].session_id]
            accept = Mock(wraps=PluginHub._editor_states.accept)
            forged = editor_state_message(sequence=2, observed=int(time.time() * 1000))
            # When: a live socket's mutable principal disagrees with its immutable lease.
            with monkeypatch.context() as patch:
                patch.setattr(socket.state, "user_id", "bob")
                patch.setattr(PluginHub._editor_states, "accept", accept)
                # Rejection ends the peer, so a next-receive processing barrier is invalid.
                await peers[0].input.send(
                    ({"type": "websocket.receive", "text": json.dumps(forged)}, None)
                )
                await wait(peers[0].done)
                accept.assert_not_called()
            # Then: policy close precedes any store mutation and invalidates only its own scope.
            assert peers[0].close_codes == [4403]
            assert peers[0].session_id not in PluginHub._connections
            assert PluginHub._editor_states.get(peers[0].session_id, "alice") is None
            await wait(streams[0].changed)
            await anyio.wait_all_tasks_blocked()
            assert len(resource_notifications(streams[0])) > counts[0]
            assert [len(resource_notifications(stream)) for stream in streams[1:]] == counts[1:]
            for peer, user in zip(peers[1:], ("alice", "bob")):
                assert not peer.done.is_set()
                assert PluginHub._connections[peer.session_id] is healthy[peer.session_id]
                assert (
                    PluginHub._editor_states.get(peer.session_id, user)
                    == healthy_states[peer.session_id]
                )
            for peer, stream in zip(peers[1:], streams[1:]):
                await peer.send_json(
                    editor_state_message(sequence=2, observed=int(time.time() * 1000))
                )
                await wait(stream.changed)
            for peer in peers:
                await peer.close()
            for stream in streams:
                stream.disconnect.set()
                await wait(stream.done)
        assert not runtime._resource_dirty and not runtime._resource_stamps and not runtime._leases


@pytest.mark.asyncio
@pytest.mark.parametrize("hosted_catalog", [False], indirect=True)
async def test_local_resource_watch_requires_explicit_instance_but_tools_global_remains(
    hosted_catalog,
):
    # Given: a local server with a resolvable canonical editor.
    app, runtime = hosted_catalog
    async with app.router.lifespan_context(app):
        await PluginHub._registry.register("local-a", "A", "a", "fixture")
        token = config.local_auth_token
        denied = ListenStream(
            app,
            "resource-global",
            local_token=token,
            instance=None,
            notifications=resource_filter(),
        )
        await denied.run()
        assert not denied.acknowledged.is_set()
        assert "unity_instance" in denied.messages()[-1]["error"]["message"]
        streams = [
            ListenStream(
                app,
                "tools-global",
                local_token=token,
                instance=None,
                notifications={"toolsListChanged": True},
            ),
            ListenStream(
                app,
                "resource-explicit",
                local_token=token,
                instance="A@a",
                notifications=resource_filter(),
            ),
            ListenStream(
                app,
                "unsupported-global",
                local_token=token,
                instance=None,
                notifications={"resourceSubscriptions": ["fixture://unsupported"]},
            ),
        ]
        async with anyio.create_task_group() as group:
            for stream in streams:
                group.start_soon(stream.run)
                await wait(stream.acknowledged)
            # When: the explicit editor's state changes.
            publish_state(runtime, user=None)
            await wait(streams[1].changed)
            await anyio.wait_all_tasks_blocked()
            # Then: global tools/unsupported watches receive no resource event.
            assert streams[2].messages()[0]["params"]["notifications"] == {}
            assert not streams[0].changed.is_set()
            assert not streams[2].changed.is_set()
            for stream in streams:
                stream.disconnect.set()
                await wait(stream.done)
        assert not runtime._resource_stamps


@pytest.mark.asyncio
async def test_resource_burst_coalesces_and_generation_zero_invalidates(hosted_catalog):
    # Given: one acknowledged resource-only watch and tools-only watch in the same scope.
    app, runtime = hosted_catalog
    async with app.router.lifespan_context(app):
        await register_scopes()
        resource = ListenStream(app, "resource-burst", notifications=resource_filter())
        tools = ListenStream(app, "tools-burst", notifications={"toolsListChanged": True})
        async with anyio.create_task_group() as group:
            for stream in (resource, tools):
                group.start_soon(stream.run)
                await wait(stream.acknowledged)
            # When: a synchronous burst replaces the pending latest invalidation.
            for revision in range(1000):
                publish_state(runtime, revision=revision)
            assert len(runtime._resource_dirty) == 1
            assert len(runtime._resource_stamps) == 1
            await wait(resource.changed)
            await anyio.wait_all_tasks_blocked()
            assert len(resource_notifications(resource)) == 1
            publish_state(runtime, revision=999)
            await anyio.wait_all_tasks_blocked()
            assert len(resource_notifications(resource)) == 1
            resource.changed = anyio.Event()
            publish_state(runtime, session="replacement", revision=0)
            await wait(resource.changed)
            # Then: generation reset invalidates, and resource state is removed with its last watch.
            assert len(resource_notifications(resource)) == 2
            assert not tools.changed.is_set()
            resource.disconnect.set()
            await wait(resource.done)
            assert not runtime._resource_dirty
            assert not runtime._resource_stamps
            assert runtime.bus.scopes == {WatchScope("alice", "a")}
            tools.disconnect.set()
            await wait(tools.done)


@pytest.mark.asyncio
@pytest.mark.parametrize("shield_send", [False, True], ids=["cooperative", "retired-shielded"])
async def test_resource_slow_writer_is_bounded_and_keeps_charge_until_http_exit(
    hosted_catalog, monkeypatch, shield_send
):
    # Given: a slow Alice writer and a healthy Bob writer with separate tenant ownership.
    app, runtime = hosted_catalog
    runtime.admission = SubscriptionAdmission(total=2, principal=1, project=1)
    dispatched = anyio.Event()
    publish = runtime.bus.publish

    async def observed_publish(event):
        await publish(event)
        dispatched.set()

    monkeypatch.setattr(runtime.bus, "publish", observed_publish)
    async with app.router.lifespan_context(app):
        await register_scopes()
        slow = ListenStream(
            app,
            "resource-slow",
            notifications=resource_filter(),
            stall_changes=True,
            shield_send=shield_send,
        )
        healthy = ListenStream(
            app, "resource-healthy", principal="bob-key", notifications=resource_filter()
        )
        async with anyio.create_task_group() as group:
            for stream in (slow, healthy):
                group.start_soon(stream.run)
                await wait(stream.acknowledged)
            # When: bounded SDK buffering overflows behind the stalled HTTP writer.
            for revision in range(80):
                dispatched = anyio.Event()
                publish_state(runtime, revision=revision)
                await wait(dispatched)
            assert slow.changed.is_set()
            assert len(runtime.handler._streams) == 1
            assert runtime.admission.active_count == 2
            assert len(runtime._resource_dirty) <= 2
            assert len(runtime._resource_stamps) <= 2
            denied = ListenStream(app, "resource-excess", notifications=resource_filter())
            await denied.run()
            assert not denied.acknowledged.is_set()
            assert (
                denied.messages()[-1]["error"]["message"] == "Catalog subscription capacity reached"
            )
            publish_state(runtime, user="bob")
            await wait(healthy.changed)
            assert len(resource_notifications(healthy)) == 1
            await runtime.close(grace_seconds=0)
            # Then: retirement clears pending data while actual HTTP teardown owns admission.
            assert not runtime._resource_dirty
            assert not runtime._resource_stamps
            if shield_send:
                assert not slow.done.is_set()
                assert runtime.admission.active_count >= 1
                from mcp.shared.exceptions import MCPError

                with pytest.raises(MCPError):
                    runtime.admission.acquire(WatchScope("alice", "other"))
                slow.resume.set()
            slow.disconnect.set()
            healthy.disconnect.set()
            await wait(slow.done)
            await wait(healthy.done)
        assert runtime.admission.active_count == 0
        assert not runtime.bus.scopes


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "disconnect_during_ack", [False, True], ids=["buffer-until-ack", "disconnect-during-ack"]
)
async def test_resource_ack_boundary_buffers_events_and_cleans_disconnect(
    hosted_catalog, disconnect_during_ack
):
    # Given: a listener registered by the real SDK before its HTTP acknowledgment completes.
    app, runtime = hosted_catalog
    async with app.router.lifespan_context(app):
        await register_scopes()
        stream = ListenStream(app, "resource-ack", notifications=resource_filter(), stall_ack=True)
        async with anyio.create_task_group() as group:
            group.start_soon(stream.run)
            await wait(stream.acknowledged)
            publish_state(runtime)
            await anyio.wait_all_tasks_blocked()
            assert not stream.changed.is_set()
            assert runtime.admission.active_count >= 1
            # When: the client disconnects during acknowledgment or allows it to finish.
            if disconnect_during_ack:
                stream.disconnect.set()
            else:
                stream.resume.set()
                await wait(stream.changed)
                assert stream.messages()[0]["method"] == "notifications/subscriptions/acknowledged"
                assert resource_notifications(stream)[0]["params"]["uri"] == EDITOR_STATE_URI
                stream.disconnect.set()
            await wait(stream.done)
        # Then: listener, pending state and admission all leave with the actual HTTP handler.
        assert not runtime._resource_dirty
        assert not runtime._resource_stamps
        assert not runtime._leases
        assert not runtime.bus.scopes


@pytest.mark.asyncio
async def test_nonpush_editor_replacement_and_disconnect_invalidate_polling_resource(
    hosted_catalog,
):
    # Given: an authorized legacy editor and an explicit fixed-resource watcher.
    app, runtime = hosted_catalog
    async with app.router.lifespan_context(app):
        async with anyio.create_task_group() as group:
            first = PluginStream(app, "alice-key", "a", "A")
            await first.connect(group)
            stream = ListenStream(app, "polling-lifecycle", notifications=resource_filter())
            group.start_soon(stream.run)
            await wait(stream.acknowledged)
            old_socket = PluginHub._connections[first.session_id]
            # When: a polling-only editor reconnects, then disconnects.
            replacement = PluginStream(app, "alice-key", "a", "A")
            await replacement.connect(group)
            await wait(stream.changed)
            await anyio.wait_all_tasks_blocked()
            assert not PluginHub._editor_states._owners
            before = len(resource_notifications(stream))
            lock = PluginHub._lock
            assert lock is not None
            await PluginHub.on_disconnect(PluginHub.__new__(PluginHub), old_socket, 1000)
            assert PluginHub._lock is lock
            await anyio.wait_all_tasks_blocked()
            assert len(resource_notifications(stream)) == before
            stream.changed = anyio.Event()
            await replacement.close()
            await wait(stream.changed)
            # Then: both availability changes invalidate, without retaining an old push slot.
            assert len(resource_notifications(stream)) > before
            await first.close()
            stream.disconnect.set()
            await wait(stream.done)
        assert not runtime._resource_dirty
        assert not runtime._resource_stamps
