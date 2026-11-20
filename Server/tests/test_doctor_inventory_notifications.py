"""Watch candidate inventory changes over the pinned SDK's actual HTTP transport."""

from contextlib import AsyncExitStack
import json

import anyio
from fastmcp import FastMCP
import pytest
from starlette.responses import Response

from cli.utils.config import CLIConfig
from cli.utils.doctor_connection import DiagnosticClient, DoctorHttpFactory
from cli.utils.host_tool_inventory import observe_host_tools
from tests.test_cli_doctor import live_mcp
from transport.catalog_subscriptions import install_catalog_subscriptions


TARGET = "Fixture@aaaa1111"
PRESENT = "observed_on_page_one"
MISSING = "absent_from_both_pages"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,changed,notifications,expected",
    [
        ("legacy", True, True, (None, None, False)),
        ("legacy", False, False, (True, None, False)),
        ("auto", True, True, (None, None, False)),
        ("auto", False, True, (True, False, True)),
        ("auto", False, False, (True, None, False)),
    ],
)
async def test_actual_sdk_catalog_notifications_fence_paged_absence(
    mode, changed, notifications, expected
):
    subscribe = mode == "auto" and notifications
    server = FastMCP("paged-doctor-notifications")
    server.tool(name=PRESENT)(lambda: "visible")
    runtime = install_catalog_subscriptions(server, remote_hosted=False) if subscribe else None
    trace, cursors = [], []
    notification = {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}
    client: DiagnosticClient

    async def page(request: dict, _headers: dict) -> dict | Response:
        params = request["params"]
        assert params["_meta"]["unity_instance"] == TARGET
        cursor = params.get("cursor")
        cursors.append(cursor)
        result = {"resultType": "complete", "tools": []}
        if mode == "auto":
            result.update({"cacheScope": "public", "ttlMs": 0})
        if cursor is None:
            result["tools"] = [{"name": PRESENT, "inputSchema": {"type": "object"}}]
            result["nextCursor"] = ""  # A provided empty cursor is valid.
        elif changed:
            if runtime is not None:
                assert runtime.bus.scopes and cursors == [None, None, ""], cursors
                epoch = client.tool_inventory_epoch
                runtime.publish()
                with anyio.fail_after(3):
                    while client.tool_inventory_epoch == epoch:
                        await anyio.sleep(0.01)
            else:
                assert mode == "legacy"
                reply = {"jsonrpc": "2.0", "id": request["id"], "result": result}
                frames = "".join(f"data: {json.dumps(item)}\n\n" for item in (notification, reply))
                return Response(frames, media_type="text/event-stream")
        return result

    async with AsyncExitStack() as stack:
        if runtime is not None:
            await stack.enter_async_context(runtime.lifespan())
        port = await stack.enter_async_context(
            live_mcp(server, trace, tool_page=page, legacy_events=notifications)
        )
        config = CLIConfig(port=port, unity_instance=TARGET)
        client = DiagnosticClient(
            config, mode, DoctorHttpFactory(deadline=anyio.current_time() + 12), {}
        )
        async with client:
            evidence = await observe_host_tools(client, (PRESENT, MISSING), TARGET)
            if changed:
                assert client.tool_inventory_epoch > 0
    assert (evidence.received(PRESENT), evidence.received(MISSING), evidence.complete) == expected
    if subscribe:
        assert cursors == [None, None, ""]
    else:
        assert cursors in ([None, ""], [None, None, ""])
    assert trace.count("subscriptions/listen") == int(subscribe)
