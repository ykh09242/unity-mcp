"""Exercise an archive-installed server's public MCP/HTTP/CLI contracts without Unity.

Run with the archive environment's Python, isolated mode and an empty working directory:
    python -I /path/to/check_server_archive.py --forbid-source CHECKOUT --candidate-sha FULL_SHA --archive-file ZIP
Only the supervised MCP worker may launch and contact its own loopback listener.
Editor discovery, user configuration and external connections are prohibited.
"""

import argparse
import asyncio
from importlib import import_module
from importlib.metadata import distribution, version
import json
import hashlib
import logging
import os
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
from typing import Final
from urllib.parse import urlsplit


PACKAGES: Final = (
    "main",
    "core",
    "cli",
    "services",
    "transport",
    "fastmcp",
    "mcp",
    "httpx",
    "httpx2",
    "click",
)
Json = str | int | float | bool | None | list["Json"] | dict[str, "Json"]
JsonMap = dict[str, Json]


def verify_archive_origin(direct: JsonMap, source: str | Path, candidate: str) -> JsonMap:
    """Bind installed origin to the selected immutable URL or independently hashed local ZIP."""
    if re.fullmatch(r"[0-9a-f]{40}", candidate) is None or candidate == "0" * 40:
        raise RuntimeError("Archive provenance requires a full nonzero candidate SHA")
    match source:
        case Path():
            if not source.is_file() or source.is_symlink():
                raise RuntimeError("Archive provenance requires an ordinary local ZIP")
            expected_url = source.resolve().as_uri()
            with source.open("rb") as stream:
                expected_hash = hashlib.file_digest(stream, "sha256").hexdigest()
            subdirectory = None
        case str():
            expected = (
                f"https://github.com/ykh09242/unity-mcp/archive/{candidate}.zip#subdirectory=Server"
            )
            if source != expected:
                raise RuntimeError("Archive provenance source differs from selected immutable SHA")
            expected_url = urlsplit(source)._replace(fragment="").geturl()
            expected_hash = None
            subdirectory = "Server"
        case _:
            raise RuntimeError("Unsupported archive provenance input")
    archive = direct.get("archive_info")
    if (
        not isinstance(archive, dict)
        or "dir_info" in direct
        or "vcs_info" in direct
        or direct.get("url") != expected_url
        or direct.get("subdirectory") != subdirectory
    ):
        raise RuntimeError("Installed archive required: provenance differs from selected source")
    hashes = archive.get("hashes", {})
    if not isinstance(hashes, dict):
        raise RuntimeError("Installed archive required: provenance hashes must be an object")
    checksum = hashes.get("sha256")
    legacy = archive.get("hash")
    if legacy is not None:
        if not isinstance(legacy, str) or "=" not in legacy:
            raise RuntimeError("Installed archive required: provenance legacy hash is malformed")
        algorithm, legacy_digest = legacy.split("=", 1)
        if (
            not algorithm
            or not legacy_digest
            or ("hashes" in archive and hashes.get(algorithm) != legacy_digest)
        ):
            raise RuntimeError("Installed archive required: provenance legacy hash is inconsistent")
        if "hashes" not in archive and algorithm == "sha256":
            checksum = legacy_digest
    if (
        not isinstance(checksum, str)
        or re.fullmatch(r"[0-9a-f]{64}", checksum) is None
        or checksum == "0" * 64
        or expected_hash is not None
        and checksum != expected_hash
    ):
        raise RuntimeError(
            "Installed archive required: provenance SHA256 mismatch or missing digest"
        )
    return direct


def installed_origins(forbidden: Path) -> dict[str, str]:
    """Require archive metadata and imports inside this interpreter's installation."""
    installed = distribution("ykh09242-unity-mcp-server")
    direct = json.loads(installed.read_text("direct_url.json") or "{}")
    if "archive_info" not in direct or direct.get("dir_info", {}).get("editable"):
        raise RuntimeError("Installed archive required: missing archive installation metadata")
    prefix = Path(sys.prefix).resolve()
    declared = {Path(installed.locate_file(path)).resolve() for path in installed.files or ()}
    origins = {}
    for name in PACKAGES:
        module = import_module(name)
        origin = Path(module.__file__).resolve()
        if not origin.is_relative_to(prefix) or origin.is_relative_to(forbidden):
            raise RuntimeError(f"Installed archive required: source leakage for {name}: {origin}")
        if name in {"main", "core", "cli", "services", "transport"} and origin not in declared:
            raise RuntimeError(f"Installed archive required: undeclared application module {name}")
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


def check_mcp(root: Path, forbidden: Path) -> dict:
    """Supervise real installed protocol exchanges with existing bounded tree cleanup."""
    # Only assurance tooling is loaded from this explicit directory. Product
    # imports were checked before this and the child starts afresh with -I.
    tools_directory = str(Path(__file__).resolve().parent)
    sys.path.insert(0, tools_directory)
    try:
        from player_e2e_process import Invocation, execute
    finally:
        sys.path.remove(tools_directory)
    directory = root / "mcp-protocol"
    invocation = Invocation(
        command=[
            sys.executable,
            "-I",
            "-W",
            "error",
            str(Path(__file__).with_name("installed_mcp_probe.py")),
            "--forbid-source",
            str(forbidden),
            "--root",
            str(directory),
        ],
        directory=directory,
        timeout=90,
    )
    result = execute(invocation)
    if result["actual_exit_code"] or result["forced_termination"] or result["log_truncated"]:
        # Retain a bounded diagnosis even when TemporaryDirectory later removes
        # owned state. Redact the generated launch credential before emission.
        with (directory / "process.log").open("rb") as log:
            log.seek(max(0, log.seek(0, 2) - 4096))
            detail = log.read(4096).decode("utf-8", errors="replace")
        token_path = directory / "http" / "launch-token"
        if token_path.exists():
            token = token_path.read_text(encoding="utf-8").strip()
            if token:
                detail = detail.replace(token, "[redacted]")
        print(detail, file=sys.stderr)
        raise RuntimeError("Installed MCP protocol worker failed; bounded diagnostic emitted above")
    proofs = json.loads((directory / "protocol.json").read_text(encoding="utf-8"))
    if set(proofs) != {"stdio", "http"}:
        raise RuntimeError("Installed MCP protocol worker returned incomplete transport proof")
    return {
        "transports": proofs,
        "supervision": {
            "process_id": result["process_id"],
            "process_ended": result["process_ended"],
            "forced_termination": False,
            "timeout_seconds": invocation.timeout,
        },
    }


async def check_behavior(root: Path, forbidden: Path) -> tuple[dict[str, str], dict]:
    # Windows' event loop creates its own wakeup socket before this hook is installed.
    sys.addaudithook(reject_network)
    origins = installed_origins(forbidden)
    return origins, {
        "http": await check_http(root),
        "cli": check_cli(),
        "mcp": check_mcp(root, forbidden),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--forbid-source", type=Path, required=True)
    parser.add_argument("--candidate-sha", required=True)
    origin = parser.add_mutually_exclusive_group(required=True)
    origin.add_argument("--archive-source", help="Selected immutable launcher source URL")
    origin.add_argument("--archive-file", type=Path, help="Actual local candidate ZIP installed")
    args = parser.parse_args()
    forbidden = args.forbid_source.resolve()
    source = args.archive_file.resolve() if args.archive_file else args.archive_source
    installed = distribution("ykh09242-unity-mcp-server")
    direct = json.loads(installed.read_text("direct_url.json") or "{}")
    verified_origin = verify_archive_origin(direct, source, args.candidate_sha)
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
            installed = distribution("ykh09242-unity-mcp-server")
            record = installed.read_text("RECORD")
            if record is None:
                raise RuntimeError("Installed archive required: missing distribution RECORD")
            print(
                json.dumps(
                    {
                        "candidate_sha": args.candidate_sha,
                        "archive_origin": verified_origin,
                        "archive_hashes": json.loads(installed.read_text("direct_url.json"))[
                            "archive_info"
                        ].get("hashes", {}),
                        "installed_record_sha256": hashlib.sha256(
                            record.encode("utf-8")
                        ).hexdigest(),
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
