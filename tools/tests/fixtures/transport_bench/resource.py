"""Real HTTP resource/source-copy contract, separate from latency workloads."""

from __future__ import annotations

import importlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import anyio
from fastmcp import Context, FastMCP
from starlette.types import ASGIApp, Receive, Scope, Send

from tools.tests.fixtures.transport_bench.accounting import Accounting
from tools.tests.fixtures.transport_bench.peer import PeerState
from tools.tests.fixtures.transport_bench.workload import INSTANCE


@dataclass(slots=True)
class ResourceContract:
    """Own rendezvous and actual response-send holds for one two-reader cohort."""

    peer: PeerState
    accounting: Accounting
    directory: Path
    entered: int = 0
    held: int = 0
    held_event: anyio.Event = field(default_factory=anyio.Event)
    release: anyio.Event = field(default_factory=anyio.Event)

    def install(self, mcp: FastMCP) -> None:
        editor = importlib.import_module("services.resources.editor_state")
        project = self.directory / "owned-project"
        (project / "Assets").mkdir(parents=True)

        async def owned_project(_instance: str) -> str:
            return str(project)

        editor._local_project_root = owned_project

        async def ordinary(ctx: Context):
            await ctx.set_state("unity_instance", INSTANCE)
            self.enter()
            return (await editor.get_editor_state(ctx)).model_dump()

        async def authoritative(ctx: Context):
            await ctx.set_state("unity_instance", INSTANCE)
            self.enter()
            return (await editor.get_editor_state_authoritative(ctx)).model_dump()

        mcp.resource("mcpforunity://editor/state")(ordinary)
        mcp.resource("mcpforunity://editor/state/authoritative")(authoritative)

        @mcp.tool(name="bench_resource_arm")
        async def arm() -> dict[str, bool]:
            self.entered = self.held = 0
            self.held_event, self.release = anyio.Event(), anyio.Event()
            self.peer.resource_entered = anyio.Event()
            self.peer.resource_enabled = True
            return {"armed": True}

        @mcp.tool(name="bench_resource_held")
        async def held():
            with anyio.fail_after(8):
                await self.held_event.wait()
            return self.accounting.snapshot()

        @mcp.tool(name="bench_resource_release")
        async def release() -> dict[str, bool]:
            self.peer.resource_enabled = False
            self.release.set()
            return {"released": True}

    def enter(self) -> None:
        self.entered += 1
        if self.entered == 2:
            self.peer.resource_entered.set()


class ResourceHold:
    """Hold actual resource body sends inside product retention's await boundary."""

    def __init__(self, app: ASGIApp, contract: ResourceContract):
        self.app, self.contract = app, contract

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def hold_send(message):
            if message["type"] == "http.response.body" and self.contract.peer.resource_enabled:
                body = message.get("body", b"")
                if body and body.startswith(b"{"):
                    payload = json.loads(body)
                    if isinstance(payload.get("result"), dict) and "contents" in payload["result"]:
                        self.contract.held += 1
                        self.contract.accounting.snapshot()
                        if self.contract.held == 2:
                            self.contract.held_event.set()
                        await self.contract.release.wait()
            await send(message)

        await self.app(scope, receive, hold_send)
