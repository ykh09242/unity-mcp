"""Aggregate local scalar phase logs. Does not connect, execute work, or emit trace IDs."""

import argparse
import json
import math
from pathlib import Path
import re

SERVER = ("admission", "preflight", "send", "response", "return", "total")
NATIVE = ("queue", "handler", "import", "prepare", "serialization", "send", "total")
LINE_BYTES = 16384


CAPTURE_COUNTS = (
    "offered",
    "written",
    "write_failed",
    "dropped",
    "pending",
    "drop_queue",
    "drop_startup",
    "drop_closed",
    "drop_shutdown",
    "checkpoint_write_failed",
)
DROP_COUNTS = ("drop_queue", "drop_startup", "drop_closed", "drop_shutdown")


def _finite_nonnegative(value):
    try:
        return type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def _retention(row):
    counts = (
        "entries_scanned",
        "owned_sessions",
        "active_sessions",
        "retained_bytes",
        "deleted_sessions",
        "cleanup_errors",
    )
    if (
        not isinstance(row, dict)
        or type(row.get("scan_complete")) is not bool
        or not all(type(row.get(key)) is int and row[key] >= 0 for key in counts)
        or not _finite_nonnegative(row.get("oldest_retained_age_seconds"))
    ):
        return None
    return {key: row[key] for key in (*counts, "scan_complete", "oldest_retained_age_seconds")}


def capture_record(row):
    if (
        type(row.get("schema_version")) is not int
        or row["schema_version"] != 1
        or not isinstance(row.get("session"), str)
        or not re.fullmatch("[a-f0-9]{32}", row["session"])
        or type(row.get("checkpoint")) is not int
        or row["checkpoint"] <= 0
        or type(row.get("sample_every")) is not int
        or row["sample_every"] <= 0
        or type(row.get("closed")) is not bool
        or not _finite_nonnegative(row.get("elapsed_ms"))
        or not all(type(row.get(key)) is int and row[key] >= 0 for key in CAPTURE_COUNTS)
    ):
        return None
    if row["offered"] != sum(
        row[key] for key in ("written", "write_failed", "dropped", "pending")
    ) or row["dropped"] != sum(row[key] for key in DROP_COUNTS):
        return None
    return {
        **{
            key: row[key]
            for key in (
                *CAPTURE_COUNTS,
                "session",
                "checkpoint",
                "sample_every",
                "closed",
                "elapsed_ms",
            )
        },
        "retention": _retention(row.get("retention")),
    }


class CaptureCoverage:
    """Validate bounded checkpoint history, independently of log input order."""

    def __init__(self, limit):
        self.limit = limit
        self.checkpoints = {}
        self.latest = {}
        self.ambiguous = set()
        self.overflow = 0

    def add(self, row):
        session = row["session"]
        key = (session, row["checkpoint"])
        previous = self.checkpoints.get(key)
        if previous is not None:
            if previous != row:
                self.ambiguous.add(session)
            return
        if len(self.checkpoints) >= self.limit:
            self.overflow += 1
            return
        self.checkpoints[key] = row
        latest = self.latest.get(session)
        if latest is None or latest["checkpoint"] < row["checkpoint"]:
            self.latest[session] = row

    def _validate_history(self):
        previous = {}
        monotonic = tuple(key for key in CAPTURE_COUNTS if key != "pending") + ("elapsed_ms",)
        for (session, _), newer in sorted(self.checkpoints.items()):
            older = previous.get(session)
            if older is not None and (
                any(newer[key] < older[key] for key in monotonic)
                or newer["sample_every"] != older["sample_every"]
                or (older["closed"] and not newer["closed"])
            ):
                self.ambiguous.add(session)
            previous[session] = newer

    def summarize(self, traces, incomplete):
        self._validate_history()
        valid = {key: row for key, row in self.latest.items() if key not in self.ambiguous}
        selected = after = unknown = duplicate = 0
        seen = set()
        for trace in traces.values():
            row = trace.get("server")
            if row is None or trace.get("ambiguous"):
                continue
            session, sequence = row.get("capture_session"), row.get("capture_sequence")
            checkpoint = valid.get(session)
            if checkpoint is None:
                unknown += 1
            elif (session, sequence) in seen:
                duplicate += 1
            else:
                seen.add((session, sequence))
                if sequence <= checkpoint["written"] + checkpoint["write_failed"]:
                    selected += 1
                else:
                    after += 1
        counts = {
            key: sum(row[key] for row in valid.values()) if valid else None
            for key in CAPTURE_COUNTS
        }
        offered, written = counts["offered"], counts["written"]
        complete = bool(valid) and not any(
            (incomplete, self.overflow, self.ambiguous, after, unknown, duplicate)
        )
        complete = complete and all(row["closed"] for row in valid.values())
        complete = (
            complete
            and counts["pending"] == counts["dropped"] == counts["write_failed"] == 0
            and selected == written
        )
        return {
            "sessions": len(valid),
            **counts,
            "selected_samples_in_window": selected,
            "samples_after_checkpoint": after,
            "samples_without_checkpoint": unknown,
            "duplicate_sample_sequences": duplicate,
            "ambiguous_sessions": len(self.ambiguous),
            "checkpoint_overflow": self.overflow,
            "capture_fraction": written / offered if offered else None,
            "selected_log_fraction": selected / written
            if written and selected <= written
            else None,
            "complete": complete,
            "windows": [
                {
                    key: row[key]
                    for key in (
                        *CAPTURE_COUNTS,
                        "sample_every",
                        "elapsed_ms",
                        "closed",
                        "retention",
                    )
                }
                for row in valid.values()
            ],
            "interpretation": "Cumulative counters through each session's last saved checkpoint, not all invocations. New sessions reset counters. Open windows and missing checkpoints have an unknown tail. Capture fraction concerns sampled offers; selected-log fraction concerns written samples still present in the chosen files. Retention scans may overlap directories and must not be summed.",
        }


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
        session, sequence = row.get("capture_session"), row.get("capture_sequence")
        if (
            isinstance(session, str)
            and re.fullmatch("[a-f0-9]{32}", session)
            and type(sequence) is int
            and sequence > 0
        ):
            detail.update(capture_session=session, capture_sequence=sequence)
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
    capture = CaptureCoverage(max_traces)
    ignored = overflow = duplicates = 0
    for path in paths:
        for row in records(path):
            if isinstance(row, dict) and row.get("event") == "phase_capture_coverage":
                checkpoint = capture_record(row)
                if checkpoint is None:
                    ignored += 1
                else:
                    capture.add(checkpoint)
                continue
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
        "capture_coverage": capture.summarize(traces, bool(overflow or ambiguous or ignored)),
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
