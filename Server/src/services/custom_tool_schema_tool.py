"""Public SDK Tool adapter for exact, bounded version-one JSON schemas."""

from collections.abc import Awaitable, Callable
import json

from fastmcp import Context
from fastmcp.exceptions import ValidationError
from fastmcp.server.dependencies import get_context
from fastmcp.server.middleware.dereference import DereferenceRefsMiddleware
from fastmcp.tools import Tool, ToolResult
from mcp.types import TextContent
from pydantic import JsonValue, PrivateAttr

from models.custom_tool_schema import SchemaContractError, prepare_schema
from models.models import MCPResponse, ToolDefinitionModel


class _SchemaAwareDereferenceMiddleware(DereferenceRefsMiddleware):
    """Keep bounded rich schemas exact; preserve the installed SDK's legacy policy."""

    def __init__(self, original: DereferenceRefsMiddleware):
        self._original = original

    async def on_list_tools(self, context, call_next):
        tools = await call_next(context)

        async def legacy_only(_context):
            return [tool for tool in tools if not isinstance(tool, SchemaTool)]

        legacy = iter(await self._original.on_list_tools(context, legacy_only))
        return [tool if isinstance(tool, SchemaTool) else next(legacy) for tool in tools]

    async def on_list_resource_templates(self, context, call_next):
        return await self._original.on_list_resource_templates(context, call_next)


def install_schema_listing_policy(server) -> None:
    """Replace only FastMCP4.1's exact built-in instances, retaining list positions."""
    middleware = getattr(server, "middleware", None)
    if isinstance(middleware, list):
        for index, item in enumerate(middleware):
            if type(item) is DereferenceRefsMiddleware:
                middleware[index] = _SchemaAwareDereferenceMiddleware(item)


def require_schema_listing_policy(server) -> None:
    """Refuse rich publication when the pinned SDK's bounded listing shape is unknown."""
    middleware = getattr(server, "middleware", None)
    if not isinstance(middleware, list) or any(
        isinstance(item, DereferenceRefsMiddleware)
        and not isinstance(item, _SchemaAwareDereferenceMiddleware)
        for item in middleware
    ):
        raise ValueError("Rich custom schemas require the supported SDK listing policy")


class SchemaTool(Tool):
    """SDK owns execution framing; the installed validator owns schema semantics."""

    _handler: Callable[..., Awaitable[MCPResponse]] = PrivateAttr()
    _input_json: str = PrivateAttr()
    _output_json: str | None = PrivateAttr()
    _structured: bool = PrivateAttr()

    def __init__(
        self, definition: ToolDefinitionModel, handler: Callable[..., Awaitable[MCPResponse]]
    ):
        prepared = prepare_schema(definition.input_schema, definition.output_data_schema)
        output = MCPResponse.model_json_schema() if definition.structured_output else None
        if prepared.output_json is not None:
            data_schema = json.loads(prepared.output_json)
            definitions = data_schema.pop("$defs", {})
            data_schema.pop("$schema", None)
            output["$defs"] = definitions
            output["allOf"] = [
                {
                    "if": {"properties": {"success": {"const": True}}, "required": ["success"]},
                    "then": {"properties": {"data": data_schema}, "required": ["data"]},
                }
            ]
        super().__init__(
            name=definition.name,
            description=definition.description,
            parameters=json.loads(prepared.input_json),
            output_schema=output,
            return_type=MCPResponse,
        )
        self._handler = handler
        self._input_json = prepared.input_json
        self._output_json = prepared.output_json
        self._structured = bool(definition.structured_output)

    async def run(self, arguments: dict[str, JsonValue]) -> ToolResult:
        try:
            prepared = prepare_schema(
                json.loads(self._input_json),
                None if self._output_json is None else json.loads(self._output_json),
            )
            parameters = prepared.validate_input(arguments)
        except SchemaContractError as exc:
            raise ValidationError("Invalid custom tool parameters") from exc
        context: Context = get_context()
        response = await self._handler(context, **parameters)
        if not self._structured:
            return ToolResult(content=[TextContent(type="text", text=response.model_dump_json())])
        return self.convert_result(response)
