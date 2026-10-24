"""Production CLI with test-only catalog load and observable retention counters.

No middleware is replaced. Unity commands still cross the real WebSocket hub.
This module is a subprocess entry point, never imported into the shared test runner.
"""

import json
import os

import main
from services.registry import mcp_for_unity_tool
from transport.plugin_hub import PluginHub


def build_server(*args, **kwargs):
    """Add bounded synthetic catalog pressure through production tool registration."""
    count = int(os.environ.get("UNITY_MCP_ASSURANCE_CATALOG_SIZE", "512"))
    if not 1 <= count <= 4096:
        raise ValueError("Assurance catalog size must be in 1..4096")

    for index in range(count):

        async def echo(value: int = 7) -> int:
            """Return a strictly validated scalar without contacting Unity."""
            return value

        mcp_for_unity_tool(name=f"assurance_catalog_{index:04d}", group="vfx", unity_target=None)(
            echo
        )
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
