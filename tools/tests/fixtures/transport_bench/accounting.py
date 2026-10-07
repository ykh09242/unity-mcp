"""Owned observation of product reservation counters, never an RSS estimate."""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import JsonValue

from services.tools.shared_read_budget import shared_read_budget
from transport.plugin_hub import PluginHub
from transport.stdio_response_delivery import stdio_delivery


@dataclass(slots=True)
class Accounting:
    """Mutable high-water marks observed at actual product receive/control seams."""

    mode: str
    peaks: dict[str, int] = field(default_factory=dict)

    def snapshot(self) -> dict[str, JsonValue]:
        delivery = stdio_delivery.get()
        if self.mode == "stdio" and delivery is None:
            raise RuntimeError("Owned control cannot observe product stdio delivery context")
        shared = shared_read_budget.retained_bytes
        values = {"shared_read_reserved_bytes": shared}
        if self.mode == "http":
            values.update(
                {
                    "hub_reserved_bytes": sum(
                        row["bytes"] for row in PluginHub._retained_results.values()
                    )
                    + sum(row["bytes"] for row in PluginHub._raw_results.values()),
                    "assembler_buffer_bytes": PluginHub._large_results.retained_bytes
                    if PluginHub._large_results
                    else 0,
                    "pending_commands": len(PluginHub._pending),
                }
            )
        else:
            values["charged_stdio_deliveries"] = (
                sum(bool(entry.owner.entries) for entry in delivery.pending.values())
                if delivery
                else 0
            )
        for key, value in values.items():
            self.peaks[key] = max(self.peaks.get(key, 0), value)
        return {
            **values,
            "stdio_delivery_observer_available": delivery is not None
            if self.mode == "stdio"
            else None,
            "capacity_bytes": PluginHub.MAX_RETAINED_RESULT_BYTES
            if self.mode == "http"
            else shared_read_budget.max_bytes,
            "scope": "hub reservations and overlapping assembler buffers"
            if self.mode == "http"
            else "shared-read reservations and charged SDK deliveries; legacy/SDK transient buffers unmeasured",
        }

    def drained(self) -> bool:
        snapshot = self.snapshot()
        names = (
            "shared_read_reserved_bytes",
            "hub_reserved_bytes",
            "assembler_buffer_bytes",
            "pending_commands",
            "charged_stdio_deliveries",
        )
        return all(snapshot.get(name, 0) == 0 for name in names)

    async def wait_drained(self) -> dict[str, JsonValue]:
        import anyio

        with anyio.fail_after(8):
            while not self.drained():
                await anyio.sleep(0)
        return self.snapshot()
