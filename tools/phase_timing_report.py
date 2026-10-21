"""Aggregate local scalar phase logs. Does not connect, execute work, or emit trace IDs."""

import argparse
import json
import math
from pathlib import Path
import re

SERVER = ("admission", "preflight", "send", "response", "return", "total")
NATIVE = ("queue", "handler", "import", "prepare", "serialization", "send", "total")
LINE_BYTES = 16384


def records(path):
    with path.open("rb") as stream:
        while line := stream.readline(LINE_BYTES + 1):
            if len(line) > LINE_BYTES:
                while line and not line.endswith(b"\n"):
                    line = stream.readline(LINE_BYTES + 1)
                yield None
                continue
            try:
                start = line.find(b"{")
                row = json.loads(line[start:]) if start >= 0 else None
                yield row if isinstance(row, dict) else None
            except (ValueError, UnicodeError, RecursionError):
                yield None


def normalized(row):
    if not isinstance(row, dict) or not re.fullmatch(
        "[a-f0-9]{32}", str(row.get("correlation", ""))
    ):
        return None
    event = row.get("event")
    if not isinstance(event, str):
        return None
    if event == "server_phase_timing":
        side, stages = "server", SERVER
        if (
            not isinstance(row.get("outcome"), str)
            or row.get("outcome") not in {"returned", "raised", "canceled"}
            or type(row.get("native_requested")) is not bool
        ):
            return None
        detail = {"outcome": row["outcome"], "native_requested": row["native_requested"]}
    elif event == "phase_timing" and row.get("correlation_source") == "server":
        side, stages = "native", NATIVE
        if (
            not isinstance(row.get("delivery"), str)
            or row.get("delivery") not in {"sent", "canceled", "failed", "unobserved"}
            or row.get("execution_settled") is not True
            or type(row.get("execution_settled_after_delivery")) is not bool
        ):
            return None
        detail = {
            "delivery": row["delivery"],
            "late_execution": row["execution_settled_after_delivery"],
        }
    else:
        return None
    values = {}
    for stage in stages:
        value = row.get(stage + "_ms")
        if value is not None:
            try:
                if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                    return None
            except OverflowError:
                return None
            values[stage + "_ms"] = value
    if "total_ms" not in values:
        return None
    return row["correlation"], side, {**detail, **values}


def percentile(values, fraction):
    position = (len(values) - 1) * fraction
    lower = int(position)
    return values[lower] + (values[math.ceil(position)] - values[lower]) * (position - lower)


def summarize(paths, max_traces=10000):
    if type(max_traces) is not int or max_traces <= 0:
        raise ValueError("max_traces must be a positive integer")
    traces = {}
    ignored = overflow = duplicates = 0
    for path in paths:
        for row in records(path):
            parsed = normalized(row)
            if parsed is None:
                ignored += 1
                continue
            token, side, value = parsed
            if token not in traces and len(traces) >= max_traces:
                overflow += 1
                continue
            trace = traces.setdefault(token, {})
            if side in trace:
                duplicates += 1
                trace["ambiguous"] = True
            else:
                trace[side] = value
    paired = server_only = native_only = late = ambiguous = 0
    metrics = {}
    outcomes = {"returned": 0, "raised": 0, "canceled": 0}
    deliveries = {"sent": 0, "canceled": 0, "failed": 0, "unobserved": 0}
    for trace in traces.values():
        if trace.get("ambiguous"):
            ambiguous += 1
            continue
        server, native = trace.get("server"), trace.get("native")
        if server is not None and native is not None and server["native_requested"]:
            paired += 1
        elif server is not None:
            server_only += 1
        else:
            native_only += 1
        if server is not None:
            outcomes[server["outcome"]] += 1
        if native is not None:
            deliveries[native["delivery"]] += 1
            late += int(native["late_execution"])
        for side, row in (("server", server), ("native", native)):
            if row is not None:
                for name, value in row.items():
                    if name.endswith("_ms"):
                        metrics.setdefault(side + "." + name, []).append(value)
    summary = {}
    for name, values in sorted(metrics.items()):
        values.sort()
        summary[name] = {
            "samples": len(values),
            "p50": percentile(values, 0.5),
            "p95": percentile(values, 0.95),
            "max": values[-1],
        }
    return {
        "schema_version": 1,
        "paired": paired,
        "server_only": server_only,
        "native_only": native_only,
        "late_execution": late,
        "ambiguous_traces": ambiguous,
        "duplicate_records": duplicates,
        "ignored_lines": ignored,
        "overflow_records": overflow,
        "server_outcomes": outcomes,
        "native_deliveries": deliveries,
        "phase_ms": summary,
        "interpretation": "Per-process durations; clocks are not synchronized. Native import is a measured subset of handler. Server response wait overlaps native work. Missing phases are unknown, never zero. Returned means a response, not successful Unity work.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logs", type=Path, nargs="+")
    parser.add_argument("--max-traces", type=int, default=10000)
    args = parser.parse_args()
    print(json.dumps(summarize(args.logs, args.max_traces), indent=2))


if __name__ == "__main__":
    main()
