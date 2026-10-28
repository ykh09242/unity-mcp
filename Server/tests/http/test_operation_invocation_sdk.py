"""Real SDK registration/invocation with explicitly injected auth/target/peer boundaries.

These are in-process FastMCP Client scenarios, not HTTP authentication or native
transport evidence. Real built-in callbacks, central registration, SDK publication,
policy identity checks and the fair scheduler remain in the path under test.
"""

import asyncio
from collections import defaultdict
import importlib
from uuid import uuid4

from fastmcp import Client, Context, FastMCP
from fastmcp.server.dependencies import get_context
import pytest
import pytest_asyncio

from core.config import config
from services.operation_permissions import OperationPermissionError
from services.registry import get_registered_tools
from services.registry import operation_invocation as invocation
from services.registry.operation_context import active_operation, scheduler
from services.registry.operation_scheduler import OperationScheduler, OperationScope
from services.registry.operation_target import OperationTarget
import services.tools as tools_package
from tests.timing import await_completion, await_signal, watchdog_timeout


class InvocationBoundary:
    """Owned held signals and external boundaries; no replacement scheduler/policy."""

    def __init__(self):
        self.entered = defaultdict(asyncio.Event)
        self.releases = defaultdict(asyncio.Event)
        self.auth_seen = defaultdict(asyncio.Event)
        self.auth_calls = defaultdict(int)
        self.holds = set()
        self.denied = set()
        self.effects = []
        self.targets = []
        self.tasks = []
        self.queued = asyncio.Event()
        self.principal = "sdk-" + uuid4().hex
        self.definitions = []

    @staticmethod
    def metadata(ctx):
        return ctx.request_context.meta

    async def authorize(self, name, params, *, ctx=None):
        label = self.metadata(ctx)["qa_label"]
        self.auth_calls[label] += 1
        self.auth_seen[label].set()
        if label in self.denied:
            raise OperationPermissionError()

    async def selected_instance(self, ctx):
        return self.metadata(ctx).get("qa_instance", "Owned@aa11bb22")

    async def capture_target(self, instance, *, user_id=None):
        self.targets.append(instance)
        return OperationTarget(
            OperationScope(self.principal, instance.rsplit("@", 1)[-1], "owned-generation"),
            lambda: True,
            True,
        )

    async def effect(self, label, phase):
        owner = active_operation()
        assert owner is not None, "Central admission must precede the actual callback/preflight."
        self.effects.append((label, phase, owner.request.mode, owner.request.scope.instance))
        self.entered[label].set()
        if label in self.holds:
            await self.releases[label].wait()

    async def preflight(self, ctx, **kwargs):
        await self.effect(self.metadata(ctx)["qa_label"], "preflight")
        return None

    async def peer(self, sender, instance, command, params, **kwargs):
        label = self.metadata(get_context())["qa_label"]
        if command == "manage_script":
            await self.effect(label, "peer_read")
            return {"success": True, "data": {"sha256": "a" * 64, "lengthBytes": 7}}
        assert command == "find_gameobjects"
        return {"success": True, "data": {"gameObjects": [101]}}

    def call(self, client, name, label, *, instance="Owned@aa11bb22"):
        arguments = {"uri": "Assets/Owned.cs"} if name == "get_sha" else {"search_term": label}
        task = asyncio.create_task(
            client.call_tool(
                name,
                arguments,
                raise_on_error=False,
                meta={"qa_label": label, "qa_instance": instance},
            )
        )
        self.tasks.append(task)
        return task

    async def finish(self):
        for event in self.releases.values():
            event.set()
        if self.tasks:
            settled = asyncio.gather(*self.tasks, return_exceptions=True)
            await await_completion(settled, "owned SDK calls settle", timeout=watchdog_timeout(10))


@pytest_asyncio.fixture
async def sdk_boundary(monkeypatch):
    """Inject only external authority/routing/peer effects around real SDK publication."""
    search = importlib.import_module("services.tools.find_gameobjects")
    scripts = importlib.import_module("services.tools.manage_script")
    state = InvocationBoundary()
    state.definitions = [
        item for item in get_registered_tools() if item["name"] in {"find_gameobjects", "get_sha"}
    ]
    assert {item["name"] for item in state.definitions} == {"find_gameobjects", "get_sha"}
    monkeypatch.setenv("UNITY_MCP_DISABLE_TELEMETRY", "1")
    monkeypatch.setenv("UNITY_MCP_TOOL_PROFILE", "full")
    monkeypatch.setattr(config, "transport_mode", "stdio")
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(tools_package, "discover_modules", lambda *args, **kwargs: iter(()))
    monkeypatch.setattr(tools_package, "get_registered_tools", lambda: state.definitions)
    monkeypatch.setattr(tools_package, "get_unity_instance_from_context", state.selected_instance)
    monkeypatch.setattr(invocation, "capture_target", state.capture_target)
    monkeypatch.setattr(invocation, "authorize_remote_operation", state.authorize)
    for module in (search, scripts):
        monkeypatch.setattr(module, "get_unity_instance_from_context", state.selected_instance)
        monkeypatch.setattr(module, "send_with_unity_instance", state.peer)
    monkeypatch.setattr(search, "preflight", state.preflight)
    original_wake = OperationScheduler._wake

    def observed_wake(owner, key, instance):
        original_wake(owner, key, instance)
        if instance.waiting:
            state.queued.set()

    # Observation only: actual tickets, ordering, grants and cleanup stay production code.
    monkeypatch.setattr(OperationScheduler, "_wake", observed_wake)
    yield state
    await state.finish()
    assert scheduler().snapshot() == {
        "instances": 0,
        "active": 0,
        "waiting": 0,
        "retained_bytes": 0,
    }


def registered_server():
    server = FastMCP("operation-invocation-sdk")
    tools_package.register_all_tools(server, project_scoped_tools=False)
    return server


async def completed(task, phase):
    return await await_completion(task, phase, timeout=watchdog_timeout(10))


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_central_sdk_registration_fences_the_entire_second_preflight(sdk_boundary, mode):
    # Given: real published built-in callbacks share one negotiated Editor target.
    state = sdk_boundary
    state.holds.add("first")
    server = registered_server()
    async with Client(server, mode=mode) as first, Client(server, mode=mode) as second:
        assert {tool.name for tool in await first.list_tools()} == {"find_gameobjects", "get_sha"}
        try:
            first_call = state.call(first, "find_gameobjects", "first")
            await await_signal(
                state.entered["first"], "first actual preflight", timeout=watchdog_timeout(10)
            )
            # When
            second_call = state.call(second, "find_gameobjects", "second")
            await await_signal(
                state.queued, "second invocation queued", timeout=watchdog_timeout(10)
            )
            # Then: the second callback cannot begin even its preflight while the first owns admission.
            assert state.effects == [("first", "preflight", "exclusive", "aa11bb22")]
            assert not state.entered["second"].is_set()
            state.releases["first"].set()
            assert not (await completed(first_call, "first SDK invocation")).is_error
            assert not (await completed(second_call, "second SDK invocation")).is_error
            assert [row[:2] for row in state.effects] == [
                ("first", "preflight"),
                ("second", "preflight"),
            ]
        finally:
            await state.finish()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_actual_sdk_clients_for_different_instances_enter_independently(sdk_boundary, mode):
    # Given
    state = sdk_boundary
    state.holds.update({"first", "independent"})
    server = registered_server()
    async with Client(server, mode=mode) as first, Client(server, mode=mode) as second:
        try:
            state.call(first, "find_gameobjects", "first")
            await await_signal(
                state.entered["first"], "first instance preflight", timeout=watchdog_timeout(10)
            )
            # When
            state.call(second, "find_gameobjects", "independent", instance="Other@cc33dd44")
            await await_signal(
                state.entered["independent"],
                "independent instance preflight",
                timeout=watchdog_timeout(10),
            )
            # Then
            assert scheduler().snapshot()["active"] == 2
            assert {row[3] for row in state.effects} == {"aa11bb22", "cc33dd44"}
        finally:
            await state.finish()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_denied_sdk_writer_never_installs_a_fence_ahead_of_readers(sdk_boundary, mode):
    # Given: an actual reviewed get_sha callback remains held in its peer read.
    state = sdk_boundary
    state.holds.add("held_read")
    state.denied.add("denied_writer")
    server = registered_server()
    async with Client(server, mode=mode) as first, Client(server, mode=mode) as second:
        try:
            state.call(first, "get_sha", "held_read")
            await await_signal(
                state.entered["held_read"], "held reviewed SDK reader", timeout=watchdog_timeout(10)
            )
            captures_before = len(state.targets)
            # When
            denied = await completed(
                state.call(second, "find_gameobjects", "denied_writer"), "cheap SDK refusal"
            )
            reader = await completed(
                state.call(second, "get_sha", "reader"), "compatible SDK reader"
            )
            # Then
            assert denied.is_error
            assert not reader.is_error
            assert state.auth_calls["denied_writer"] == 1
            assert (
                len(state.targets) == captures_before + 1
            )  # Only the admitted reader captured a target.
            assert not state.entered["denied_writer"].is_set()
            assert scheduler().snapshot()["waiting"] == 0
            assert state.effects[-1][2] == "shared_read"
        finally:
            await state.finish()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
@pytest.mark.parametrize("change", ["revoke", "disable"])
async def test_queued_sdk_callback_rechecks_authority_and_enabled_state(sdk_boundary, mode, change):
    # Given
    state = sdk_boundary
    state.holds.add("first")
    server = registered_server()
    async with Client(server, mode=mode) as first, Client(server, mode=mode) as second:
        try:
            state.call(first, "find_gameobjects", "first")
            await await_signal(
                state.entered["first"], "first SDK invocation", timeout=watchdog_timeout(10)
            )
            queued = state.call(second, "find_gameobjects", "queued")
            await await_signal(
                state.queued,
                "SDK call queued before authority change",
                timeout=watchdog_timeout(10),
            )
            assert state.auth_calls["queued"] == 2
            # When
            if change == "revoke":
                state.denied.add("queued")
            else:
                server.disable(names={"find_gameobjects"}, components={"tool"})
            state.releases["first"].set()
            refused = await completed(queued, "queued SDK revalidation")
            # Then
            assert not state.entered["queued"].is_set()
            assert state.auth_calls["queued"] == 3
            if change == "revoke":
                assert refused.is_error
            else:
                assert refused.structured_content["error"] == "operation_target_changed"
        finally:
            await state.finish()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_registered_sdk_override_cannot_spoof_builtin_read_identity(sdk_boundary, mode):
    # Given: the published name and annotations still advertise get_sha/readOnly.
    state = sdk_boundary
    state.holds.add("first")

    async def override(ctx: Context, uri: str) -> dict:
        await state.effect(state.metadata(ctx)["qa_label"], "override_callback")
        return {"success": True, "data": {"sha256": "b" * 64, "lengthBytes": 3}}

    state.definitions = [
        {**item, "func": override} if item["name"] == "get_sha" else item
        for item in state.definitions
    ]
    server = registered_server()
    async with Client(server, mode=mode) as first, Client(server, mode=mode) as second:
        tool = next(tool for tool in await first.list_tools() if tool.name == "get_sha")
        assert tool.annotations.read_only_hint is True
        try:
            state.call(first, "get_sha", "first")
            await await_signal(
                state.entered["first"], "first registered override", timeout=watchdog_timeout(10)
            )
            # When
            second_call = state.call(second, "get_sha", "second")
            await await_signal(state.queued, "second override queued", timeout=watchdog_timeout(10))
            # Then
            assert state.effects == [("first", "override_callback", "exclusive", "aa11bb22")]
            state.releases["first"].set()
            assert not (await completed(second_call, "second registered override")).is_error
            assert {row[2] for row in state.effects} == {"exclusive"}
        finally:
            await state.finish()
