"""Exercise exact host candidate evidence through the MCP tools and CLI doctor."""

import json

import anyio
from click.testing import CliRunner
from fastmcp import Client, FastMCP
from mcp.types import ListToolsResult, Tool
import pytest

from tests.test_http_command_outcome import command_hub  # noqa: F401


TARGET = "Fixture@aaaa1111"


def _tools(count: int) -> list[Tool]:
    return [
        Tool(name=f"inventory_{index:04}", inputSchema={"type": "object"})
        for index in range(1, count + 1)
    ]


def _candidate(names: list[str], present: list[str], *, complete: bool) -> dict:
    return {"candidates": names, "present": present, "complete": complete}


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [511, 512, 513, 648])
async def test_large_catalog_candidate_checks_against_current_advertisement(count: int):
    from services.tools.manage_tools import manage_tools, search_unity_tools

    server = FastMCP("host-inventory")
    server.tool(manage_tools)
    server.tool(search_unity_tools)
    server.resource("inventory://fixture", name="inventory fixture")(lambda: "fixture")
    catalog = [item.name for item in _tools(count - 2)]

    def probe() -> str:
        pytest.fail("inventory inspection and search must not invoke a candidate")

    for name in catalog:
        server.tool(name=name, description="Fixture hostprobe")(probe)

    async with Client(server, mode="2026-07-28") as client:
        wire_names = [item.name for item in await client.list_tools()]
        assert len(wire_names) == count
        last = sorted(wire_names)[-3]
        assert last == catalog[-1]
        inspection = {"action": "inspect_inventory", "tool": last}
        legacy = await client.call_tool(
            "manage_tools", {**inspection, "host_tool_names": wire_names}, raise_on_error=False
        )
        if count <= 512:
            assert legacy.structured_content["status"] == "host_advertised"
        else:
            assert legacy.is_error

        candidates = [last, "manage_tools", "server_missing"]
        for complete, present, expected in (
            (False, [last], "host_advertised"),
            (False, [], "host_unknown"),
            (True, [], "host_filtered"),
        ):
            result = await client.call_tool(
                "manage_tools",
                {
                    **inspection,
                    "host_tool_evidence": _candidate(candidates, present, complete=complete),
                },
            )
            assert result.structured_content["status"] == expected

        evidence = _candidate(candidates, [last, "server_missing"], complete=False)
        args = {
            "query": "마지막 후보가 보이는지 확인",
            "catalog_query": last,
            "host_tool_evidence": evidence,
        }
        for tool, extra in (("search_unity_tools", {}), ("manage_tools", {"action": "search"})):
            result = (await client.call_tool(tool, {**args, **extra})).structured_content
            assert result["route_groups"][0]["representative"]["tool"] == last
            assert result["host_inventory"]["statuses"] == {
                last: "host_advertised",
                "manage_tools": "host_unknown",
            }
        generic = await client.call_tool(
            "search_unity_tools",
            {**args, "catalog_query": "hostprobe", "route_surfaces": "tools", "limit": 1},
        )
        assert generic.structured_content["route_groups"][0]["representative"]["tool"] == last
        for complete, expected in ((False, True), (True, False)):
            scoped = await client.call_tool(
                "search_unity_tools",
                {**args, "host_tool_evidence": _candidate([last], [], complete=complete)},
            )
            result = scoped.structured_content
            assert any(route.get("tool") == last for route in result["routes"]) is expected
            assert result["host_inventory"]["statuses"][last] == (
                "host_filtered" if complete else "host_unknown"
            )
        missing = await client.call_tool(
            "manage_tools",
            {**inspection, "tool": "server_missing", "host_tool_evidence": evidence},
        )
        assert missing.structured_content["status"] == "server_unavailable"

        # An omitted name and a fresh caller do not inherit a report's absence.
        omitted = await client.call_tool(
            "manage_tools",
            {
                **inspection,
                "host_tool_evidence": _candidate(["manage_tools"], [], complete=True),
            },
        )
        assert omitted.structured_content["status"] == "host_unknown"
    async with Client(server, mode="legacy") as second:
        result = await second.call_tool("manage_tools", inspection)
        assert result.structured_content["status"] == "host_unknown"
        server.disable(names={last})
        result = await second.call_tool(
            "manage_tools", {**inspection, "host_tool_evidence": evidence}
        )
        assert result.structured_content["status"] == "server_unavailable"
        result = await second.call_tool("search_unity_tools", args)
        assert last not in result.structured_content["host_inventory"]["statuses"]


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [511, 512, 513, 648])
@pytest.mark.parametrize("complete", [True, False])
async def test_doctor_follows_pages_until_candidate_or_a_complete_miss(
    monkeypatch, count: int, complete: bool
):
    from cli.utils.config import CLIConfig
    from cli.utils.doctor_connection import DiagnosticClient, diagnose
    from models.doctor import DoctorCLIReceipt

    catalog = _tools(count)
    final_cursor = None if complete else "page3"
    pages = {
        None: ListToolsResult(tools=catalog[:512], nextCursor="page2"),
        "page2": ListToolsResult(tools=catalog[512:], nextCursor=final_cursor),
        "page3": ListToolsResult(tools=[], nextCursor="page3"),
    }
    calls = []

    async def enter(client: DiagnosticClient) -> DiagnosticClient:
        return client

    async def read(*_args) -> DoctorCLIReceipt:
        return DoctorCLIReceipt(
            reason="verified", next_action="none", connection_verified=True, exit_code=0
        )

    async def list_page(client: DiagnosticClient, *, cursor=None, meta=None) -> ListToolsResult:
        calls.append((client, cursor, meta))
        return pages[cursor]

    monkeypatch.setattr(DiagnosticClient, "__aenter__", enter)
    monkeypatch.setattr(DiagnosticClient, "list_tools_mcp", list_page)
    monkeypatch.setattr("cli.utils.doctor_connection._auth_headers", lambda _config: {})
    monkeypatch.setattr("cli.utils.doctor_connection._read", read)
    config = CLIConfig(unity_instance=TARGET)
    for name in (catalog[-1].name, "inventory_missing"):
        calls.clear()
        receipt = await diagnose(config, "legacy", name)
        found = name == catalog[-1].name
        expected = True if found else False if complete else None
        assert receipt.tool_inventory.received is expected
        assert receipt.tool_inventory.complete is (complete and (not found or count > 512))
        assert all(meta == {"unity_instance": TARGET} for _, _, meta in calls)
        assert len({id(client) for client, _, _ in calls}) == 1
        expected_calls = [None] if found and count <= 512 else [None, "page2"]
        if not found and not complete:
            expected_calls += ["page3"]
        assert [cursor for _, cursor, _ in calls] == expected_calls


@pytest.mark.asyncio
async def test_a_catalog_change_discards_both_presence_and_absence_from_mixed_pages(monkeypatch):
    from cli.utils.config import CLIConfig
    from cli.utils.doctor_connection import DiagnosticClient, DoctorHttpFactory
    from cli.utils.host_tool_inventory import observe_host_tools

    client = DiagnosticClient(CLIConfig(unity_instance=TARGET), "legacy", DoctorHttpFactory(), {})
    original, replacement = _tools(2)

    async def changed_page(*, cursor=None, meta=None) -> ListToolsResult:
        assert meta == {"unity_instance": TARGET}
        if cursor is None:
            return ListToolsResult(tools=[original], nextCursor="page2")
        assert cursor == "page2"
        client.tool_inventory_epoch += 1
        return ListToolsResult(tools=[replacement])

    monkeypatch.setattr(client, "list_tools_mcp", changed_page)
    evidence = await observe_host_tools(client, (original.name, replacement.name), TARGET)
    assert evidence.complete is False
    assert evidence.received(original.name) is evidence.received(replacement.name) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("page_size", [1, 20])
async def test_candidate_scan_reaches_the_terminal_page_of_a_648_tool_catalog(
    monkeypatch, page_size
):
    from cli.utils.config import CLIConfig
    from cli.utils.doctor_connection import DiagnosticClient, DoctorHttpFactory
    from cli.utils.host_tool_inventory import observe_host_tools

    client = DiagnosticClient(CLIConfig(unity_instance=TARGET), "legacy", DoctorHttpFactory(), {})
    catalog = _tools(648)
    candidates = (catalog[512].name, catalog[-1].name, "inventory_missing")
    visited = []

    async def list_page(*, cursor=None, meta=None) -> ListToolsResult:
        assert meta == {"unity_instance": TARGET}
        start = int(cursor) if cursor is not None else 0
        visited.append(start)
        stop = start + page_size
        return ListToolsResult(
            tools=catalog[start:stop], nextCursor=str(stop) if stop < len(catalog) else None
        )

    monkeypatch.setattr(client, "list_tools_mcp", list_page)
    evidence = await observe_host_tools(client, candidates, TARGET)
    assert [evidence.received(name) for name in candidates] == [True, True, False]
    assert evidence.complete
    assert visited == list(range(0, len(catalog), page_size))


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [False, True])
async def test_timeout_preserves_prior_presence_only_in_the_same_catalog(monkeypatch, changed):
    from cli.utils.config import CLIConfig
    from cli.utils.doctor_connection import DiagnosticClient, DoctorHttpFactory
    from cli.utils.host_tool_inventory import observe_host_tools

    factory = DoctorHttpFactory(deadline=anyio.current_time() + 0.05)
    client = DiagnosticClient(CLIConfig(unity_instance=TARGET), "legacy", factory, {})
    present, unobserved = _tools(2)

    async def list_page(*, cursor=None, meta=None) -> ListToolsResult:
        assert meta == {"unity_instance": TARGET}
        if cursor is None:
            return ListToolsResult(tools=[present], nextCursor="next page")
        try:
            await anyio.sleep_forever()
        finally:
            if changed:
                client.tool_inventory_epoch += 1

    monkeypatch.setattr(client, "list_tools_mcp", list_page)
    with anyio.fail_after(2):
        evidence = await observe_host_tools(client, (present.name, unobserved.name), TARGET)
    assert evidence.complete is False
    assert evidence.received(present.name) is (None if changed else True)
    assert evidence.received(unobserved.name) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("paged", [False, True])
async def test_cli_doctor_checks_last_name_in_an_actual_sdk_listing(
    command_hub, monkeypatch, paged
):
    from cli.main import cli
    from core.server_build import ServerBuild
    import services.doctor as doctor
    from tests.http.test_doctor import doctor_server, install_native_replies, observations
    from tests.test_cli_doctor import live_mcp

    monkeypatch.setenv("UNITY_MCP_LOCAL_AUTH_TOKEN", "disposable-inventory-fixture-token")
    monkeypatch.delenv("UNITY_MCP_LOCAL_AUTH_TOKEN_FILE", raising=False)
    monkeypatch.setattr(doctor, "RUNNING_SERVER", ServerBuild("1.2.0", "a" * 40, "c" * 32))
    await command_hub.registry.unregister("session")
    await command_hub.registry.register("session", "Fixture", "aaaa1111", "6000")
    values = observations()
    values["get_editor_state"]["unity"]["instance_id"] = TARGET
    await install_native_replies(command_hub, values)
    server = doctor_server()
    async with Client(server) as client:
        builtins = await client.list_tools()
    catalog = _tools(648 - len(builtins))

    def probe() -> str:
        pytest.fail("doctor must not invoke a listed tool")

    for item in catalog:
        server.tool(name=item.name)(probe)

    trace = []
    page_requests = []
    pages = [None, "empty", *(str(start) for start in range(20, 648, 20))]
    wire_catalog = [*builtins, *catalog]

    def list_page(params: dict, headers: dict) -> dict:
        cursor, session = params.get("cursor"), headers.get(b"mcp-session-id")
        assert params["_meta"]["unity_instance"] == TARGET and session
        page_requests.append((cursor, session))
        # Include a valid empty page in a typical 20-result listing.
        start = int(cursor) if cursor not in {None, "empty"} else 0
        items = [] if cursor == "empty" else wire_catalog[start : start + 20]
        result = {"tools": [item.model_dump(by_alias=True, exclude_none=True) for item in items]}
        if cursor != pages[-1]:
            result["nextCursor"] = pages[pages.index(cursor) + 1]
        return result

    async with live_mcp(server, trace, tool_page=list_page if paged else None) as port:
        for name, expected in ((catalog[-1].name, True), ("absent", False)):
            result = await anyio.to_thread.run_sync(
                lambda name=name: CliRunner().invoke(
                    cli,
                    [
                        "--port",
                        str(port),
                        "--instance",
                        TARGET,
                        "--format",
                        "json",
                        "doctor",
                        "--protocol",
                        "legacy",
                        "--inspect-tool",
                        name,
                    ],
                )
            )
            assert result.exit_code == 0, (result.stdout, result.exception)
            receipt = json.loads(result.stdout)
            assert receipt["tool_inventory"]["received"] is expected
            assert receipt["connection_verified"] is True
    assert trace.count("tools/list") == (2 * len(pages) if paged else 2)
    assert "tools/call" not in trace
    if paged:
        assert [cursor for cursor, _ in page_requests] == pages * 2
        assert len({session for _, session in page_requests[: len(pages)]}) == 1
        assert len({session for _, session in page_requests[len(pages) :]}) == 1
