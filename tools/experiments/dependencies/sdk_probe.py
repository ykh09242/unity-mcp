"""Check the installed server's adapter gates and a real MCP schema exchange."""

import asyncio
from importlib.metadata import distributions
from importlib.util import find_spec
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from fastmcp import Client, FastMCP

from core.config import config
from core.sdk_compatibility import runtime_sdk_report
from services.execution_receipt_identity import ReceiptUnavailable
from services.registry.response_schema import direct_object_output_schema


def reject_network(event, _args):
    if event in {"socket.bind", "socket.connect"}:
        raise RuntimeError(f"SDK comparison must not use network sockets: {event}")


async def probe_adapters(root: Path):
    from main import create_mcp_server

    config.transport_mode = "stdio"
    config.http_remote_hosted = False
    receipts_root = root / "receipts"
    waits_root = root / "waits"
    server = create_mcp_server(
        project_scoped_tools=False,
        test_wait_tasks_root=waits_root,
        execution_receipts_root=receipts_root,
        execution_receipts_private_root=True,
    )
    receipts = server._execution_receipts
    tasks = getattr(server, "_test_job_tasks", None)
    receipt_admission = "opened"
    try:
        try:
            await receipts.start()
        except ReceiptUnavailable as error:
            if str(error) != "execution_receipt_sdk_unsupported":
                raise
            receipt_admission = str(error)
        if tasks is None:
            report = runtime_sdk_report(server)
        else:
            async with tasks.lifespan():
                report = runtime_sdk_report(server)
        resources = await server.list_resources(run_middleware=False)
        assert "mcpforunity://doctor/runtime" in {str(item.uri) for item in resources}
        return {
            "runtime": report,
            "receipt_admission": receipt_admission,
            "tasks_installed": tasks is not None,
            "store_paths_present": [receipts_root.exists(), waits_root.exists()],
        }
    finally:
        receipts.close()
        if tasks is not None:
            await tasks.close()


async def probe_tool_result():
    """Use the production envelope adapter and real SDK client-side hydration."""
    result_object = {
        "type": "object",
        "required": ["inventory"],
        "properties": {
            "inventory": {
                "type": "object",
                "required": ["slot"],
                "properties": {
                    "slot": {
                        "type": "array",
                        "prefixItems": [{"type": "string"}, {"type": "integer"}],
                    }
                },
                "additionalProperties": {"type": "string"},
            }
        },
    }
    schema = direct_object_output_schema(
        {"x-fastmcp-wrap-result": True, "properties": {"result": result_object}}
    )
    server = FastMCP("inventory-schema-fixture")

    @server.tool(output_schema=schema)
    def inventory_slot(short: bool) -> dict:
        slot = ["Fixture"] if short else ["Fixture", 7]
        return {"inventory": {"slot": slot, "source": "fixture"}}

    outcomes = {}
    for mode in ("legacy", "2026-07-28"):
        async with Client(server, mode=mode, cache=False) as client:
            catalog = {tool.name: tool for tool in await client.list_tools()}
            assert catalog["inventory_slot"].output_schema == result_object
            cases = {}
            for name, short in (("complete", False), ("short", True)):
                result = await client.call_tool("inventory_slot", {"short": short})
                wire = {
                    "inventory": {
                        "slot": ["Fixture"] if short else ["Fixture", 7],
                        "source": "fixture",
                    }
                }
                assert not result.is_error and result.structured_content == wire
                data = result.data.model_dump(mode="json") if result.data is not None else None
                cases[name] = {"wire": wire, "hydrated": data}
            outcomes[mode] = cases
    return outcomes


async def probe(root: Path):
    sys.addaudithook(reject_network)
    adapters = await probe_adapters(root)
    results = await probe_tool_result()
    return {"adapters": adapters, "results": results}


def main():
    os.environ["DISABLE_TELEMETRY"] = "true"
    os.environ["UNITY_MCP_TELEMETRY_ENABLED"] = "false"
    spec = find_spec("main")
    assert spec is not None and spec.origin is not None
    assert Path(spec.origin).resolve().is_relative_to(Path(sys.prefix).resolve())
    versions = {
        package.name.lower().replace("_", "-"): package.version for package in distributions()
    }
    with TemporaryDirectory(prefix="unity-mcp-sdk-ab-") as directory:
        root = Path(directory).resolve(strict=True)
        os.environ["UNITY_MCP_LOG_DIR"] = str(root)
        outcome = asyncio.run(probe(root))
    print(json.dumps({"python": sys.version.split()[0], "packages": versions, **outcome}, indent=2))


if __name__ == "__main__":
    main()
