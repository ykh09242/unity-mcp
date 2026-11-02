"""Bound known discovery and prompt MCP responses before SDK serialization."""

import json
from collections.abc import Sequence

from fastmcp.prompts.base import Prompt, PromptResult
from fastmcp.resources.base import Resource
from fastmcp.resources.template import ResourceTemplate
from fastmcp.tools.base import Tool
from fastmcp.server.middleware import Middleware, MiddlewareContext, CallNext
from fastmcp.exceptions import FastMCPError, to_mcp_error
from mcp.shared.exceptions import MCPError
from mcp_types import (
    ListToolsResult,
    ListResourcesResult,
    ListResourceTemplatesResult,
    ListPromptsResult,
)
from pydantic import BaseModel, JsonValue

from models.response_limits import MAX_RESPONSE_BYTES, response_size
from services.tools.response_work import needs_response_worker, response_work
from transport.response_delivery import (
    MCP_RESULT_BYTES,
    capture_response_delivery,
    delivery_meta,
    retained_response,
)


class ResponseCatalogLimits(Middleware):
    """Retain oversized inventories explicitly instead of returning partial discovery."""

    async def _handle(
        self, wire: BaseModel | dict[str, JsonValue], context: MiddlewareContext
    ) -> dict[str, JsonValue] | None:
        large = needs_response_worker(wire)
        fits = await response_work(
            lambda: response_size(wire, max_bytes=MCP_RESULT_BYTES) is not None, large=large
        )
        if fits:
            return None
        admitted = await response_work(
            lambda: response_size(wire, max_bytes=MAX_RESPONSE_BYTES - 32_768) is not None,
            large=large,
        )
        if not admitted:
            return {"state": "unavailable", "reason": "response_payload_limit"}
        delivery = await capture_response_delivery(context.fastmcp_context)
        return await response_work(lambda: retained_response(wire, delivery), large=True)

    async def _inventory(
        self, wire: BaseModel | dict[str, JsonValue], context: MiddlewareContext
    ) -> None:
        handle = await self._handle(wire, context)
        if handle is not None:
            raise MCPError(
                -32000,
                "MCP inventory exceeds the delivery budget; inspect data.delivery for recovery.",
                {"reason": "response_transport_limit", "delivery": handle},
            )

    async def _visible_error(
        self, error: FastMCPError, context: MiddlewareContext
    ) -> MCPError | None:
        """Preserve SDK error codes and masking; adapt only public oversized errors."""
        translated = to_mcp_error(error)
        handle = await self._handle(translated.error, context)
        if handle is None:
            return None
        return MCPError(
            translated.error.code,
            "MCP operation returned an oversized error; inspect data.delivery. "
            "Do not repeat mutations to retrieve their response.",
            {"reason": "response_transport_limit", "original_is_error": True, "delivery": handle},
        )

    async def on_list_tools(
        self, context: MiddlewareContext, call_next: CallNext
    ) -> Sequence[Tool]:
        result = await call_next(context)
        await self._inventory(
            ListToolsResult(tools=[tool.to_mcp_tool() for tool in result]), context
        )
        return result

    async def on_list_resources(
        self, context: MiddlewareContext, call_next: CallNext
    ) -> Sequence[Resource]:
        result = await call_next(context)
        await self._inventory(
            ListResourcesResult(resources=[resource.to_mcp_resource() for resource in result]),
            context,
        )
        return result

    async def on_list_resource_templates(
        self, context: MiddlewareContext, call_next: CallNext
    ) -> Sequence[ResourceTemplate]:
        result = await call_next(context)
        await self._inventory(
            ListResourceTemplatesResult(
                resourceTemplates=[template.to_mcp_template() for template in result]
            ),
            context,
        )
        return result

    async def on_list_prompts(
        self, context: MiddlewareContext, call_next: CallNext
    ) -> Sequence[Prompt]:
        result = await call_next(context)
        await self._inventory(
            ListPromptsResult(prompts=[prompt.to_mcp_prompt() for prompt in result]), context
        )
        return result

    async def on_discover(
        self, context: MiddlewareContext, call_next: CallNext
    ) -> BaseModel | dict[str, JsonValue]:
        result = await call_next(context)
        await self._inventory(result, context)
        return result

    async def on_initialize(
        self, context: MiddlewareContext, call_next: CallNext
    ) -> BaseModel | None:
        result = await call_next(context)
        if result is not None:
            await self._inventory(result, context)
        return result

    async def on_get_prompt(self, context: MiddlewareContext, call_next: CallNext) -> PromptResult:
        try:
            result = await call_next(context)
        except FastMCPError as error:
            bounded = await self._visible_error(error, context)
            if bounded is not None:
                raise bounded from None
            raise
        handle = await self._handle(result.to_mcp_prompt_result(), context)
        if handle is not None:
            return PromptResult(json.dumps({"delivery": handle}), meta=delivery_meta(result.meta))
        return result
