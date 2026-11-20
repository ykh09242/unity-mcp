"""Check a bounded set of exact names from SDK-filtered pages on one connection."""

from contextlib import AsyncExitStack
from typing import Final, TYPE_CHECKING

import anyio
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

from cli.utils.doctor_connection import DiagnosticLimit
from models.host_tool_inventory import HostToolEvidence, ToolName

if TYPE_CHECKING:
    from cli.utils.doctor_connection import DiagnosticClient


_MAX_PAGES: Final = 1024
_MAX_CURSOR: Final = 1024


async def observe_host_tools(
    client: "DiagnosticClient", candidates: tuple[ToolName, ...], target: str
) -> HostToolEvidence:
    """Use the caller's existing client, byte limit and deadline; retain only candidates."""
    unknown = HostToolEvidence(candidates=candidates, present=(), complete=False)
    remaining = set(unknown.candidates)
    epoch = client.tool_inventory_epoch
    cursor = None
    seen_cursors: set[str] = set()
    complete = False
    meta = {"unity_instance": target}
    modern = client.protocol_version in MODERN_PROTOCOL_VERSIONS
    legacy_stamp = client.legacy_tool_inventory_stamp
    watch_attempted = False
    watch_covered = legacy_stamp is not None
    deadline = client.factory.deadline
    timeout = None if deadline is None else max(0, deadline - anyio.current_time())

    def invalidated() -> bool:
        return client.tool_inventory_epoch != epoch or (
            legacy_stamp is not None and client.legacy_tool_inventory_stamp != legacy_stamp
        )

    with anyio.move_on_after(timeout):
        async with AsyncExitStack() as watch:
            for _ in range(_MAX_PAGES):
                await anyio.lowlevel.checkpoint_if_cancelled()
                try:
                    page = await client.list_tools_mcp(cursor=cursor, meta=meta)
                except (DiagnosticLimit, TimeoutError):
                    # A read budget can end a scan while earlier names remain observed.
                    break
                await anyio.lowlevel.checkpoint()
                if invalidated():
                    return unknown
                cursor = page.next_cursor
                if cursor is not None and (len(cursor) > _MAX_CURSOR or cursor in seen_cursors):
                    break
                remaining.difference_update(tool.name for tool in page.tools)
                if cursor is None:
                    complete = not seen_cursors or watch_covered
                    break
                if not remaining:
                    break
                if not watch_covered and not watch_attempted:
                    watch_attempted = True
                    if modern:
                        watch_covered = await watch.enter_async_context(
                            client.watch_modern_tool_inventory()
                        )
                    else:
                        legacy_stamp = client.legacy_tool_inventory_stamp
                        watch_covered = legacy_stamp is not None
                    if invalidated():
                        return unknown
                    if watch_covered:
                        # Restart when the watch comes online after the first request.
                        remaining = set(unknown.candidates)
                        cursor = None
                        continue
                seen_cursors.add(cursor)
    if invalidated():
        return unknown
    return HostToolEvidence(
        candidates=unknown.candidates,
        present=tuple(name for name in unknown.candidates if name not in remaining),
        complete=complete,
    )
