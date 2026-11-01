"""Independent authenticated HTTP and framed stdio children preserve target ownership."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
import json
import os
from pathlib import Path
import ssl
import sys
from threading import Thread

import anyio
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import MCPError
import pytest

from tests.http.client_assurance_legacy import LegacyPeer
from tests.http.client_assurance_wire import ControlledPlugin, read_scene, wire_client
from tests.http.test_local_control_wire import local_server_process


@contextmanager
def external_validator(tmp_path, monkeypatch) -> Iterator[str]:
    """Serve two synthetic verdicts over verified HTTPS without patching admission."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Assurance validator")])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ip_address("127.0.0.1"))]), critical=False
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    certificate_path = tmp_path / "fixture-ca.pem"
    fixture_key = tmp_path / "fixture-private.pem"
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    fixture_key.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    monkeypatch.setenv("SSL_CERT_FILE", str(certificate_path))

    class Validator(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            key = body.get("api_key")
            users = {"assurance-alice": "alice", "assurance-bob": "bob"}
            payload = json.dumps({"valid": key in users, "user_id": users.get(key)}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, _format, *args) -> None:
            """Do not retain synthetic credentials or request logs."""

    with ThreadingHTTPServer(("127.0.0.1", 0), Validator) as validator:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certificate_path, fixture_key)
        validator.socket = context.wrap_socket(validator.socket, server_side=True)
        worker = Thread(target=validator.serve_forever, name="assurance-validator", daemon=True)
        worker.start()
        try:
            yield f"https://127.0.0.1:{validator.server_port}/validate"
        finally:
            validator.shutdown()
            worker.join(timeout=10)
            assert not worker.is_alive()
            fixture_key.unlink()
            certificate_path.unlink()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["modern", "legacy"])
async def test_production_auth_isolates_two_principals_and_denies_cross_target_dispatch(
    tmp_path, monkeypatch, mode
):
    # Given: the real remote admission stack calls a loopback external validator.
    with (
        external_validator(tmp_path, monkeypatch) as validation_url,
        local_server_process(
            tmp_path, module="tests.http.client_assurance_server", validation_url=validation_url
        ) as server,
        ControlledPlugin(server, 1, "assurance-alice").connected() as alice,
        ControlledPlugin(server, 2, "assurance-bob").connected() as bob,
    ):
        async with (
            wire_client(server, mode, "assurance-alice") as alice_client,
            wire_client(server, mode, "assurance-bob") as bob_client,
        ):
            for client, own, other in ((alice_client, alice, bob), (bob_client, bob, alice)):
                inventory = await client.read_resource(
                    "mcpforunity://instances", cache_mode="refresh"
                )
                instances = json.loads(inventory.contents[0].text)["instances"]
                assert {instance["id"] for instance in instances} == {own.instance}
                with anyio.fail_after(10):
                    while "manage_scene" not in {
                        tool.name for tool in (await client.list_tools(cache_mode="refresh")).tools
                    }:
                        await anyio.lowlevel.checkpoint()
                # When: the authenticated requester dispatches its own target and another owner's target.
                assert await read_scene(client, own.instance) == own.hash
                calls_before = other.commands
                try:
                    refused = await client.call_tool(
                        "manage_scene", {"action": "get_active", "unity_instance": other.instance}
                    )
                except MCPError:
                    pass  # Middleware rejection may use the protocol error boundary.
                else:
                    assert refused.is_error
                # Then: the unauthorized peer receives no command.
                assert other.commands == calls_before

            async def interleave(client: Client, peer: ControlledPlugin) -> None:
                for _ in range(8):
                    assert await read_scene(client, peer.instance) == peer.hash

            async with anyio.create_task_group() as tasks:
                tasks.start_soon(interleave, alice_client, alice)
                tasks.start_soon(interleave, bob_client, bob)


@pytest.mark.asyncio
async def test_stdio_routes_two_framed_targets_and_restores_pinned_selection(tmp_path):
    # Given: isolated status files and real framed TCP peers with explicit legacy opt-in.
    root = Path(__file__).resolve().parents[2]
    with (
        LegacyPeer(tmp_path, 1).connected() as first,
        LegacyPeer(tmp_path, 2).connected() as second,
    ):
        params = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "tests.http.client_assurance_server",
                "--transport",
                "stdio",
                "--project-scoped-tools",
            ],
            cwd=root,
            env={
                **os.environ,
                "PYTHONPATH": str(root / "src"),
                "DISABLE_TELEMETRY": "1",
                "UNITY_MCP_SKIP_STARTUP_CONNECT": "1",
                "UNITY_MCP_STATUS_DIR": str(tmp_path),
                "UNITY_MCP_STDIO_ALLOW_LEGACY": "1",
                "UNITY_MCP_TOOL_PROFILE": "full",
                "UNITY_MCP_LOG_DIR": str(tmp_path / "logs"),
            },
        )
        with (tmp_path / "stdio.log").open("w", encoding="utf-8") as log:
            async with Client(
                stdio_client(params, errlog=log), mode="legacy", cache=None
            ) as client:
                pin = await client.call_tool("set_active_instance", {"instance": first.instance})
                assert not pin.is_error
                # When: an explicit second-target call overrides the connection's pinned target.
                assert await read_scene(client, second.instance) == second.hash
                implicit = await client.call_tool("manage_scene", {"action": "get_active"})
                # Then: only that request changes route, and both sockets close with the child.
                assert implicit.structured_content["data"]["name"] == first.hash
                assert first.commands == second.commands == 1
