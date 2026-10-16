"""Normalize authorized inventories before the SDK deduplicates and paginates them."""

from fastmcp.server.middleware import Middleware, MiddlewareContext


class InventoryOrderingMiddleware(Middleware):
    """Keep discovery presentation independent of provider registration order."""

    async def on_list_tools(self, context: MiddlewareContext, call_next):
        return sorted(await call_next(context), key=lambda tool: tool.name)

    async def on_list_resources(self, context: MiddlewareContext, call_next):
        return sorted(await call_next(context), key=lambda resource: str(resource.uri))

    async def on_list_resource_templates(self, context: MiddlewareContext, call_next):
        return sorted(await call_next(context), key=lambda template: str(template.uri_template))

    async def on_list_prompts(self, context: MiddlewareContext, call_next):
        return sorted(await call_next(context), key=lambda prompt: prompt.name)
