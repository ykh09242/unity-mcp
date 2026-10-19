"""Build-report archive public wrapper contracts; native artifact behavior has owned fixtures."""

from unittest.mock import AsyncMock

import anyio
import pytest

from services.tools import manage_build as module


@pytest.fixture
def wire(monkeypatch):
    send = AsyncMock(return_value={"success": True, "data": {"details_complete": False}})
    context = AsyncMock(return_value="owned")
    monkeypatch.setattr(module, "get_unity_instance_from_context", context)
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    return send, context


@pytest.mark.parametrize(
    "action,options,expected",
    [
        (
            "report_history",
            {"page_size": "7", "page_number": "2"},
            {"page_size": 7, "page_number": 2},
        ),
        (
            "report",
            {
                "report_id": "br-" + "a" * 32,
                "report_section": "packed_assets",
                "report_revision": "b" * 64,
            },
            {
                "report_id": "br-" + "a" * 32,
                "report_section": "packed_assets",
                "report_revision": "b" * 64,
            },
        ),
        (
            "compare_reports",
            {
                "baseline_report_id": "br-" + "a" * 32,
                "candidate_report_id": "br-" + "b" * 32,
                "report_section": "steps",
            },
            {
                "baseline_report_id": "br-" + "a" * 32,
                "candidate_report_id": "br-" + "b" * 32,
                "report_section": "steps",
            },
        ),
    ],
)
def test_report_actions_admit_closed_bounded_wire_and_keep_partial_result(
    wire, action, options, expected
):
    # Given: transport-only mocks around the existing public tool.
    async def call():
        return await module.manage_build(None, action=action, **options)

    # When: reading retained reports or comparing their inventories.
    result = anyio.run(call)
    # Then: native observations are returned unchanged with one exact bounded request.
    assert result == {"success": True, "data": {"details_complete": False}}
    params = wire[0].await_args.args[3]
    assert params == {"action": action, "page_size": 50, "page_number": 1, **expected}


@pytest.mark.parametrize(
    "action,options",
    [
        ("report", {}),
        ("report", {"report_id": "../outside"}),
        ("report", {"report_id": "br-" + "a" * 32, "page_size": True}),
        ("report_history", {"page_size": 101}),
        ("report_history", {"page_number": 0}),
        ("report_history", {"target": "windows64"}),
        ("report_history", {"dry_run": False}),
        ("report_history", {"report_id": "br-" + "a" * 32}),
        ("report", {"report_id": "br-" + "a" * 32, "report_section": "unknown"}),
        ("report", {"report_id": "br-" + "a" * 32, "report_revision": "guess"}),
        ("compare_reports", {"baseline_report_id": "br-" + "a" * 32}),
        ("status", {"report_section": "steps"}),
    ],
)
def test_report_invalid_options_refuse_before_instance_or_transport(wire, action, options):
    # Given: invalid bounds, identity, section or action-specific scope.
    async def call():
        return await module.manage_build(None, action=action, **options)

    # When: direct callers bypass schema validation.
    result = anyio.run(call)
    # Then: no Editor activity occurs and the refusal has a stable report code.
    assert result["success"] is False
    assert result["code"] == "invalid_build_report_request"
    wire[0].assert_not_awaited()
    wire[1].assert_not_awaited()


def test_report_sdk_schema_and_response_survive_real_fastmcp_call(wire):
    from fastmcp import Client, FastMCP

    async def call():
        # Given: the actual public wrapper published as an SDK tool.
        server = FastMCP("build-report-contract")
        server.tool()(module.manage_build)
        async with Client(server) as client:
            tools = await client.list_tools()
            props = tools[0].input_schema["properties"]
            assert {
                "report_id",
                "baseline_report_id",
                "candidate_report_id",
                "report_section",
                "page_size",
            } <= props.keys()
            # When: fetching a bounded report page through generated schema validation.
            result = await client.call_tool(
                "manage_build",
                {
                    "action": "report",
                    "report_id": "br-" + "a" * 32,
                    "report_section": "steps",
                    "page_size": 3,
                },
            )
            # Then: the stored partial-detail status is preserved.
            assert result.structured_content == {
                "success": True,
                "data": {"details_complete": False},
            }
            assert wire[0].await_args.args[3]["page_size"] == 3

    anyio.run(call)


def test_report_sdk_rejects_boolean_page_without_editor_io(wire):
    from fastmcp import Client, FastMCP

    async def call():
        # Given: schema validation precedes direct-call admission.
        server = FastMCP("build-report-strict-page")
        server.tool()(module.manage_build)
        async with Client(server) as client:
            # When: a boolean could otherwise coerce to an integer page size.
            result = await client.call_tool(
                "manage_build",
                {"action": "report_history", "page_size": True},
                raise_on_error=False,
            )
            # Then: it is refused without any Editor context or transport activity.
            assert result.is_error or result.structured_content.get("success") is False
            wire[0].assert_not_awaited()
            wire[1].assert_not_awaited()

    anyio.run(call)
