"""Production CLI with test-only catalog load and observable retention counters.

No middleware is replaced. Unity commands still cross the real WebSocket hub.
Catalog registration is explicit so construction tests can import this entry point safely.
"""

from collections.abc import Callable
from copy import deepcopy
import inspect
import json
import os
from types import FunctionType
from unittest.mock import patch

from fastmcp.tools.base import Tool
from fastmcp.tools.function_tool import FunctionTool

import main
from services.registry import mcp_for_unity_tool
from transport.plugin_hub import PluginHub


def register_catalog(count: int) -> dict[FunctionType, str]:
    """Create independent declarations through the real strict-input registry."""
    declarations = {}
    for index in range(count):

        async def echo(value: int = 7) -> int:
            """Return a strictly validated scalar without contacting Unity."""
            return value

        name = f"assurance_catalog_{index:04d}"
        mcp_for_unity_tool(name=name, group="vfx", unity_target=None)(echo)
        declarations[echo] = name
    return declarations


def _signature_state(fn: FunctionType) -> tuple:
    """Keep default types that Signature equality treats as numerically interchangeable."""
    signature = inspect.signature(fn)
    return signature, tuple(type(parameter.default) for parameter in signature.parameters.values())


def catalog_tool_factory(declarations: dict[FunctionType, str]) -> Callable[..., FunctionTool]:
    """Parse one known synthetic shape; keep every current production wrapper.

    Only this construction phase replaces the factory. Delivered tools still use
    the SDK's normal run/validation path. Capture a pristine descriptor before
    production adds routing parameters or changes its output schema.
    """
    original = Tool.from_function
    first = next(iter(declarations))
    code = first.__code__
    scope = first.__globals__
    description = first.__doc__
    signature = deepcopy(_signature_state(first))
    annotations = deepcopy(first.__annotations__)
    template = None
    wrapped_signature = None
    wrapped_annotations = None

    def clone(fn: Callable, **kwargs) -> FunctionTool:
        nonlocal template, wrapped_signature, wrapped_annotations
        if not isinstance(fn, FunctionType):
            return original(fn, **kwargs)
        declaration = inspect.unwrap(fn)
        if not isinstance(declaration, FunctionType):
            return original(fn, **kwargs)
        name = declarations.get(declaration)
        if (
            name is None
            or not name.startswith("assurance_catalog_")
            or declaration.__code__ is not code
            or declaration.__globals__ is not scope
            or declaration.__doc__ != description
            or declaration.__closure__ is not None
            or declaration.__defaults__ != (7,)
            or type(declaration.__defaults__[0]) is not int
            or declaration.__kwdefaults__ is not None
            or declaration.__annotations__ != annotations
            or _signature_state(declaration) != signature
            or fn.__doc__ != description
            or (template is not None and _signature_state(fn) != wrapped_signature)
            or (template is not None and fn.__annotations__ != wrapped_annotations)
            or kwargs
            != {"name": name, "description": None, "run_in_thread": True, "tags": {"group:vfx"}}
        ):
            return original(fn, **kwargs)
        if template is None:
            tool = original(fn, **kwargs)
            # A future SDK that wraps this no-dependency function must reparse;
            # replacing its bound function would otherwise discard that wrapper.
            if tool.fn is fn:
                template = tool.model_copy(deep=True)
                wrapped_signature = deepcopy(_signature_state(fn))
                wrapped_annotations = deepcopy(fn.__annotations__)
            return tool
        return template.model_copy(deep=True, update={"name": name, "fn": fn})

    return clone


def build_server(*args, **kwargs):
    """Add bounded synthetic catalog pressure through production tool registration."""
    count = int(os.environ.get("UNITY_MCP_ASSURANCE_CATALOG_SIZE", "512"))
    if not 1 <= count <= 4096:
        raise ValueError("Assurance catalog size must be in 1..4096")
    declarations = register_catalog(count)
    with patch.object(Tool, "from_function", catalog_tool_factory(declarations)):
        server = _create_server(*args, **kwargs)

    @server.resource("assurance://retention")
    async def retention() -> str:
        store = server._unity_session_state_store
        return json.dumps(
            {
                "entries": store.retained_entries,
                "bytes": store.retained_bytes,
                "pending": len(PluginHub._pending),
                "connections": len(PluginHub._connections),
                "retained_results": len(PluginHub._retained_results),
                "result_bytes": PluginHub._retained_results.total_bytes,
                "raw_results": len(PluginHub._raw_results),
                "ping_tasks": len(PluginHub._ping_tasks),
                "admitted": len(PluginHub._admitted),
                "source": main.__file__,
            }
        )

    return server


_create_server = main.create_mcp_server

if __name__ == "__main__":
    main.create_mcp_server = build_server
    main.main()
