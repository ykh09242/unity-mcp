# Changed-script diagnostics

`manage_script_quality` (the `testing` tool group) analyzes explicitly selected
current C# source using Unity's owning assembly context. It returns advice and
evidence without rewriting source, assets, serialization data or compiler settings.

## Optional setup

In **Advanced > Script Validation**, explicitly set up/update the analysis host,
then enable analysis for the Editor session. Setup requires a .NET SDK capable of
building `net10.0` and a .NET 10 runtime; first setup restores pinned packages.
The transactional installation lives under
`Library/MCPForUnity/ScriptDiagnosticsHost`. A shipped-source change requires an
explicit host update. Ordinary capability checks and analysis never install,
restore packages or run project MSBuild scripts.

The isolated host pins Roslyn Workspaces and CSharp CodeStyle **5.9.0**,
Microsoft.Unity.Analyzers **1.28.0**, and Microsoft.Bcl.AsyncInterfaces **10.0.1**.
Its lock file and third-party notices ship in `Editor/ScriptDiagnosticsHost~`;
Unity excludes that folder from its assembly compilation. The host does not load
arbitrary project analyzers, generators or code-fix providers. The allowlisted
analyzers and Unity suppressors run in the same pipeline. Thus an ordinary object
can receive `IDE0031`, while Unity-object null semantics are protected by Unity's
suppressor. This is independent of the older in-Editor `USE_ROSLYN` validation.

## Actions and selection

| Action | Required options / result |
| --- | --- |
| `capabilities` | No options; reports installation, providers, consent and occupied slots. |
| `capture_baseline` | `paths` XOR `change_set_id`; returns a job ID, then a baseline ID only after complete stable analysis. |
| `analyze` | Same selection; optional `baseline_id`, `include_suppressed`, `timeout_seconds` (1..300, default 120). |
| `status` | `job_id`; optional `page_size` (1..500, default 100) and returned opaque cursor. |
| `cancel` | `job_id`; requests cancellation, with settlement observed through status. |
| `impact` | `analysis_id`; optional `baseline_id`, page size and returned impact cursor. |

Select 1..256 distinct project-relative `.cs` files under `Assets` or admitted
`Packages` roots. Each file is limited to 2 MiB, all selected files to 16 MiB.
Selection rejects traversal, links escaping admitted roots, hidden/ignored source,
and prohibited data paths. A prepared change set selects verified original bytes;
an applied set selects verified candidate bytes. There is no implicit Git-diff
selection or retrospective reconstruction of code before editing.

Unity assembly metadata determines references, dependency source projects, defines,
language/nullable settings, response files, rulesets and EditorConfig coverage.
Primary diagnostics belong to the requested files; context diagnostics are reported
separately. Unsupported generator/options/context and unimported membership are
coverage gaps. Broken current source may still be diagnosed even when native
compilation failed. An unchanged old DLL is not a substitute for dependency source.

## Baselines and impact

Complete comparable baselines classify diagnostics as `introduced`, `existing`,
`resolved`, or `unclassified`. Matching uses symbol/syntax anchors and duplicate
multisets; line insertion alone does not create new warnings. GUID evidence can
link a renamed file. Ambiguous moves, configuration/provider changes, missing
baselines, stale inputs and partial results cannot prove resolution. A baseline
captured now says that earlier edit history is unknown.

Impact joins captured field/method declarations with the existing checked reference
graph and bounded serialized text observations. It can identify referring assets,
field removal/type change, `FormerlySerializedAs` evidence and supported persistent
UnityEvent target/method references. An asset dependency alone is not a member
reference. Binary assets, ambiguous/external file IDs, custom serialization,
reflection and nested prefab override details remain explicit limitations.
Pages bind to source and graph revision; changed revisions invalidate cursors.
No scene/prefab is opened, saved or migrated by impact inspection.

Baseline-only script identities remain in impact pages even when current analysis
selects surviving files. A bounded native observation must confirm that the old
script and metadata are absent and that its GUID has not moved. Omitted live files,
replaced paths, remaining metadata and unresolved identities are not deletions.
Source snapshots before and after the join must match the retained analysis.
For confirmed deletions, retained incoming graph edges and the baseline's serialized
members are reported; `deleted_script_reference_coverage` remains an explicit gap
because an import may remove dangling GUID dependencies from Unity's graph.

## Bounds and lifetime

One host process and two queued jobs are admitted per Editor. Context source is
bounded to 256 MiB / 100,000 documents. Results are bounded to 10,000 diagnostics /
8 MiB; public pages to 512 KiB. Overflow and incomplete providers cannot produce
a complete clean verdict. Declarations are limited to 512 / 192 KiB.

Completed server results/baselines share 16 entries / 128 MiB per authenticated
project scope and a 256 MiB global byte budget, with a 30-minute TTL. Active jobs
retain reservations until actual settlement. Expired, evicted or restarted handles
do not replay analysis. A domain reload stops the owned host and preserves an
unsettled-process barrier when termination cannot be proven.

## CLI

The CLI uses the public MCP server composition, preserving baseline/report handles:

```sh
unity-mcp --no-project-context --instance 'Game@0123456789abcdef' --format json script quality capabilities
unity-mcp --no-project-context --instance 'Game@0123456789abcdef' --format json script quality analyze --options '{"paths":["Assets/Scripts/Player.cs"]}'
unity-mcp --no-project-context --instance 'Game@0123456789abcdef' --format json script quality status --options '{"job_id":"<returned-id>"}'
```

`--options` also accepts `@file` or stdin (`-`). These composed commands require an
exact instance. The existing HTTP project-root guard is not available on this MCP
surface; a configured guard is explicitly refused, never silently dropped. Normal
`raw` CLI/native calls do not provide the server baseline/report service.

See [verification profiles](verification-profiles.md) and
[permission mapping](remote-operation-permissions.md). The SDK transport contract
is described in the [official Python SDK reference](https://py.sdk.modelcontextprotocol.io/api/mcp/client/).
