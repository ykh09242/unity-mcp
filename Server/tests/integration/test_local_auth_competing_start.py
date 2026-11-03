"""Verify local discovery through competing real server processes."""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from cli.utils.config import CLIConfig
from cli.utils.connection import run_list_instances


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_competing_server_preserves_discovery_and_restart_rotates(tmp_path, monkeypatch, host):
    server_dir = Path(__file__).resolve().parents[2]
    token_path = tmp_path / "token"
    client_host = "127.0.0.1" if host == "localhost" else host
    family = socket.AF_INET6 if client_host == "::1" else socket.AF_INET
    try:
        with socket.socket(family) as candidate:
            candidate.bind((client_host, 0))
            port = candidate.getsockname()[1]
    except OSError:
        if host == "::1":
            pytest.skip("IPv6 loopback is unavailable")
        raise
    env = os.environ.copy()
    monkeypatch.setenv("UNITY_MCP_LOCAL_AUTH_TOKEN_FILE", str(token_path))
    monkeypatch.delenv("UNITY_MCP_LOCAL_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("NO_PROXY", "*")
    env.update(
        {
            "UNITY_MCP_LOCAL_AUTH_TOKEN_FILE": str(token_path),
            "UNITY_MCP_SKIP_STARTUP_CONNECT": "1",
            "DISABLE_TELEMETRY": "1",
            "UNITY_MCP_HTTP_REMOTE_HOSTED": "false",
        }
    )
    command = [
        sys.executable,
        "src/main.py",
        "--transport",
        "http",
        "--http-host",
        host,
        "--http-port",
        str(port),
    ]
    authority = f"[{client_host}]" if family == socket.AF_INET6 else client_host
    url = f"http://{authority}:{port}/api/instances"

    def start():
        return subprocess.Popen(
            command, cwd=server_dir, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

    def ready(process):
        deadline = time.monotonic() + 30
        with httpx.Client(trust_env=False, timeout=1) as client:
            while time.monotonic() < deadline:
                assert process.poll() is None, "local server exited before becoming ready"
                if token_path.exists():
                    token = token_path.read_text().strip()
                    try:
                        if client.get(url, headers={"X-Unity-MCP-Token": token}).status_code == 200:
                            return token
                    except httpx.TransportError:
                        pass
                time.sleep(0.05)
        raise AssertionError("local server did not become ready")

    first = start()
    try:
        original = ready(first)
        competing = subprocess.run(
            command,
            cwd=server_dir,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
        assert competing.returncode != 0
        assert token_path.read_text().strip() == original
        with httpx.Client(trust_env=False, timeout=2) as client:
            assert client.get(url, headers={"X-Unity-MCP-Token": original}).status_code == 200
        assert "instances" in run_list_instances(CLIConfig(host=client_host, port=port))
    finally:
        first.terminate()
        first.wait(timeout=15)

    second = start()
    try:
        assert ready(second) != original
        assert "instances" in run_list_instances(CLIConfig(host=client_host, port=port))
    finally:
        second.terminate()
        second.wait(timeout=15)
