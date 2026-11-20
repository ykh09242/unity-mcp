"""Check a bounded set of exact names from SDK-filtered pages on one connection."""

from typing import Final, TYPE_CHECKING

import anyio

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
    deadline = client.factory.deadline
    timeout = None if deadline is None else max(0, deadline - anyio.current_time())
    with anyio.move_on_after(timeout):
        for _ in range(_MAX_PAGES):
            await anyio.lowlevel.checkpoint_if_cancelled()
            try:
                page = await client.list_tools_mcp(cursor=cursor, meta=meta)
            except (DiagnosticLimit, TimeoutError):
                # A read budget can end a scan while earlier names remain observed.
                break
            if client.tool_inventory_epoch != epoch:
                return unknown
            cursor = page.next_cursor
            if cursor is not None and (
                not 0 < len(cursor) <= _MAX_CURSOR or cursor in seen_cursors
            ):
                break
            remaining.difference_update(tool.name for tool in page.tools)
            complete = cursor is None
            if complete or not remaining:
                break
            seen_cursors.add(cursor)
    if client.tool_inventory_epoch != epoch:
        return unknown
    return HostToolEvidence(
        candidates=unknown.candidates,
        present=tuple(name for name in unknown.candidates if name not in remaining),
        complete=complete,
    )
