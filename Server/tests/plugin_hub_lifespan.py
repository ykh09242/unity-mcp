"""Give an async test an owned PluginHub while preserving prior loop resources."""

from contextlib import asynccontextmanager
from weakref import WeakKeyDictionary

from transport.charge_ledger import ChargeLedger
from transport.plugin_hub import PluginHub
from transport.plugin_registry import PluginRegistry
from transport.tool_list_notifier import ToolListNotifier


@asynccontextmanager
async def isolated_plugin_hub(state):
    # Production keeps these owners across configure(). A function-scoped
    # fixture owns a different event loop and must never retire the prior hub.
    for name in (
        "_connections",
        "_pending",
        "_last_pong",
        "_admitted",
        "_ping_tasks",
        "_eviction_tasks",
        "_result_tasks",
        "_control_reservations",
        "_outbound_payloads",
    ):
        state.setattr(PluginHub, name, {})
    for name in (
        "_registry",
        "_mcp",
        "_lock",
        "_loop",
        "_large_results",
        "_shutdown_reservation",
    ):
        state.setattr(PluginHub, name, None)
    for name in ("_retained_results", "_raw_results"):
        state.setattr(PluginHub, name, ChargeLedger())
    state.setattr(PluginHub, "_catalog_notifications", ToolListNotifier())
    state.setattr(PluginHub, "_ordinary_state_flights", WeakKeyDictionary())
    # configure() replaces these before use. Save their original identities.
    for name in (
        "_editor_states",
        "_readiness_reads",
        "_ordinary_state_reads",
        "_shutdown_requested",
    ):
        state.setattr(PluginHub, name, getattr(PluginHub, name))
    registry = PluginRegistry()
    PluginHub.configure(registry)
    try:
        yield registry
    finally:
        await PluginHub.shutdown()
