"""Exercise an archive-installed server's public HTTP/CLI contracts without Unity.

Run with the archive environment's Python, isolated mode and an empty working directory:
    python -I /path/to/check_server_archive.py --forbid-source CHECKOUT --candidate-sha FULL_SHA
No listeners, Editor discovery, user configuration or external connections are allowed.
"""

import argparse
import asyncio
from importlib import import_module
from importlib.metadata import distribution, version
import json
import logging
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from typing import Final


PACKAGES: Final = ("main", "core", "cli", "services", "transport", "fastmcp", "httpx", "click")


def installed_origins(forbidden: Path) -> dict[str, str]:
    """Require archive metadata and imports inside this interpreter's installation."""
    installed = distribution("ykh09242-unity-mcp-server")
    direct = json.loads(installed.read_text("direct_url.json") or "{}")
    if "archive_info" not in direct or direct.get("dir_info", {}).get("editable"):
        raise RuntimeError("Installed archive required: missing archive installation metadata")
    prefix = Path(sys.prefix).resolve()
    origins = {}
    for name in PACKAGES:
        module = import_module(name)
        origin = Path(module.__file__).resolve()
        if not origin.is_relative_to(prefix) or origin.is_relative_to(forbidden):
            raise RuntimeError(f"Installed archive required: source leakage for {name}: {origin}")
        origins[name] = str(origin)
    return origins


def reject_network(event: str, _args: tuple) -> None:
    """Fail before a probe can discover an Editor or reach a user service."""
    if event in {"socket.bind", "socket.connect"}:
        raise RuntimeError(f"Archive validation must not use network sockets: {event}")


async def check_http(root: Path) -> list[str]:
    """Use the production ASGI routes and middleware with owned, empty hub state."""
    import httpx
    from core.config import config
    from main import create_mcp_server
    from transport.plugin_hub import PluginHub
    from transport.plugin_registry import PluginRegistry

    config.transport_mode = "http"
    config.http_remote_hosted = False
    config.local_auth_token = "archive-owned-launch-token"
    server = create_mcp_server(
        project_scoped_tools=False,
        test_wait_tasks_root=root / "waits",
        execution_receipts_root=root / "receipts",
        execution_receipts_private_root=True,
    )
    PluginHub.configure(PluginRegistry())
    app = server.http_app(json_response=True, stateless_http=True)
    passed = []
    try:
        # Do not enter the complete application lifespan: it discovers Unity. The
        # HTTP requests below exercise real routes without any network transport.
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            health = await client.get("/health")
            assert health.status_code == 200 and health.json()["status"] == "healthy"
            assert config.local_auth_token not in health.text
            passed.append("health_and_token_redaction")
            for path in ("/api/instances", "/api/custom-tools", "/mcp"):
                response = await client.get(path)
                assert response.status_code == 401, (path, response.text)
            passed.append("unauthenticated_control_routes")
            headers = {"X-Unity-MCP-Token": config.local_auth_token}
            browser = await client.get(
                "/api/instances", headers={**headers, "Origin": "https://browser.invalid"}
            )
            assert browser.status_code == 403
            duplicate = await client.get(
                "/api/instances", headers=[*headers.items(), ("X-Unity-MCP-Token", "old-token")]
            )
            assert duplicate.status_code == 401
            passed.append("browser_and_duplicate_token_rejection")
            instances = await client.get("/api/instances", headers=headers)
            assert instances.status_code == 200
            assert instances.json() == {"success": True, "instances": []}
            passed.append("authenticated_empty_instances")
            missing = await client.post("/api/command", headers=headers, json={})
            assert missing.status_code == 400 and missing.json()["success"] is False
            wrong_type = await client.post("/api/command", headers=headers, content="{}")
            assert wrong_type.status_code == 415
            passed.append("command_input_validation")
            custom = await client.get("/api/custom-tools", headers=headers)
            assert custom.status_code == 503 and custom.json()["success"] is False
            passed.append("honest_disconnected_custom_tools")
    finally:
        server._execution_receipts.close()
        tasks = getattr(server, "_test_job_tasks", None)
        if tasks is not None:
            await tasks.close()
        await PluginHub.shutdown()
    return passed


def check_cli() -> list[str]:
    """Invoke the installed public CLI parser without dispatching any command."""
    from click.testing import CliRunner
    from cli.main import cli

    runner = CliRunner()
    help_result = runner.invoke(cli, ["--help"])
    assert help_result.exit_code == 0 and "Unity MCP" in help_result.output
    assert all(name in help_result.output for name in ("gameobject", "scene", "doctor"))
    malformed = runner.invoke(cli, ["--no-project-context", "raw", "read_console", "not-json"])
    assert malformed.exit_code != 0 and "Invalid JSON params" in malformed.output
    scalar = runner.invoke(cli, ["--no-project-context", "raw", "read_console", "[]"])
    assert scalar.exit_code != 0 and "expected an object" in scalar.output
    return ["help_catalog", "raw_json_parse_error", "raw_json_object_required"]


async def check_behavior(
    root: Path, forbidden: Path
) -> tuple[dict[str, str], dict[str, list[str]]]:
    # Windows' event loop creates its own wakeup socket before this hook is installed.
    sys.addaudithook(reject_network)
    origins = installed_origins(forbidden)
    return origins, {"http": await check_http(root), "cli": check_cli()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--forbid-source", type=Path, required=True)
    parser.add_argument("--candidate-sha", required=True)
    args = parser.parse_args()
    forbidden = args.forbid_source.resolve()
    with TemporaryDirectory(prefix="unity-mcp-archive-") as temporary:
        root = Path(temporary).resolve(strict=True)
        for name in (
            "HOME",
            "USERPROFILE",
            "XDG_CONFIG_HOME",
            "XDG_DATA_HOME",
            "UNITY_MCP_LOG_DIR",
        ):
            os.environ[name] = str(root)
        for name in tuple(os.environ):
            if name.startswith("UNITY_MCP_"):
                del os.environ[name]
        os.environ["UNITY_MCP_LOG_DIR"] = str(root)
        os.environ["DISABLE_TELEMETRY"] = "true"
        os.environ["UNITY_MCP_TELEMETRY_ENABLED"] = "false"
        previous = Path.cwd()
        try:
            os.chdir(root)
            origins, behaviors = asyncio.run(check_behavior(root, forbidden))
            print(
                json.dumps(
                    {
                        "candidate_sha": args.candidate_sha,
                        "python": sys.version.split()[0],
                        "dependencies": {
                            name: version(name)
                            for name in (
                                "ykh09242-unity-mcp-server",
                                "fastmcp",
                                "mcp",
                                "httpx",
                                "pydantic",
                                "click",
                            )
                        },
                        "origins": origins,
                        "behaviors": behaviors,
                    }
                ),
                flush=True,
            )
        finally:
            os.chdir(previous)
            logging.shutdown()


if __name__ == "__main__":
    main()
