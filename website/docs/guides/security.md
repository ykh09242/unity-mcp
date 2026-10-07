---
id: security
slug: /guides/security
title: Security And Consent
sidebar_label: Security And Consent
description: Authenticate locally, grant narrow consent, protect credentials, and handle budgets and uncertain writes.
---

# Security And Consent

An MCP connection gives an assistant Editor capabilities. Use a recoverable project, keep changes under version control, and enable only the needed operations. Authentication, tool visibility, and consent are separate checks.

## Local HTTP authentication

Every local HTTP launch creates a fresh 256-bit token. MCP `/mcp`, REST `/api/*`, and plugin WebSocket `/hub/plugin` require `X-Unity-MCP-Token`; only `GET /health` is public. Health does not prove an authenticated client or connected Editor.

The token is atomically written to `~/.unity-mcp/auth/token-<port>` (Windows: `%USERPROFILE%\.unity-mcp\auth\token-<port>`). POSIX permissions are owner-only; Windows inherits the directory ACL. It is not printed or served over HTTP. Clean shutdown removes only that launch's token.

Start HTTP **before** configuring static-header clients. Their generated configs include the current token and need reconfiguration/reconnection after a server restart. Unity package **1.2.1** adds token-file helpers for verified Codex and Claude Code clients; after configuring once, reconnect if their automatic authentication recovery does not succeed. Unsupported CLIs should use stdio. Unity and native CLI already look up the current token on new connections/requests. See [client-specific behavior](../getting-started/clients.md). Keep token files and generated configs private; do not include their contents in Git, issues or screenshots.

`UNITY_MCP_LOCAL_AUTH_TOKEN_FILE` selects an absolute private token path. The server generates a new token rather than trusting an existing file. Automatic lookup sends credentials only to loopback; non-loopback clients need an explicit token/file/header. Local HTTP is unencrypted: use a trusted tunnel across machines, not an open listener/firewall rule as a default workaround.

Requests with `Origin` or `Sec-Fetch-Site` are rejected even with a token. Use native clients, not browser-origin fetches. POST/PUT/PATCH require `Content-Type: application/json`.

## Grant narrow Editor consent

Explicit consent is required for `execute_code`, `manage_script`, `execute_menu_item`, `manage_packages`, `manage_build`, `batch_execute`, and `blender_bridge`. Activating a tool group does not grant consent. Compiler-affecting asset/folder/metadata changes also require script consent.

The Blender Bridge uses one consent grant for all its operations, including inspection, Python execution, model import, and addon synchronization. Enable `blender_bridge` explicitly in the Editor's tool controls before using the bridge through MCP, CLI, or the Editor panel/menu. Older automatically enabled settings do not count as consent. Disabling the tool revokes the grant for subsequent calls, including existing sessions and batches.

Review the Editor's tool controls and operation-specific consent prompt. Do not enable all high-impact tools to fix a missing-tool error. A batch cannot bypass nested operations' consent or disabled-tool checks. Asset writes stay inside their permitted roots; link/junction rejection is a containment safeguard.

## Hosted services are a separate mode

Hosted requests use API keys and user-isolated plugin catalogs. Keep the backend private behind actual HTTPS/WSS, configure an HTTPS validator, and supply `--http-behind-tls-proxy`. This flag is an assertion, not TLS implementation.

Only hosted `GET /health` and `GET /api/auth/login-url` are public. Select an authorized Editor explicitly. Host-local REST control, file recovery/scanning, synchronization and test-focus helpers are unavailable. Follow [Remote Server Auth](./remote-server-auth.md) rather than exposing a local-token server publicly.

## Budgets, failures and retries

Requests/results, pending commands, polling, regex, pagination, captures, textures and imports have limits. Reduce page size, split work, or use supported async jobs. A budget/validation error is not permission to bypass checks.

A timeout after dispatch does **not** prove Unity stopped or rolled back. An error can also follow partial writes: best-effort property operations may apply valid fields before reporting invalid ones. Inspect scene/asset/job state before retrying mutations; there is no general rollback or exactly-once retry guarantee. Read-only script preparation also does not apply a change. Native-file handoffs require independently verified local provenance and exact byte/hash verification; fall back to direct Unity edits when those guarantees cannot be met.

## Credentials and telemetry

The fork has no default telemetry destination and does not automatically send events to Coplay. Telemetry needs an explicit endpoint and the enabled gate; `DISABLE_TELEMETRY`, `UNITY_MCP_DISABLE_TELEMETRY`, or `MCP_DISABLE_TELEMETRY` can opt out. See [Telemetry](../architecture/telemetry.md).

MCP authentication is distinct from optional asset-generation provider credentials. Invoking those integrations can send external requests; disabled telemetry does not disable provider traffic. Do not share API keys/private payloads in diagnostics. Use the [security policy](https://github.com/ykh09242/unity-mcp/blob/beta/SECURITY.md) for private vulnerability reports.
