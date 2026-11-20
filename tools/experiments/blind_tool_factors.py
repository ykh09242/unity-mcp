"""Single-variable counterfactuals for an operator's read-only experiment relay."""

import json

CAMERA_TOOLS = {"manage_camera", "manage_cameras"}
CAMERA_SENTENCE = (
    "For read-only camera inventory, read the MCP resource mcpforunity://scene/cameras."
)


def alternate_order(payload):
    """Reverse each returned route list. Keep its members, alternatives and pagination intact."""
    if not isinstance(payload, dict):
        return payload
    if "data" in payload:
        data = alternate_order(payload["data"])
        return {**payload, "data": data} if data is not payload["data"] else payload
    if not isinstance(payload.get("routes"), list):
        return payload
    return {
        **payload,
        **{
            name: list(reversed(payload[name]))
            for name in ("routes", "route_groups")
            if isinstance(payload.get(name), list)
        },
    }


def install_factor(server, factor):
    """Attach one response-only intervention outside the operator's normal observation guard."""
    from fastmcp.server.middleware import Middleware
    from fastmcp.tools.base import ToolResult

    if factor not in {"camera-description", "route-order"}:
        raise ValueError("Choose exactly one supported factor")

    class Factor(Middleware):
        async def on_list_tools(self, context, call_next):
            tools = await call_next(context)
            if factor != "camera-description":
                return tools
            return [
                tool.model_copy(
                    update={"description": tool.description.replace(" " + CAMERA_SENTENCE, "")}
                )
                if tool.name in CAMERA_TOOLS and CAMERA_SENTENCE in (tool.description or "")
                else tool
                for tool in tools
            ]

        async def on_call_tool(self, context, call_next):
            result = await call_next(context)
            name = context.message.name
            args = context.message.arguments or {}
            if (
                factor != "route-order"
                or result.is_error
                or not (
                    name == "search_unity_tools"
                    or (name == "manage_tools" and args.get("action") == "search")
                )
            ):
                return result
            reordered = alternate_order(result.structured_content)
            if reordered is result.structured_content:
                return result
            content = []
            for block in result.content:
                if block.type == "text":
                    try:
                        value = json.loads(block.text)
                    except ValueError:
                        value = None
                    if value == result.structured_content:
                        block = block.model_copy(
                            update={
                                "text": json.dumps(
                                    reordered, ensure_ascii=False, separators=(",", ":")
                                )
                            }
                        )
                content.append(block)
            return ToolResult(content=content, structured_content=reordered, meta=result.meta)

    server.middleware.insert(0, Factor())
