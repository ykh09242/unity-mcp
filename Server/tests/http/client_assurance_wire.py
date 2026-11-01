"""Wire-only client and Unity peer helpers shared by independent assurance groups."""

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
import json
from threading import Event, Thread

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from core.local_auth import LOCAL_AUTH_HEADER

MODERN_VERSION = "2026-07-28"


@asynccontextmanager
async def wire_client(server, mode: str, api_key: str | None = None) -> AsyncIterator[Client]:
    """Use the official client without its catalog cache masking a server regression."""
    url, _, token = server
    headers = {"X-API-Key": api_key} if api_key is not None else {LOCAL_AUTH_HEADER: token}
    async with httpx2.AsyncClient(headers=headers, trust_env=False, timeout=20) as http:
        async with Client(
            streamable_http_client(f"{url}/mcp", http_client=http),
            mode=MODERN_VERSION if mode == "modern" else "legacy",
            read_timeout_seconds=20,
            cache=None,
        ) as client:
            yield client


class ControlledPlugin:
    """A wire-only Unity peer retaining counters and one held command, not a transcript."""

    def __init__(self, server, index: int, api_key: str | None = None):
        self.server = server
        self.index = index
        self.hash = f"assurance{index}"
        self.instance = f"Assurance{index}@{self.hash}"
        self.commands = 0
        self.cancelled = Event()
        self.entered = Event()
        self.hold_next = False
        self.failure = None
        self.api_key = api_key

    @contextmanager
    def connected(self) -> Iterator["ControlledPlugin"]:
        """Register before yielding and join the owned reader on every exit path."""
        _, port, token = self.server
        headers = (
            {"X-API-Key": self.api_key} if self.api_key is not None else {LOCAL_AUTH_HEADER: token}
        )
        with connect(
            f"ws://127.0.0.1:{port}/hub/plugin",
            additional_headers=headers,
            proxy=None,
        ) as socket:
            assert json.loads(socket.recv(timeout=10))["type"] == "welcome"
            socket.send(
                json.dumps(
                    {
                        "type": "register",
                        "project_name": f"Assurance{self.index}",
                        "project_hash": self.hash,
                        "unity_version": "6000.0",
                        "capabilities": ["command_cancel_v1"],
                    }
                )
            )
            registered = json.loads(socket.recv(timeout=10))
            assert registered["type"] == "registered"
            if self.api_key is not None:
                socket.send(
                    json.dumps({"type": "register_tools", "tools": [{"name": "manage_scene"}]})
                )

            def respond() -> None:
                try:
                    for raw in socket:
                        message = json.loads(raw)
                        match message["type"]:
                            case "ping":
                                socket.send(
                                    json.dumps(
                                        {"type": "pong", "session_id": registered["session_id"]}
                                    )
                                )
                            case "execute":
                                self.commands += 1
                                if self.hold_next:
                                    self.hold_next = False
                                    self.entered.set()
                                    continue
                                socket.send(
                                    json.dumps(
                                        {
                                            "type": "command_result",
                                            "id": message["id"],
                                            "result": {
                                                "success": True,
                                                "data": {"name": self.hash},
                                            },
                                        }
                                    )
                                )
                            case "cancel":
                                self.cancelled.set()
                            case other:
                                raise AssertionError(f"Unexpected plugin frame: {other}")
                except ConnectionClosed:
                    return
                except (AssertionError, ValueError, KeyError) as error:
                    self.failure = error

            reader = Thread(target=respond, name=f"assurance-peer-{self.index}", daemon=True)
            reader.start()
            try:
                yield self
            finally:
                socket.close()
                reader.join(timeout=10)
                assert not reader.is_alive(), "Owned fixture reader survived cleanup"
                assert self.failure is None, self.failure


async def read_scene(client: Client, instance: str) -> str:
    result = await client.call_tool(
        "manage_scene", {"action": "get_active", "unity_instance": instance}
    )
    assert not result.is_error, result
    assert result.structured_content["success"] is True
    return result.structured_content["data"]["name"]
