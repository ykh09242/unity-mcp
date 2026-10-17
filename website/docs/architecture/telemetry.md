# Unity MCP (ykh09242) Telemetry

This fork has no telemetry destination configured by default. Without a validated, explicit `UNITY_MCP_TELEMETRY_ENDPOINT`, the server does not start a telemetry worker or create telemetry persistence. It does not send telemetry to Coplay.

## Optional collection

Telemetry requires both an explicitly configured endpoint and the existing enabled gate. An endpoint alone does not override a disabled setting. The endpoint must pass the server's HTTP/HTTPS URL validation, which rejects localhost destinations. HTTPS is recommended; no fork endpoint is advertised.

When enabled, telemetry uses anonymous installation/session identifiers and constrained built-in tool/action labels, timing, success status, and version/platform metadata. Free-form custom actions, complete tool arguments and results, script contents, project paths, and exception payloads are excluded. See [Security](https://github.com/ykh09242/unity-mcp/blob/beta/SECURITY.md) for logging and telemetry safeguards.

## Disable telemetry

The existing opt-out controls remain available:

```bash
export DISABLE_TELEMETRY=true
# Alternatively:
export UNITY_MCP_DISABLE_TELEMETRY=true
export MCP_DISABLE_TELEMETRY=true
```

For an MCP client that launches the server, set the environment variable in its configuration:

```json
{
  "env": {
    "DISABLE_TELEMETRY": "true"
  }
}
```

These controls disable collection even when an endpoint is supplied.

## Operator responsibility

A privately configured endpoint is operated by whoever supplies it. Review that operator's privacy and retention policy before enabling telemetry. This fork makes no claims about an external operator's retention, access controls, or use of submitted data.

For questions about this fork, use [fork issues](https://github.com/ykh09242/unity-mcp/issues). For sensitive reports, follow the [security policy](https://github.com/ykh09242/unity-mcp/blob/beta/SECURITY.md).

## Local sampled command phase diagnostics

The Editor's existing **command log** opt-in also enables local phase diagnostics. Within each Editor domain, the first admitted command and then every 64th admission are sampled (`sample_every=64`); disabled logging does not advance this counter. Sampling is independent of command contents and outcomes. The generated correlation token is local to this diagnostic record: wire/request IDs, arguments, results, action strings, exceptions, traceparent and tracestate are neither retained nor forwarded. These records are never sent to server telemetry.

Each `phase_timing` record may contain `queue_ms` (native transport admission to handler start), `handler_ms` (handler entry through observed settlement in the existing completion path), `prepare_ms` (response freezing or legacy JSON parsing), `serialization_ms` (final response/envelope JSON and UTF8 preparation), and `send_ms` (local framing/compression/write including send-lock wait). Legacy async handler-internal serialization and scheduling of its completion continuation remain inside handler time. Missing stages are omitted; unfinished stages are named in `incomplete_stages`. `delivery` describes local writes only, not peer receipt or command success. A canceled reply can precede real mutation settlement: `execution_settled_after_delivery` discloses this and `total_ms` ends at delivery, so phase values need not sum to total. Direct dispatcher calls have no observed transport send. Clock/sink failure or ambiguous overlapping observations can omit a sampled record; aggregation is descriptive, not a completeness guarantee. Sampling adds no command retry, deadline, output serialization, payload copy or background queue. Existing legacy command log fields and their duration meaning remain unchanged.

Allocation, object-layout and paired disabled-overhead measurements are quantitatively unavailable. Diagnostic costs remain unquantified; no zero-allocation, zero-overhead, measured acceptable regression or performance-improvement claim is made.

For offline aggregation, use a copied local `mcp.log` file. The bounded standard-library example below refuses files larger than 1 MiB (including any last append beyond the rotation threshold), never prints raw rows, rejects malformed or non-finite/out-of-range timing fields, and returns **sampled local** count/sum/mean/max. It does not estimate all-command latency, per-request latency, percentiles or end-to-end network time. Missing phases contribute no observation; an actual measured zero does. Ordinary legacy command-log rows are ignored.

```python
# CORE13_OFFLINE_EXAMPLE
import json
import math

LIMIT = 1024 * 1024
PHASES = ("queue_ms", "handler_ms", "prepare_ms", "serialization_ms", "send_ms", "total_ms")
FIELDS = set(PHASES) | {"event", "sample_every", "correlation", "transport", "delivery", "execution_settled", "execution_settled_after_delivery", "incomplete_stages"}


def summarize(data):
    if len(data) > LIMIT:
        raise ValueError("Log exceeds offline size limit")
    try:
        text = data.decode("utf-8")
    except UnicodeError:
        raise ValueError("Log is not UTF-8") from None
    stats = {key: {"count": 0, "sum_ms": 0.0, "max_ms": 0.0} for key in PHASES}
    accepted = invalid = 0
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except (ValueError, RecursionError):
            invalid += 1
            continue
        if type(row) is not dict or row.get("event") != "phase_timing":
            continue
        values = {key: row[key] for key in PHASES if key in row}
        if (
            set(row) - FIELDS
            or type(row.get("sample_every")) is not int
            or row["sample_every"] != 64
            or "total_ms" not in values
            or row.get("transport") not in ("direct", "websocket", "stdio")
            or row.get("delivery") not in ("unobserved", "sent", "canceled", "failed")
            or any(type(value) not in (int, float) or value < 0 or value > 1e12 or not math.isfinite(value) for value in values.values())
        ):
            invalid += 1
            continue
        accepted += 1
        for key, value in values.items():
            item = stats[key]
            item["count"] += 1
            item["sum_ms"] += value
            item["max_ms"] = max(item["max_ms"], value)
    for item in stats.values():
        item["mean_ms"] = item["sum_ms"] / item["count"] if item["count"] else None
    return {"sample_every": 64, "sampled_rows": accepted, "invalid_rows": invalid, "phases": stats}


# Read at most LIMIT+1 bytes, then print only the aggregate:
# with open("copied-mcp.log", "rb") as stream:
#     print(json.dumps(summarize(stream.read(LIMIT + 1)), allow_nan=False))
```
