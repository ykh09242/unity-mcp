"""Verify competing managed launches retain discovery and graceful shutdown ownership."""

import errno
import json
import os
import socket
import subprocess
import time
from contextlib import ExitStack
from pathlib import Path

import httpx
import pytest
from websockets.sync.client import connect

from cli.utils.config import CLIConfig
from cli.utils.connection import run_list_instances
from core.local_auth import local_http_sockets
from tests.stdio_process import native_python
from tests.timing import watchdog_timeout


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
@pytest.mark.parametrize("owner_disconnects_first", [True, False])
def test_competing_server_preserves_discovery_and_restart_rotates(
    tmp_path, monkeypatch, host, owner_disconnects_first
):
    server_dir = Path(__file__).resolve().parents[2]
    token_path = tmp_path / "token"
    monkeypatch.setenv("UNITY_MCP_LOCAL_AUTH_TOKEN_FILE", str(token_path))
    monkeypatch.delenv("UNITY_MCP_LOCAL_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("NO_PROXY", "*")
    env = os.environ.copy()
    env.update(
        {
            "UNITY_MCP_SKIP_STARTUP_CONNECT": "1",
            "DISABLE_TELEMETRY": "1",
            "UNITY_MCP_HTTP_REMOTE_HOSTED": "false",
            "PYTHONASYNCIODEBUG": "1",
        }
    )
    entry = Path(__file__).with_name("competing_launch.py")
    processes = {}

    def failure_report(process):
        report = processes[process] / "failure.json"
        return json.loads(report.read_text(encoding="ascii")) if report.exists() else {}

    def launch_states():
        states = {}
        for process, root in processes.items():
            code = process.poll()
            states[root.name] = (code, failure_report(process)) if code is not None else "running"
        return states

    def wait_until(predicate, phase):
        deadline = time.monotonic() + watchdog_timeout(30)
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(0.05)  # Bounded observation of external process progress.
        raise AssertionError(f"Timed out waiting for {phase}: {launch_states()}")

    with ExitStack() as resources:
        # Keep the listeners through CLI and server construction. Release only
        # when both contenders reach their own production socket acquisition.
        reservation = resources.enter_context(ExitStack())
        try:
            listeners = reservation.enter_context(local_http_sockets(host, 0))
        except OSError:
            if host == "::1":
                pytest.skip("IPv6 loopback is unavailable")
            raise
        endpoint = next((item for item in listeners if item.family == socket.AF_INET), listeners[0])
        client_host, port = endpoint.getsockname()[:2]
        authority = f"[{client_host}]" if endpoint.family == socket.AF_INET6 else client_host
        base_url = f"http://{authority}:{port}"
        url = f"{base_url}/api/instances"
        client = resources.enter_context(httpx.Client(trust_env=False, timeout=2))

        def start(project):
            root = tmp_path / project
            root.mkdir()
            pidfile = root / "Library" / "MCPForUnity" / "RunState" / f"mcp_http_{port}.pid"
            launch_token = f"owned-test-launch-{project}"
            ready_file = root / "ready"
            log = resources.enter_context((root / "server.log").open("wb"))
            command = [
                str(entry),
                "--transport",
                "http",
                "--http-host",
                host,
                "--http-port",
                str(port),
                "--pidfile",
                str(pidfile),
                "--unity-instance-token",
                launch_token,
                "--project-scoped-tools",
            ]
            process = subprocess.Popen(
                native_python(command, (server_dir / "src",)),
                cwd=server_dir,
                env={**env, "LAUNCH_READY_FILE": str(ready_file)},
                stdin=subprocess.PIPE,
                stdout=log,
                stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            processes[process] = root
            return process, pidfile, launch_token, ready_file

        def gated(launch):
            process, _pidfile, _identity, ready_file = launch
            assert process.poll() is None, f"Exited before the launch gate: {launch_states()}"
            return ready_file.exists() and ready_file.read_text() == str(process.pid)

        def release(launch):
            process = launch[0]
            process.stdin.write(b"1")
            process.stdin.flush()
            process.stdin.close()

        def ready():
            exit_codes = [process.poll() for process in processes]
            assert None in exit_codes, f"All managed launches exited: {launch_states()}"
            try:
                token = token_path.read_text().strip()
                if client.get(url, headers={"X-Unity-MCP-Token": token}).status_code == 200:
                    return token
            except (FileNotFoundError, httpx.TransportError):
                pass
            return None

        def shutdown(launch):
            return client.post(
                f"{base_url}/api/server/shutdown", json={"instance_token": launch[2]}
            )

        def expect_sessions(count):
            def observed():
                response = client.get(url)
                assert response.status_code == 200
                return len(response.json()["instances"]) == count

            wait_until(observed, f"{count} remaining Unity sessions")

        try:
            launches = [start("project-a"), start("project-b")]
            wait_until(lambda: all(gated(launch) for launch in launches), "both launch gates")
            reservation.close()
            for launch in launches:
                release(launch)
            original = wait_until(ready, "authenticated server readiness")
            wait_until(
                lambda: sum(launch[0].poll() is not None for launch in launches) == 1,
                "one competing launch to exit",
            )
            winner = next(launch for launch in launches if launch[0].poll() is None)
            loser = next(launch for launch in launches if launch[0].poll() is not None)
            assert loser[0].returncode != 0
            failure = failure_report(loser[0])
            assert (
                failure.get("file") == "local_auth.py"
                and failure.get("function") == "_bind_listeners"
                and (
                    failure.get("errno") == errno.EADDRINUSE
                    or failure.get("winerror") in (10013, 10048)
                )
            ), f"Contender exited for an unexpected reason: {failure}"
            for process, pidfile, _identity, _ready_file in launches:
                assert int(pidfile.read_text()) == process.pid
            assert token_path.read_text().strip() == original
            client.headers["X-Unity-MCP-Token"] = original
            assert client.get(url).status_code == 200
            assert "instances" in run_list_instances(CLIConfig(host=client_host, port=port))
            refused = shutdown(loser)
            assert refused.status_code == 403
            assert refused.json()["error"] == "launch_identity_mismatch"

            sessions = []
            for project in ("owner", "peer"):
                plugin = resources.enter_context(
                    connect(
                        f"ws://{authority}:{port}/hub/plugin",
                        additional_headers={"X-Unity-MCP-Token": original},
                        proxy=None,
                        open_timeout=watchdog_timeout(5),
                        close_timeout=watchdog_timeout(5),
                    )
                )
                assert json.loads(plugin.recv(timeout=watchdog_timeout(5)))["type"] == "welcome"
                plugin.send(
                    json.dumps(
                        {
                            "type": "register",
                            "project_hash": project,
                            "project_name": project,
                        }
                    )
                )
                assert json.loads(plugin.recv(timeout=watchdog_timeout(5)))["type"] == "registered"
                sessions.append(plugin)
            expect_sessions(2)
            assert shutdown(winner).status_code == 409
            first, last = sessions if owner_disconnects_first else reversed(sessions)
            first.close()
            expect_sessions(1)
            busy = shutdown(winner)
            assert busy.status_code == 409
            assert busy.json()["error"] == "sessions_active"
            assert winner[0].poll() is None
            assert token_path.read_text().strip() == original
            last.close()
            expect_sessions(0)
            # Even after both editors disconnect, ownership does not transfer. This
            # also models the owning editor exiting while the peer still uses it.
            assert shutdown(loser).status_code == 403
            assert winner[0].poll() is None
            # The replacement reaches its bind gate while the winner still owns
            # the listeners. Its actual launch occurs only after graceful shutdown.
            replacement = start("project-restart")
            wait_until(lambda: gated(replacement), "replacement launch gate")
            accepted = shutdown(winner)
            assert accepted.status_code == 200
            assert accepted.json() == {"success": True, "status": "shutdown_requested"}
            assert winner[0].wait(timeout=watchdog_timeout(15)) == 0
            assert not token_path.exists()

            release(replacement)
            renewed = wait_until(ready, "replacement readiness")
            assert renewed != original
            client.headers["X-Unity-MCP-Token"] = renewed
            assert "instances" in run_list_instances(CLIConfig(host=client_host, port=port))
            assert shutdown(winner).status_code == 403
            assert shutdown(replacement).status_code == 200
            assert replacement[0].wait(timeout=watchdog_timeout(15)) == 0
            assert not token_path.exists()
        finally:
            # Only fixture-owned PIDs: never terminate a process discovered by port.
            # Success must already have proven graceful exit through the endpoint.
            for process in processes:
                if process.stdin and not process.stdin.closed:
                    process.stdin.close()
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=watchdog_timeout(15))
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=watchdog_timeout(15))
