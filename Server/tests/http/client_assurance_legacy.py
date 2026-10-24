"""Controlled framed TCP peers for the explicit unauthenticated legacy opt-in.

These fixtures never write native credential stores or claim authenticated Unity
coverage. They exercise the real stdio client's routing and socket lifecycle.
"""

from collections.abc import Iterator
from contextlib import contextmanager
import json
from pathlib import Path
from socketserver import StreamRequestHandler, ThreadingTCPServer
import struct
from threading import Lock, Thread


class LegacyPeer:
    """Track only live socket/command counts while real framed peers are active."""

    def __init__(self, directory: Path, index: int):
        self.directory = directory
        self.index = index
        self.hash = f"legacy{index}"
        self.instance = f"Legacy{index}@{self.hash}"
        self.active = 0
        self.commands = 0
        self.lock = Lock()

    @contextmanager
    def connected(self) -> Iterator["LegacyPeer"]:
        """Publish isolated status metadata and join every owned socket handler."""
        owner = self

        class Handler(StreamRequestHandler):
            def handle(self) -> None:
                with owner.lock:
                    owner.active += 1
                try:
                    self.connection.settimeout(10)
                    self.wfile.write(b"WELCOME UNITY-MCP 1 FRAMING=1\n")
                    while header := self.rfile.read(8):
                        if len(header) != 8:
                            return
                        length = struct.unpack(">Q", header)[0]
                        assert 0 < length < 64 * 1024
                        payload = self.rfile.read(length)
                        if payload == b"ping":
                            result = {"message": "pong"}
                        else:
                            request = json.loads(payload)
                            assert request["type"] == "manage_scene"
                            with owner.lock:
                                owner.commands += 1
                            result = {"success": True, "data": {"name": owner.hash}}
                        response = json.dumps(
                            {"status": "success", "result": result}, separators=(",", ":")
                        ).encode()
                        self.wfile.write(struct.pack(">Q", len(response)) + response)
                finally:
                    with owner.lock:
                        owner.active -= 1

        with ThreadingTCPServer(("127.0.0.1", 0), Handler) as server:
            status = self.directory / f"unity-mcp-status-{self.hash}.json"
            status.write_text(
                json.dumps(
                    {
                        "project_path": str(self.directory / f"Legacy{self.index}"),
                        "unity_port": server.server_address[1],
                        "unity_version": "6000.0",
                    }
                ),
                encoding="utf-8",
            )
            worker = Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                yield self
            finally:
                status.unlink()
                server.shutdown()
                worker.join(timeout=10)
                assert not worker.is_alive()
        assert self.active == 0
