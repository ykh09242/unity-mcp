---
id: uv-setup
slug: /guides/uv-setup
title: Install or Repair uv + Python
sidebar_label: uv + Python Setup
description: Install or repair uv and Python — the runtime MCP for Unity needs to launch the Python server from Cursor, VS Code, Windsurf, Rider, and other uv-based clients.
---

# Install or Repair uv + Python

The key to configuring MCP with **Cursor, VS Code, Windsurf, and Rider is [`uv`](https://docs.astral.sh/uv/)**.

- `uv` is a fast Python package manager used to install and run the Unity MCP Server (`mcp-for-unity`).
- **How it's used:** your MCP client config points to `command: uvx` with args like `--python ">=3.11" --from "<mcpServerSource>" mcp-for-unity --transport stdio`. Replace `<mcpServerSource>` with the full-commit GitHub archive URL from the installed Unity package's `package.json`; see [installation](../getting-started/install.md). The client invokes `uvx` directly to launch the server without a Git checkout. Git is still needed for the Unity Package Manager Git installation.
- **Why it matters:** if `uv` isn't installed or on PATH, Cursor / Windsurf / VS Code can't start the server. Inspect the dependency status in the Unity MCP (ykh09242) setup window.
- **Detection / override:** the Unity MCP (ykh09242) window auto-detects `uv` in common locations and on PATH. Use **Browse** next to the **UVX Path** override to choose your installed executable when automatic discovery fails.

:::tip When in doubt, restart your client
Clients like Claude Code or JetBrains Rider can get confused if you switch from `http` to `stdio` (or vice versa). If they say **"No Unity Instances found"**, restart the client so it picks up the new configuration.
:::

## Requirements

You need **Python 3.11+** and the **`uv`** package manager. Starting with fork release **1.1.4**, Python 3.10 is no longer supported. Generated launch commands request a compatible interpreter explicitly.

### Verify

```bash
python3 --version   # should be 3.11+
uv --version        # should print a version like "uv 0.x"
```

## Install Python

With uv already installed, `uv python install 3.11` installs the minimum supported interpreter. After updating the Unity package, regenerate existing stdio client configurations so they use the new server source and Python request. Custom commands that explicitly select Python 3.10 must be updated; do not patch `enum` or change unrelated environment variables to work around `StrEnum` import errors.

**macOS:**

```bash
# Option A: Official installer (recommended)
# Download from https://www.python.org/downloads/

# Option B: Homebrew example (choose a Python version supported by this project)
brew install python@3.12
```

**Windows:**

```powershell
# Official installer (recommended)
# Download from https://www.python.org/downloads/windows/
```

## Install uv

**macOS / Linux / WSL:**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
# or Homebrew on macOS
brew install uv
```

**Windows PowerShell:**

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
# or
winget install --id=astral-sh.uv -e
```

## Common uv locations

| OS | Path |
|---|---|
| **macOS** | `/opt/homebrew/bin/uv`, `/usr/local/bin/uv`, `~/.local/bin/uv` |
| **Linux** | `/usr/local/bin/uv`, `/usr/bin/uv`, `~/.local/bin/uv` |
| **Windows** | `%LOCALAPPDATA%/Programs/Python/Python3xx/Scripts/uv.exe` |

## Unity MCP (ykh09242) window behavior

- The setup window displays the detected uv version and dependency details, with **Install uv** and **uv Downloads** actions when applicable.
- Use **Browse** next to **UVX Path** to save an executable override. **Clear** removes that override so discovery can run again. Regenerate the selected client's configuration after changing its launch path.
- On macOS, Unity launched from Finder may not inherit your PATH. Setting the `uv` location here is the easiest fix.

## Notes and gotchas

- **macOS GUI apps don't inherit your shell startup files.** PATH may differ from Terminal. Set `uv` via the MCP window to avoid PATH issues.
- **Windows vs WSL:** if you installed `uv` inside WSL only, Windows-native Unity can't see it. Install `uv` on Windows, or use the MCP window to point to a Windows `uv.exe`.
- **Custom locations:** the executable override persists across sessions. Revisit **UVX Path** after moving or reinstalling uv.

## Locate the active server environment

The installed Unity package selects an immutable server archive through
`MCPForUnity/package.json`'s `mcpServerSource`. Generated commands run
`mcp-for-unity` with `uvx --from` and a compatible Python request. uv manages the
disposable tool environment in its cache; its location is not a fixed
`UnityMcpServer/src` directory. See [uv tool environments](https://docs.astral.sh/uv/concepts/tools/#tool-environments).

Inspect the generated launch command and the installed package's source pin when
diagnosing which server will run. A development source or command override changes
that selection; review any override before comparing it with the packaged release.
Repository contributors use the separate environment described in
[development setup](../contributing/dev-setup.md).

## Diagnose and repair startup

1. Check `uv --version`, the detected executable path and the Python request in the
   generated launch command. Regenerate older client configurations after upgrading
   the Unity package.
2. Stop the affected local server or client connection, then run its generated
   command in a terminal to inspect the startup error. Keep its source pin and
   transport arguments unchanged; distinguish download/interpreter errors from
   authentication or Editor connection failures.
3. For a suspected cache problem, add `--refresh` immediately after `uvx` in that
   command. This revalidates cached data while retaining the selected source.
   If the package cache still needs rebuilding, clear only the fork distribution
   and rerun the same command:

```bash
uv cache clean ykh09242-unity-mcp-server
```

4. Reconnect the client after the server starts. For local HTTP, follow the
   [client configuration](../getting-started/clients.md) guidance if its token or
   endpoint changed. Verify the Editor through `mcpforunity://instances`.

The cache commands above follow [uv's cache troubleshooting guidance](https://docs.astral.sh/uv/concepts/cache/).

## One-click installer failure handling

The setup wizard downloads the official installer into a unique temporary
directory and checks the downloader's exit status before executing that file.
Download failures never invoke the installer. Script execution failures are
reported separately, and the owned temporary installer directory is removed after
the attempt. Windows uses PowerShell's terminating download errors and explicit
exit status; macOS and Linux check curl's download result before invoking `sh`.
The confirmation describes both stages. After a successful attempt, refresh the
dependency check and inspect the detected uv path.
