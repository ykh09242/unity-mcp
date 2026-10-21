"""Execution failure contracts at the real SDK and response boundaries."""

import importlib
import json
from unittest.mock import AsyncMock

from fastmcp import Client, Context, FastMCP
from fastmcp.tools import ToolResult
from mcp_types import CallToolResult, TextContent
import pytest

from models.models import MCPResponse, ToolDefinitionModel
from models.unity_response import normalize_unity_response, parse_resource_response
from services.registry import get_registered_tools
from transport.execution_receipts import _public_projection
from transport.response_limit_middleware import ResponseLimitMiddleware


FAILURE = {
    "success": False,
    "code": "fixture_refused",
    "error": "Refused",
    "data": {"applied": False},
}


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_builtin_native_failure_preserves_code_and_mcp_error(monkeypatch, mode):
    # Given the real registered native test tool and SDK boundary.
    native = importlib.import_module("services.tools.run_tests")
    info = next(
        item
        for item in get_registered_tools()
        if item["name"] == "run_tests" and item["func"] is native.run_tests
    )
    server = FastMCP("native-failure-contract")
    server.add_middleware(ResponseLimitMiddleware())
    server.tool(name=info["name"], description=info["description"], **info["kwargs"])(info["func"])
    monkeypatch.setattr(
        native,
        "get_unity_instance_from_context",
        AsyncMock(return_value="Fixture@0123456789abcdef"),
    )
    monkeypatch.setattr(native, "preflight", AsyncMock(return_value=None))
    dispatch = AsyncMock(return_value=FAILURE)
    monkeypatch.setattr(native.unity_transport, "send_with_unity_instance", dispatch)
    # When the actual SDK client observes the native refusal.
    async with Client(server, mode=mode, cache=False) as client:
        result = await client.call_tool("run_tests", {}, raise_on_error=False)
    # Then explicit failure is machine-readable without a dispatch retry.
    assert result.is_error is True
    payload = result.structured_content["result"]
    assert payload["code"] == "fixture_refused"
    assert payload["data"] == {"applied": False}
    assert dispatch.await_count == 1


@pytest.mark.parametrize(
    "response",
    [FAILURE, {"status": "error", "result": {"code": "fixture_refused", "error": "Refused"}}],
)
def test_native_error_code_survives_normalization_and_typed_resource(response):
    # Given either supported native response envelope.
    normalized = normalize_unity_response(response)
    # When a typed consumer receives the error.
    result = parse_resource_response(normalized, MCPResponse)
    # Then the native machine code survives both boundaries.
    assert result.model_dump()["code"] == "fixture_refused"


@pytest.mark.parametrize("wrapped", [False, True])
def test_receipt_retains_failure_flag_before_stripping_sdk_metadata(wrapped):
    # Given an explicit application failure with optional SDK result wrapping.
    structured = {"result": FAILURE} if wrapped else FAILURE
    result = ToolResult(
        structured_content=structured, meta={"fastmcp": {"wrap_result": True}} if wrapped else None
    )
    # When the receipt captures its durable public projection.
    wire = _public_projection(result)
    # Then stored/replayed results preserve failure without retaining SDK metadata.
    assert wire.is_error is True
    assert wire.structured_content == structured


@pytest.mark.parametrize("raw", [False, True])
def test_admission_marks_failure_without_mutating_input_or_content(raw):
    # Given typed or raw SDK content with its own metadata and failure envelope.
    wire = CallToolResult(
        content=[TextContent(type="text", text="unaltered")],
        structuredContent=FAILURE,
        _meta={"application": {"kind": "fixture"}},
    )
    source = (
        ToolResult.from_mcp_result(wire)
        if raw
        else ToolResult(content=wire.content, structured_content=FAILURE, meta=wire.meta)
    )
    # When ordinary response admission runs.
    result = ResponseLimitMiddleware().admit_tool_result(source)
    # Then the error flag reaches the wire without editing the original or payload.
    assert result.is_error is True
    assert result.to_mcp_result().is_error is True
    assert source.is_error is False
    assert wire.is_error is False
    assert result.content[0].text == "unaltered"
    assert result.structured_content == FAILURE
    assert result.meta == {"application": {"kind": "fixture"}}


@pytest.mark.parametrize(
    "value",
    [
        {
            "success": True,
            "data": {"status": "failed", "error": "test assertion", "success": False},
        },
        {"result": {"success": False}},
        {"message": "error: informational text"},
        {"success": "false"},
        {"success": 0},
    ],
)
def test_business_data_and_successful_failed_job_queries_are_not_execution_errors(value):
    # Given structured business data without an explicit top-level boolean failure.
    source = ToolResult(structured_content=value)
    # When the same ordinary guard handles it.
    result = ResponseLimitMiddleware().admit_tool_result(source)
    # Then neither text nor nested business fields invent a failure.
    assert result.is_error is False
    assert result.structured_content == value


@pytest.mark.parametrize("version", [0, 1])
@pytest.mark.parametrize("structured", [False, True])
@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_custom_failure_uses_known_response_without_parsing_text(
    monkeypatch, version, structured, mode
):
    # Given a real custom tool descriptor and service normalization boundary.
    from services.custom_tool_service import CustomToolService
    from services.custom_tool_schema_tool import install_schema_listing_policy

    server = FastMCP("custom-failure-contract")
    install_schema_listing_policy(server)
    server.add_middleware(ResponseLimitMiddleware())
    service = CustomToolService(server)
    definition = ToolDefinitionModel(
        name="fixture_custom",
        structured_output=structured,
        schema_version=version,
        **(
            {"input_schema": {"type": "object", "properties": {}, "additionalProperties": False}}
            if version
            else {}
        ),
    )

    def build_handler(_self, _definition):
        async def handler(ctx: Context) -> MCPResponse:
            return service._normalize_response(FAILURE)

        return handler

    # Replace only external project routing; descriptor conversion and SDK run stay real.
    monkeypatch.setattr(CustomToolService, "_build_global_tool_handler", build_handler)
    server.add_tool(service._prepare_global_tool(definition))
    # When a client calls this failure-producing custom tool once.
    async with Client(server, mode=mode, cache=False) as client:
        result = await client.call_tool("fixture_custom", {}, raise_on_error=False)
    # Then both content-only and structured contracts carry the explicit failure flag.
    assert result.is_error is True
    content = json.loads(result.content[0].text)
    assert content["code"] == "fixture_refused"


def test_absent_code_preserves_legacy_wire_shape():
    # Given a response from a native version without machine codes.
    response = MCPResponse(success=True, data={"status": "running"})
    # When serializing its public envelope.
    payload = response.model_dump(mode="json")
    # Then no new null field is inserted into older payloads.
    assert "code" not in payload


def test_failure_classifier_does_not_invoke_mapping_hooks():
    # Given an arbitrary mapping subclass with executable lookup hooks.
    from models.tool_outcome import is_execution_failure

    class UnexpectedLookup(dict):
        def get(self, *args):
            raise AssertionError("application hook executed")

    # When classification sees an untrusted non-JSON container.
    result = is_execution_failure(UnexpectedLookup(success=False), None)
    # Then it does not execute application methods or guess a failure.
    assert result is False


def test_failure_projection_does_not_run_subclass_copy_after_admission(monkeypatch):
    # Given a small admitted envelope whose copy hook would allocate an oversized reply.
    from models.response_limits import response_size
    import transport.response_limit_middleware as middleware

    calls = []

    class ExpandingCopy(ToolResult):
        def __copy__(self):
            calls.append("copy")
            return ToolResult(content="x" * 10_000, structured_content={"success": False})

    source = ExpandingCopy(content="small", structured_content={"success": False})
    monkeypatch.setattr(middleware, "MAX_RESPONSE_BYTES", 32_768 + 512)
    # When explicit failure is projected after the existing bounded traversal.
    result = ResponseLimitMiddleware().admit_tool_result(source)
    # Then no plugin callback expands the admitted graph or changes the original.
    assert calls == []
    assert type(result) is ToolResult
    assert result.is_error is True
    assert result.content is source.content
    assert result.structured_content is source.structured_content
    assert source.is_error is False
    assert response_size(result.to_mcp_result(), max_bytes=512) is not None
