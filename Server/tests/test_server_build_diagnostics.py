"""Frozen version observations through the real stdio payload boundary."""

from dataclasses import FrozenInstanceError
import json
import re
import socket
import struct

import pytest

from core.config import config
from core import server_build as build
from transport.legacy import unity_connection as uc


COMMIT = "a" * 40


@pytest.mark.parametrize(
    "document",
    [
        {"url": "https://github.com/example/repo", "vcs_info": {"vcs": "git", "commit_id": COMMIT}},
        {"url": f"https://github.com/example/repo/archive/{COMMIT}.zip", "archive_info": {}},
        {"url": f"https://user:synthetic-secret@github.com/example/repo/archive/{COMMIT}.tar.gz"},
    ],
)
def test_installed_origin_returns_only_immutable_commit(document):
    assert build.commit_from_direct_url(json.dumps(document)) == COMMIT


@pytest.mark.parametrize(
    "document",
    [
        None,
        "invalid-json",
        "[]",
        "x" * 65_537,
        json.dumps({"url": "https://github.com/example/repo/archive/main.zip"}),
        json.dumps({"url": f"https://elsewhere.example/repo/archive/{COMMIT}.zip"}),
        json.dumps({"url": "file:///synthetic-local", "dir_info": {"editable": True}}),
        json.dumps({"vcs_info": {"vcs": "git", "commit_id": "main"}}),
    ],
    ids=[
        "absent",
        "invalid-json",
        "array",
        "oversized",
        "branch",
        "foreign-host",
        "local",
        "vcs-branch",
    ],
)
def test_unknown_local_or_malformed_origins_remain_unknown(document):
    assert build.commit_from_direct_url(document) is None


def test_process_build_stays_frozen_when_installed_metadata_changes(monkeypatch):
    captured = build.RUNNING_SERVER.command_metadata()
    monkeypatch.setattr(
        build.metadata, "distribution", lambda name: pytest.fail("No metadata reread")
    )
    monkeypatch.setattr(build, "MCP_VERSION", "99.0.0")
    assert build.RUNNING_SERVER.command_metadata() == captured
    assert re.fullmatch(r"[0-9a-f]{32}", captured["server_id"])
    captured["version"] = "caller-mutation"
    assert build.RUNNING_SERVER.command_metadata()["version"] != "caller-mutation"
    with pytest.raises(FrozenInstanceError):
        build.RUNNING_SERVER.version = "caller-mutation"


def test_capture_uses_default_full_commit_archive_without_reporting_its_url(monkeypatch):
    class InstalledDistribution:
        def read_text(self, filename):
            assert filename == "direct_url.json"
            return json.dumps(
                {
                    "url": f"https://github.com/ykh09242/unity-mcp/archive/{COMMIT}.zip",
                    "archive_info": {},
                    "subdirectory": "Server",
                }
            )

    monkeypatch.setattr(build.metadata, "distribution", lambda name: InstalledDistribution())
    monkeypatch.setattr(build, "MCP_VERSION", "1.2.0")
    captured = build._capture_running_build().command_metadata()
    assert captured["version"] == "1.2.0"
    assert captured["source_commit"] == COMMIT
    assert set(captured) == {"version", "source_commit", "server_id"}


def test_actual_stdio_payload_reports_frozen_build_without_changing_tool_params(monkeypatch):
    class Wire:
        def __init__(self):
            self.sent = []
            self.timeout = 1.0
            self.pending = (
                struct.pack(">Q", len(b'{"status":"success","result":{}}'))
                + b'{"status":"success","result":{}}'
            )

        def gettimeout(self):
            return self.timeout

        def settimeout(self, timeout):
            self.timeout = timeout

        def setblocking(self, blocking):
            self.timeout = None if blocking else 0.0

        def recv(self, count, flags=0):
            if flags:
                raise BlockingIOError()
            data, self.pending = self.pending[:count], self.pending[count:]
            return data

        def sendall(self, payload):
            self.sent.append(payload)

    def deny(*args, **kwargs):
        pytest.fail("Build diagnostics must not open a connection or read live discovery")

    wire = Wire()
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(uc, "read_status_file", lambda target_hash=None: None)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(uc.stdio_port_registry, "get_instances", deny)
    snapshot = build.ServerBuild("1.2.0", COMMIT, "b" * 32)
    monkeypatch.setattr(uc, "RUNNING_SERVER", snapshot)
    conn = uc.UnityConnection(
        port=1111, instance_id="Synthetic@01234567", sock=wire, use_framing=True
    )
    params = {"action": "get_active"}
    assert conn.send_command("manage_scene", params, max_attempts=0) == {}
    payload = json.loads(wire.sent[1])
    assert struct.unpack(">Q", wire.sent[0])[0] == len(wire.sent[1])
    assert payload["params"] == params
    assert payload["server_info"] == snapshot.command_metadata()
    assert "url" not in payload["server_info"]
    assert "token" not in payload["server_info"]
