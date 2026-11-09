"""Real MCP v2 exchanges with archive-installed stdio and authenticated HTTP servers.

The archive harness supervises this worker as an owned process tree. Its only
network peer is an HTTP server it launches with an empty, private configuration.
"""

import argparse
import asyncio
from dataclasses import asdict, dataclass, replace
from importlib.metadata import distribution
from importlib.util import find_spec
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import anyio
import httpx2
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

TOOL = "debug_request_context"
PACKAGE = "ykh09242-unity-mcp-server"


@dataclass(frozen=True)
class ProtocolProof:
    """Only verified, non-secret facts from a completed protocol exchange."""

    protocol_version: str
    tool_count: int
    main_origin: str
    steps: tuple[str, ...] = ("initialize", "tools/list", "tools/call:debug_request_context")
    child_pid: int | None = None
    child_exit_code: int | None = None
    child_shutdown: str | None = None


def installed_main_origin(forbidden: Path) -> Path:
    """Refuse editable metadata, source shadowing and undeclared entry modules."""
    installed = distribution(PACKAGE)
    direct = json.loads(installed.read_text("direct_url.json") or "{}")
    spec = find_spec("main")
    origin = Path(spec.origin).resolve() if spec and spec.origin else forbidden
    declared = {Path(installed.locate_file(path)).resolve() for path in installed.files or ()}
    if (
        "archive_info" not in direct
        or direct.get("dir_info", {}).get("editable")
        or not origin.is_relative_to(Path(sys.prefix).resolve())
        or origin.is_relative_to(forbidden)
        or origin not in declared
    ):
        raise RuntimeError("Installed archive required: invalid main module origin")
    return origin


def validate_tool_result(result: types.CallToolResult, origin: Path, root: Path) -> None:
    """Require a useful tool response from the installed server's actual lifespan."""
    if result.is_error:
        raise RuntimeError("Installed MCP tool failed")
    texts = [item.text for item in result.content if isinstance(item, types.TextContent)]
    if len(texts) != 1:
        raise RuntimeError("Installed MCP tool returned unexpected content")
    payload = json.loads(texts[0])
    if payload.get("success") is not True:
        raise RuntimeError("Installed MCP tool failed")
    data = payload["data"]
    server = data["server"]
    if Path(server["argv"][0]).resolve() != origin:
        raise RuntimeError("Installed MCP server origin differs from the installed entry module")
    if Path(server["cwd"]).resolve() != root:
        raise RuntimeError("Installed MCP server escaped its neutral working directory")
    if not server["version"] or data["session_state"]["plugin_hub_configured"] is not True:
        raise RuntimeError("Installed MCP server did not enter its application lifespan")


async def exchange(streams, origin: Path, root: Path) -> ProtocolProof:
    """Initialize, traverse a bounded catalog and validate one pure server call."""
    read, write = streams
    async with ClientSession(read, write, read_timeout_seconds=10.0) as session:
        with anyio.fail_after(30):
            initialized = await session.initialize()
            if not initialized.protocol_version or not initialized.server_info.name:
                raise RuntimeError("Installed MCP initialization returned no server identity")
            count = 0
            listing = await session.list_tools()
            for _ in range(8):
                count += len(listing.tools)
                if TOOL in {tool.name for tool in listing.tools}:
                    result = await session.call_tool(TOOL, {}, read_timeout_seconds=10.0)
                    validate_tool_result(result, origin, root)
                    return ProtocolProof(initialized.protocol_version, count, str(origin))
                if not listing.next_cursor:
                    break
                listing = await session.list_tools(
                    params=types.PaginatedRequestParams(cursor=listing.next_cursor)
                )
            raise RuntimeError("Installed MCP catalog did not expose the pure diagnostic tool")


async def http_exchange(
    url: str, client: httpx2.AsyncClient, origin: Path, root: Path
) -> ProtocolProof:
    """Use the SDK HTTP transport, including session shutdown on manager exit."""
    async with streamable_http_client(url, http_client=client, terminate_on_close=True) as streams:
        return await exchange(streams, origin, root)


def owned_environment(root: Path) -> dict[str, str]:
    """Provide system essentials and owned application state without ambient config."""
    environment = {
        name: os.environ[name]
        for name in ("SYSTEMROOT", "SYSTEMDRIVE", "PATH", "PATHEXT", "COMSPEC", "WINDIR")
        if name in os.environ
    }
    for name in (
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "TEMP",
        "TMP",
        "UNITY_MCP_LOG_DIR",
        "UNITY_MCP_STATUS_DIR",
    ):
        environment[name] = str(root)
    environment.update(
        DISABLE_TELEMETRY="true",
        UNITY_MCP_TELEMETRY_ENABLED="false",
        UNITY_MCP_SKIP_STARTUP_CONNECT="1",
        UNITY_MCP_EXECUTION_RECEIPTS_DIR=str(root / "receipts"),
        UNITY_MCP_EXECUTION_RECEIPTS_PRIVATE_ROOT="1",
        UNITY_MCP_TEST_WAIT_TASKS_DIR=str(root / "waits"),
        UNITY_MCP_LOCAL_AUTH_TOKEN_FILE=str(root / "launch-token"),
        UNITY_MCP_TOOL_PROFILE="core",
    )
    if os.environ.get("PY_KEY_VALUE_DISABLE_BEARTYPE") == "true":
        environment["PY_KEY_VALUE_DISABLE_BEARTYPE"] = "true"
    return environment


async def check_stdio(root: Path, origin: Path) -> ProtocolProof:
    """The installed SDK owns startup, protocol pipes and child process shutdown."""
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-I", "-W", "error", "-m", "main", "--transport", "stdio"],
        cwd=str(root),
        env=owned_environment(root),
    )
    async with stdio_client(parameters) as streams:
        proof = await exchange(streams, origin, root)
    return replace(proof, child_shutdown="sdk_context_exit")


async def check_http(root: Path, origin: Path) -> ProtocolProof:
    """Launch a real local server; contact it only after its owned token is published."""
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    # A port race fails closed: main binds before publishing this unique token
    # file, so no existing listener is contacted if our own launch cannot bind.
    environment = owned_environment(root)
    command = [
        sys.executable,
        "-I",
        "-W",
        "error",
        "-m",
        "main",
        "--transport",
        "http",
        "--http-host",
        "127.0.0.1",
        "--http-port",
        str(port),
    ]
    process = subprocess.Popen(
        command,
        cwd=root,
        env=environment,
        stdout=sys.stderr,
        stderr=sys.stderr,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    token_path = Path(environment["UNITY_MCP_LOCAL_AUTH_TOKEN_FILE"])
    try:
        deadline = time.monotonic() + 15
        while not token_path.exists():
            if process.poll() is not None:
                raise RuntimeError("Owned installed HTTP server exited before binding")
            if time.monotonic() >= deadline:
                raise TimeoutError("Owned installed HTTP server did not publish its launch token")
            await anyio.sleep(0.05)
        token = token_path.read_text(encoding="utf-8").strip()
        async with httpx2.AsyncClient(
            headers={"X-Unity-MCP-Token": token},
            timeout=httpx2.Timeout(5, read=10),
            trust_env=False,
        ) as client:
            url = f"http://127.0.0.1:{port}"
            while True:
                if process.poll() is not None:
                    raise RuntimeError("Owned installed HTTP server exited before readiness")
                try:
                    health = await client.get(url + "/health")
                    health.raise_for_status()
                    break
                except httpx2.TransportError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            "Owned installed HTTP server readiness deadline"
                        ) from None
                    await anyio.sleep(0.05)
            rejected = await client.get(url + "/mcp", headers={"X-Unity-MCP-Token": "invalid"})
            if rejected.status_code != 401:
                raise RuntimeError("Installed HTTP MCP endpoint did not enforce authentication")
            proof = await http_exchange(url + "/mcp", client, origin, root)
    finally:
        shutdown = "already_exited"
        if process.poll() is None:
            shutdown = "terminate"
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            shutdown = "kill"
            process.kill()
            process.wait(timeout=5)
        # Reaping the listener owner closes its socket handles. An immediate
        # exclusive rebind can fail for TCP connections still in TIME_WAIT and
        # is not a portable cleanup assertion; never probe a reused port.
    return replace(
        proof,
        child_pid=process.pid,
        child_exit_code=process.returncode,
        child_shutdown=shutdown,
    )


async def check_both(root: Path, forbidden: Path) -> dict[str, ProtocolProof]:
    origin = installed_main_origin(forbidden)
    proofs = {}
    for name, checker in (("stdio", check_stdio), ("http", check_http)):
        directory = root / name
        directory.mkdir()
        proofs[name] = await checker(directory, origin)
    return proofs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--forbid-source", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    os.chdir(root)
    proof = asyncio.run(check_both(root, args.forbid_source.resolve()))
    (root / "protocol.json").write_text(
        json.dumps({name: asdict(result) for name, result in proof.items()}), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
