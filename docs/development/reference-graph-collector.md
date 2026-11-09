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

The server's per-asset `MAX_SOURCE_BYTES` and `MAX_DEPENDENCY_ITEMS` operator
limits reject that asset before retaining excessive input. The previous evidence
remains stale, diagnostics name the exceeded limit and its environment setting,
and collection continues with later assets. Failed assets use the existing retry
backoff; a source change, explicit retry, or changed operator item limits makes
them eligible again. Attempt-wide page, item, byte and time exhaustion still
aborts the attempt with its durable checkpoints. Ignore-policy failures remain
fail-closed.

This evidence does not prove that an asset is unused. Dynamic code, binary serialized
strings, compilation errors and unsupported package APIs remain coverage limitations.
Existing live mutation/reference guards remain authoritative for destructive actions.

## Preparation and responsiveness

The cancellation-aware command path stages cold `inventory` and `dependencies`
preparation on Editor updates. At most two preparations are admitted. Matching
epoch/revision/source keys share one immutable result while each caller retains
its own cancellation and page cursor. The final consumer's cancellation retires
unfinished work; reload and quit retire pending preparations. Synchronous direct
callers retain the compatibility path.

`AssetDatabase.FindAssets` and `AssetDatabase.GetDependencies` remain indivisible
Editor-thread calls. Following that census, conversion, filtering, merge ordering,
deduplication and result copying use slices of at most 8,192 elementary operations
with a 4 ms time target. One conversion, native lookup or array allocation cannot
be interrupted. The target is not a worst-case callback guarantee. Epoch, revision
and idle state are checked between slices and before publishing a snapshot.

Native status advertises `preparation_version: 1` and `preparation`, with at most
two active records and one recent record per kind. Records expose stage, input and
operation counts, consumers, Editor update count, native-call duration, cumulative
preparation duration, maximum observed callback duration and elapsed duration.
They also expose the operation cap, time target and
`native_call_interruptible: false`; they retain no source paths or arrays.
Completed snapshots keep the existing ten-minute idle expiry and continuation
identity rules. Measure cold first-page latency separately from page transfer when
provisioning large projects.

## Verification

The native fixtures cover independent/interleaved readers, import/move/delete history,
GUID/dependency pages, large C# source continuation, generic folder admission, retention
recovery, Addressables configuration preservation and actual domain reload. Windows
Editor execution and the license-free win/osx/linux compilation matrix are reported
separately; compilation is not an OS-specific Editor runtime test.

Primary API references: [direct dependencies](https://docs.unity3d.com/ScriptReference/AssetDatabase.GetDependencies.html),
[import callbacks](https://docs.unity3d.com/ScriptReference/AssetPostprocessor.OnPostprocessAllAssets.html),
and [Addressables entry lookup](https://docs.unity.cn/Packages/com.unity.addressables%402.7/api/UnityEditor.AddressableAssets.Settings.AddressableAssetSettings.FindAssetEntry.html).
