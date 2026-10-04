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
