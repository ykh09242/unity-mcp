# Unity reference evidence collection

The internal collector exports native evidence for the server reference graph. It
does not own user databases, source grants, declarations or external repository
access. The public transport and automatic server reconciliation are separate layers.

## Identity and change history

The Editor instance identity survives a managed domain reload through `SessionState`.
A different Editor session gets its own identity. The project identity comes from the
existing project identity service. The server combines these with its authenticated
user identity; callers cannot choose another graph owner through collection fields.

An asset postprocessor observes imports, moves and deletions, including script
changes made through existing tools. Reads never invoke `Refresh`, reimport, save
assets or compile scripts. Each consumer keeps its own `(epoch, revision)` cursor:
reading changes does not acknowledge or remove another consumer's events.
Journal retention activates on the first graph request and remains active for that
Editor session; projects that never use the graph do not allocate import batches.

The journal survives normal domain reload. An Editor restart, invalid saved journal
or retention overflow changes its epoch and explicitly requires a fresh census. The
65,536-event retention budget limits journal memory, not project size or import batch
size. A larger batch is recovered through the complete census. No partial change list
is reported as complete. Compilation/import state and compilation failure are reported
separately from graph freshness.

## Pages and source coverage

- Inventory uses Unity's complete GUID census, without a total asset ceiling. Pages
  contain up to 1,000 candidates. Consumers can share its immutable GUID list while
  resolving current metadata and replaying subsequent journal changes independently.
- Direct dependencies come from `AssetDatabase.GetDependencies(path, false)`. Their
  pages preserve all targets and reject a changed generation. Interleaved consumers
  can reconstruct an evicted dependency array without losing their continuation.
- Resource keys follow Unity's `Resources` path convention. Addressables addresses,
  GUID keys, labels and implicit entries use the installed package through reflection.
  A missing/unsupported package is explicit coverage information. Reading default
  settings bypasses the legacy-migrating getter, preserving stored configuration.
  The package's modification event also invalidates catalog mappings before a save
  or import. Its observer coalesces changes and detaches before reload or shutdown.
- C# and Unity YAML source bytes use 64-KiB pages, with file revision checks before
  and after reading. There is no total source-size ceiling. Binary serialized assets
  report unavailable serialized-string coverage instead of returning empty source.
  File handles close within each page. Paths are resolved from the active Editor
  project or registered package, and linked filesystem paths are refused.
- Native inventory/dependency arrays and settings references are released after idle
  retention. Expired inventory continuations request a fresh census explicitly.

Collection has no application-specific folder names or asset naming rules. Optional
exclusions belong to the requesting user's graph policy, so one consumer's exclusions
cannot change another consumer's inventory. Hidden/import-ineligible paths are omitted.

This evidence does not prove that an asset is unused. Dynamic code, binary serialized
strings, compilation errors and unsupported package APIs remain coverage limitations.
Existing live mutation/reference guards remain authoritative for destructive actions.

## Verification

The native fixtures cover independent/interleaved readers, import/move/delete history,
GUID/dependency pages, large C# source continuation, generic folder admission, retention
recovery, Addressables configuration preservation and actual domain reload. Windows
Editor execution and the license-free win/osx/linux compilation matrix are reported
separately; compilation is not an OS-specific Editor runtime test.

Primary API references: [direct dependencies](https://docs.unity3d.com/ScriptReference/AssetDatabase.GetDependencies.html),
[import callbacks](https://docs.unity3d.com/ScriptReference/AssetPostprocessor.OnPostprocessAllAssets.html),
and [Addressables entry lookup](https://docs.unity.cn/Packages/com.unity.addressables%402.7/api/UnityEditor.AddressableAssets.Settings.AddressableAssetSettings.FindAssetEntry.html).
