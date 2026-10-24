---
id: roslyn
slug: /guides/roslyn
title: Roslyn Script Validation (Advanced)
sidebar_label: Roslyn Validation
description: Enable strict C# validation that catches undefined namespaces, types, and methods before the script reaches the Unity compiler.
---

# Roslyn Script Validation

By default, MCP for Unity uses a fast structural validator for scripts the LLM generates. For **strict** validation that catches undefined namespaces, types, and methods at write time — without a full Unity compile — install the optional Roslyn DLLs.

Most users don't need this. Enable it when:

- You're letting the LLM write a lot of unsupervised C# and want stricter feedback loops
- You're seeing recurring compile errors that survive the structural validator
- You're building custom tools that depend on accurate symbol resolution

## One-click installer (recommended)

1. Open **Window → Unity MCP (ykh09242)**.
2. Scroll to the **Runtime Code Execution (Roslyn)** section in the Scripts / Validation tab.
3. Click **Install Roslyn DLLs**.

The installer downloads the required NuGet packages, places the DLLs in `Assets/Plugins/Roslyn/`, and adds `USE_ROSLYN` to Scripting Define Symbols.

Before downloading, the installer checks loaded assembly identities across the Editor. Compatible assemblies provided by another package are reused without copying or changing that package. The supported set is `Microsoft.CodeAnalysis` and `Microsoft.CodeAnalysis.CSharp` 4.12.0, `System.Collections.Immutable` and `System.Reflection.Metadata` 8.0.0, and `System.Runtime.CompilerServices.Unsafe` 6.0.0, with their official strong-name identities. Other loaded versions require resolving the supplying package and restarting Unity; the failure message identifies the assembly and its location. A duplicate in the installer folder must also be removed explicitly, or uninstalled if MCP owns it.

Downloads show byte progress and a Cancel button. Cancellation aborts the active request, disposes it and discards private staging before publication. A transfer can continue beyond 30 seconds while receiving data: the default limits are 90 seconds without initial data and 120 seconds without further progress. There is no total deadline by default. Transient connection failures and HTTP 408, 429, 500, 502, 503 and 504 responses receive at most three GET attempts with increasing delays. Invalid archives and assembly identities are not retried. Programmatic callers may set `RoslynInstaller.InstallAsync`'s optional `totalDownloadTimeout` to bound each dependency's download and retries.

All downloaded DLLs are validated before the owned file set is published transactionally. A failed download or compatibility check preserves the previous installed set. Unowned or modified files are preserved and reported instead of overwritten.

You can also trigger it from the menu: **Window → Unity MCP (ykh09242) → Install Roslyn DLLs**.

## Manual install (if the installer isn't available)

1. Install [NuGetForUnity](https://github.com/GlitchEnzo/NuGetForUnity).
2. Open **Window → NuGet Package Manager**.
3. Install:
   - `Microsoft.CodeAnalysis.Common` v4.12.0
   - `Microsoft.CodeAnalysis.CSharp` v4.12.0
   - `System.Collections.Immutable` v8.0.0
   - `System.Reflection.Metadata` v8.0.0
   - `System.Runtime.CompilerServices.Unsafe` v6.0.0
4. Add `USE_ROSLYN` to **Player Settings → Scripting Define Symbols**.
5. Restart Unity.

## Manual DLL install (no NuGetForUnity)

1. Download `Microsoft.CodeAnalysis.CSharp.dll` and its dependencies from [NuGet.org](https://www.nuget.org/packages/Microsoft.CodeAnalysis.CSharp/).
2. Place DLLs in `Assets/Plugins/Roslyn/`.
3. Ensure .NET compatibility settings are correct for your Unity version.
4. Add `USE_ROSLYN` to Scripting Define Symbols.
5. Restart Unity.

## Verifying it's active

After restart, the MCP for Unity status panel shows **Roslyn: enabled** under the Scripts section. The `validate_script` tool now performs full semantic analysis rather than the structural pass.

## Disabling

Remove `USE_ROSLYN` from Scripting Define Symbols. The plugin falls back to structural validation; the DLLs can stay in `Assets/Plugins/Roslyn/` or be removed.
