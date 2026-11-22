"""Single-variable counterfactuals for an operator's read-only experiment relay."""

import json
from contextvars import ContextVar

CAMERA_TOOLS = {"manage_camera", "manage_cameras"}
CAMERA_SENTENCE = (
    "For read-only camera inventory, read the MCP resource mcpforunity://scene/cameras."
)
MENU_PREFIX = (
    "Find an unknown menu path with list_menu_declarations or read mcpforunity://menu-items. "
    "Use this tool once you know the exact path. inspect/exists checks its declaration "
    "without evaluating validation callbacks. "
)
EARLIER_MENU_PREFIX = (
    "Inspect menu declarations or invoke a menu by exact path. inspect/exists never evaluate "
    "validation callbacks. To discover an unknown path, mcpforunity://menu-items lists managed "
    "MenuItem declarations. A match confirms that declaration. Some native menu entries may be absent. "
)


def earlier_menu_prose(value):
    """Restore the mixed menu tool's public prose after lookup and ranking."""
    if isinstance(value, list):
        return [earlier_menu_prose(item) for item in value]
    if not isinstance(value, dict):
        return value
    projected = {key: earlier_menu_prose(item) for key, item in value.items()}
    if value.get("name") == "execute_menu_item" or value.get("tool") == "execute_menu_item":
        for key in ("description", "purpose"):
            prose = value.get(key)
            if isinstance(prose, str) and prose.startswith(MENU_PREFIX):
                replacement = EARLIER_MENU_PREFIX + prose[len(MENU_PREFIX) :]
                projected[key] = (
                    replacement[:320] if key == "purpose" and len(prose) == 320 else replacement
                )
    return projected


def projected_result(result, projected):
    from fastmcp.tools.base import ToolResult

    if projected == result.structured_content:
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
                        "text": json.dumps(projected, ensure_ascii=False, separators=(",", ":"))
                    }
                )
        content.append(block)
    return ToolResult(content=content, structured_content=projected, meta=result.meta)


def install_menu_factor(server, factor):
    """Restore earlier wording or withhold the menu resource's search metadata."""
    from fastmcp.server.middleware import Middleware
    from services.registry.action_catalog import SEARCH_TERMS_META
    from services.registry.capability_catalog import MENU_DECLARATIONS_URI

    searching = ContextVar("menu_trial_search", default=False)

    class MenuFactor(Middleware):
        async def on_list_tools(self, context, call_next):
            listed = await call_next(context)
            if factor != "menu-wording-baseline":
                return listed
            projected = []
            for tool in listed:
                if tool.name == "execute_menu_item":
                    if not (tool.description or "").startswith(MENU_PREFIX):
                        raise ValueError("Candidate menu wording differs from the trial definition")
                    tool = tool.model_copy(
                        update={
                            "description": EARLIER_MENU_PREFIX
                            + tool.description[len(MENU_PREFIX) :]
                        }
                    )
                projected.append(tool)
            return projected

        async def on_call_tool(self, context, call_next):
            name = context.message.name
            args = context.message.arguments or {}
            if factor == "menu-wording-baseline" and (
                name == "search_unity_tools"
                or (
                    name == "manage_tools"
                    and args.get("action") in {"describe", "search", "catalog"}
                )
            ):
                result = await call_next(context)
                if not result.is_error:
                    return projected_result(result, earlier_menu_prose(result.structured_content))
                return result
            if factor != "menu-ranking-baseline" or not (
                name == "search_unity_tools"
                or (name == "manage_tools" and args.get("action") == "search")
            ):
                return await call_next(context)
            token = searching.set(True)
            try:
                return await call_next(context)
            finally:
                searching.reset(token)

        async def on_list_resources(self, context, call_next):
            listed = await call_next(context)
            if not searching.get():
                return listed
            return [
                resource.model_copy(
                    update={
                        "meta": {
                            key: val
                            for key, val in (resource.meta or {}).items()
                            if key != SEARCH_TERMS_META
                        }
                    }
                )
                if str(resource.uri) == MENU_DECLARATIONS_URI
                else resource
                for resource in listed
            ]

    server.middleware.insert(0, MenuFactor())


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

    if factor in {"menu-wording-baseline", "menu-ranking-baseline"}:
        install_menu_factor(server, factor)
        return

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
            return projected_result(result, alternate_order(result.structured_content))

    server.middleware.insert(0, Factor())
