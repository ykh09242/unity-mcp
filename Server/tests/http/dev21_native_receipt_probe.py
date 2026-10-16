"""Sole-QA orchestration: production loopback server + SDK readback, never Editor launch."""

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit

SERVER = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SERVER))
sys.path.insert(0, str(SERVER / "src"))


async def await_record(path: Path, expires: float) -> dict:
    while time.monotonic() < expires:
        if path.exists():
            if path.stat().st_size > 8192:
                raise ValueError("Owned proof record exceeds bounds")
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass  # The native writer may still own the final write.
        await asyncio.sleep(0.05)
    raise TimeoutError("Owned native receipt proof did not reach " + path.name)


def owned_http_client_factory(**kwargs):
    import httpx2

    return httpx2.AsyncClient(**kwargs, trust_env=False)


async def readback(url: str, token: str, project: Path, output: Path, timeout: int) -> None:
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport
    from core.local_auth import LOCAL_AUTH_HEADER

    parsed = urlsplit(url)
    if not parsed.port or url != f"http://127.0.0.1:{parsed.port}":
        raise ValueError("SDK readback requires the exact owned IPv4 loopback origin")
    expires = time.monotonic() + timeout
    folder = project / "Library" / "Dev21ReceiptProof"
    evidence = []
    for phase in (1, 2, 3):
        record = await await_record(folder / f"phase-{phase}.json", expires)
        assert record["phase"] == phase and record["status"] == "applied"
        assert record["tool_name"] == "dev21_native_catalog_probe"
        assert record["parameter"] == ("first_value" if phase == 1 else "second_value")
        assert type(record["desired_revision"]) is int and record["desired_revision"] > 0
        assert type(record["catalog_revision"]) is int and record["catalog_revision"] > 0
        modes = []
        for mode in ("2026-07-28", "legacy"):
            transport = StreamableHttpTransport(
                url + "/mcp",
                headers={LOCAL_AUTH_HEADER: token},
                httpx_client_factory=owned_http_client_factory,
            )
            async with Client(transport, mode=mode, timeout=10) as client:
                tools = await client.list_tools()
                matches = [tool for tool in tools if tool.name == record["tool_name"]]
                assert len(matches) == 1
                schema = matches[0].input_schema
                assert schema["required"] == [record["parameter"]]
                assert schema["properties"][record["parameter"]]["type"] == "string"
                modes.append({"mode": mode, "name": matches[0].name, "input_schema": schema})
        evidence.append({"native_receipt": record, "sdk": modes})
        (folder / f"readback-{phase}.json").write_text(
            json.dumps({"verified": True}), encoding="utf-8"
        )
    first, changed, reconnected = [item["native_receipt"] for item in evidence]
    assert changed["desired_revision"] > first["desired_revision"]
    assert changed["catalog_revision"] == first["catalog_revision"] + 1
    assert reconnected["session_id"] != changed["session_id"]
    assert reconnected["desired_revision"] == changed["desired_revision"]
    assert reconnected["settings_epoch"] == changed["settings_epoch"]
    await await_record(folder / "done.json", expires)
    (output / "native-sdk-proof.json").write_text(
        json.dumps(
            {
                "actual_native_websocket": True,
                "production_hub": True,
                "owned_fake_catalog": True,
                "native_command_execution": False,
                "phases": evidence,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def configure_owned_environment(output: Path) -> None:
    """Configure this dedicated probe process before importing server or SDK code."""
    for key in tuple(os.environ):
        normalized = key.upper()
        if normalized.startswith(("MCPFORUNITY_", "UNITY_MCP_", "FASTMCP_")) or normalized in (
            "OPENAI_API_KEY",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
        ):
            os.environ.pop(key, None)
    for key in (
        "APPDATA",
        "LOCALAPPDATA",
        "USERPROFILE",
        "HOME",
        "XDG_DATA_HOME",
        "XDG_CONFIG_HOME",
        "UNITY_MCP_LOG_DIR",
    ):
        directory = output / "runtime" / key
        directory.mkdir(parents=True, exist_ok=True)
        os.environ[key] = str(directory)
    os.environ["NO_PROXY"] = "127.0.0.1,localhost,::1"
    os.environ["UNITY_MCP_DISABLE_TELEMETRY"] = "true"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    output, project = args.output.resolve(), args.project.resolve()
    if not project.is_relative_to(output) or project == output or not (project / "Assets").is_dir():
        raise ValueError(
            "The exact prepared disposable project must be inside the owned output directory"
        )
    if not 30 <= args.timeout <= 900:
        raise ValueError("Proof timeout must be 30..900 seconds")
    if (project / "Library" / "Dev21ReceiptProof").exists():
        raise ValueError("Receipt proof requires a fresh owned project without old markers")
    output.mkdir(parents=True, exist_ok=True)
    configure_owned_environment(output)
    from tests.http.test_local_control_wire import local_server_process

    server_directory = output / "loopback-server"
    server_directory.mkdir()
    with local_server_process(server_directory) as (url, port, token):
        child = {
            "DEV21_ACK_PROBE_PROJECT": str(project),
            "DEV21_ACK_ENDPOINT": f"ws://127.0.0.1:{port}/hub/plugin",
            "DEV21_ACK_TOKEN": token,
        }
        # Disposable local credentials stay only in the explicitly owned child-env file.
        path = output / "native-child-env.json"
        if path.exists():
            raise ValueError("The owned child environment path must be fresh")
        created = False
        try:
            with path.open("x", encoding="utf-8") as stream:
                created = True
                json.dump(child, stream)
            print("Owned server ready; child environment file: " + str(path), flush=True)
            asyncio.run(readback(url, token, project, output, args.timeout))
        finally:
            if created:
                path.unlink(missing_ok=True)
        print("Owned native/SDK receipt proof saved.", flush=True)


if __name__ == "__main__":
    main()
