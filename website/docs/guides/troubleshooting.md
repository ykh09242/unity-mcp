---
id: troubleshooting
slug: /guides/troubleshooting
title: Troubleshooting
sidebar_label: Troubleshooting
description: Diagnose installation, authentication, bridge, target, consent, and timeout failures without destructive resets.
---

# Troubleshooting

Work from package import to server, bridge, client, then target. Do not delete project caches or disable safeguards as a first step. [Install And Connect](../getting-started/install.md) is the current setup path.

## Connection checklist

| Symptom | Check and next action |
|---|---|
| Unity is in Safe Mode / package compile errors | Resolve compilation first. MCP cannot operate before its package loads. Capture the first relevant error and Editor/package versions. |
| Local server fails to launch | Verify Unity can find Python and `uv`/`uvx`; inspect `Library/MCPForUnity/Logs/server-launch-<port>.log`. A shell PATH may differ from a Hub-launched Editor's PATH. |
| Port is occupied | Identify its owner; stop only a process you own or choose a free port and reconfigure. The plugin does not terminate unrelated processes. |
| HTTP 401 | Token missing/stale/duplicated: start the server, reconfigure clients, and reconnect. Server restarts rotate tokens. |
| HTTP 403 | Browser-origin headers are unsupported; use a native client. |
| HTTP 415 | Mutating requests need `Content-Type: application/json`. |
| Hosted authentication fails | HTTPS/WSS proxy, private backend assertion, validator contract, API key and user catalog; [remote auth](./remote-server-auth.md). |
| Client has no tools | Review its config format and enablement; reload/reconnect, then check Editor toggles. |
| Server reachable, no Editors | Confirm bridge connection, matching endpoint/transport and no Unity compile/reload failure. |
| Multiple Editors | Use a discovered `Name@hash` per request; [routing](./multi-instance.md). |
| Tool present, operation denied | Review [consent](./security.md), permitted roots, values and work budgets. Visibility is not authorization. |

`GET /health` checks only the process. Verify an Editor by reading `mcpforunity://instances` and its project info. Never share token-file contents, API keys or complete private payloads in diagnostics.

## "No Unity Instances Found"

Check bridge status and read `mcpforunity://instances`. A domain reload can temporarily remove a connection; let compilation/reconnection finish and rediscover. After transport/config changes, reconnect the client. Do not send a write to another Editor merely because the intended one is temporarily unavailable.

## Timeouts and budget errors

Use smaller pages, fewer batch commands, lower capture resolution, or supported asynchronous job polling. Validation/budget rejection should not be worked around by bypassing checks. An error can follow partial writes in best-effort operations, and a timeout after dispatch can leave work running or applied. Inspect actual state before repeating mutations or starting another test/build job; do not assume failure rolled back the project.

## Package Manager: "Error when executing git command" / "not in a git directory"

Check `git --version` in a terminal and ensure Unity inherited the correct PATH. Verify the full [stable package URL](../getting-started/install.md#1-install-the-unity-package). If Git reports an ownership/safe-directory error, confirm who owns the exact project before trusting it; do not trust every repository globally. Keep the reported Git error for diagnosis.

Original report: [@Cherrymocha, upstream #1216](https://github.com/CoplayDev/unity-mcp/issues/1216).

## WSL2: Connecting Claude Code (Linux) to Unity (Windows)

Windows and WSL may have different network namespaces, executable paths and token locations. Start with the same-machine configurator when possible. If bridging them, use a trusted tunnel or deliberately scoped private networking, provide the current local token explicitly, and update it after every server restart. Do not expose an unauthenticated all-interface port or broadly open the firewall to fix discovery.

The earlier upstream port-forwarding recipe predates this fork's per-launch token contract. Its exact network commands are not a current default setup procedure. See [local authentication](./security.md#local-http-authentication) and [manual client configuration](../getting-started/clients.md#manual-client-configuration).

Historical report: [@aollivier82, upstream #712](https://github.com/CoplayDev/unity-mcp/issues/712).

## Codex: `resources/read failed: unknown MCP server`

`mcpforunity://` names a resource, not the client's server registration. List resources first and use the server key exactly as returned by the client. Do not assume capitalization or an exposed tool prefix is the discovery key. Supply resource target metadata when supported; see [routing](./multi-instance.md).

Historical report: [@drewclifton, upstream #1220](https://github.com/CoplayDev/unity-mcp/issues/1220).

## Unity takes focus while tests are running

Set `UNITY_MCP_DISABLE_FOCUS_NUDGE=1` in the **Python server's** environment and restart it to disable local nudges. For stdio, set the server's `env`; for shared HTTP, set the launch environment. Nudges are bounded, require selected-project provenance, and are unavailable in hosted mode. A nudge does not certify that a stuck test recovered.

Related historical report: [upstream #1407](https://github.com/CoplayDev/unity-mcp/issues/1407).

## Client executable paths

If Unity cannot find an executable that a terminal can, use the Editor's path selector with its verified absolute path and restart/reconfigure the client. This commonly affects GUI launch environments and version-manager installations. Use the vendor's current installation guidance for repairing their client; avoid copying an old global uninstall/reinstall recipe into an unrelated environment.

See [uv setup](./uv-setup.md), [client configurators](./client-configurators.md), and [Claude Code CLI](./claude-code-cli.md).

## Historical client and Editor reports

These upstream reports are retained as diagnostic context, not current fork compatibility guarantees or instructions to delete `Library`, replace DLLs, or remove lockfiles. Match the exact versions/error before changing dependencies, and back up the project first.

### macOS: Claude CLI fails to start (dyld ICU library not loaded)

Historical Homebrew/ICU linkage report. Repair the affected client installation using its current vendor guidance, then select the working executable in Unity.

### DLL reference mismatch with Unity AI Assistant package

Historical assembly-version conflict: [@rkroska, upstream #557](https://github.com/CoplayDev/unity-mcp/issues/557). Do not assume dropping another DLL into `Assets/Plugins` resolves the current package graph.

### Unity 6.5: Editor hangs on load and the bridge never connects

Historical pre-release package report: [@100yenadmin, upstream #1219](https://github.com/CoplayDev/unity-mcp/issues/1219). Failure before package load is not proof of an MCP defect; isolate the affected packages in a recoverable copy rather than deleting caches blindly.

## Report a reproducible fork issue

Include the fork package/server versions, installed Git revision, OS/Editor/client, transport, expected versus actual behavior, and minimal sanitized errors. State whether failure happened before dispatch, after mutation, or while polling a job. [Open a fork issue](https://github.com/ykh09242/unity-mcp/issues); use [private security reporting](https://github.com/ykh09242/unity-mcp/blob/beta/SECURITY.md) for vulnerabilities.
