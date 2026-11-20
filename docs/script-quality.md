# Changed-script diagnostics

`manage_script_quality` (the `testing` tool group) analyzes explicitly selected
current C# source using Unity's owning assembly context. It returns advice and
evidence without rewriting source, assets, serialization data or compiler settings.

## Repository C# builds

The compile workflow builds the package with each Unity distribution's own compiler.
It then analyzes the same first-party sources, defines and Unity references using the
pinned .NET SDK. All Microsoft.Unity.Analyzers rules are enabled as errors, including
its two opt-in rules. The SDK's default C# code-quality warnings also fail the build.
SDK suggestions retain their default severity. The diagnostic host itself uses the
SDK defaults for its declared target framework. A host test checks that every
descriptor in the locked Unity analyzer has an entry in the repository policy.

The checks cover Editor and Player assemblies, optional input, script validation
and VFX paths, custom tools, and the owned tests in the configured package profiles.
The Addressables fixture compiles in its separate native package profile. Three public editor window
`CreateGUI()` callbacks carry a method-level `UNT0021` exception to preserve
external callers. The script-quality tests activate deliberate analyzer findings
only in their test analysis request.

From the repository root, run
`python tools/run_dotnet.py restore MCPForUnity/Editor/ScriptDiagnosticsHost~/ScriptDiagnosticsHost.csproj --locked-mode`.
Set `UNITY_ANALYZER_DLL` to
`analyzers/dotnet/cs/Microsoft.Unity.Analyzers.dll` under the restored package
and `UNITY_ANALYZER_SDK` to the installed SDK version directory. Run
`tools/compile-check.sh` with the Unity and package inputs documented in that script.
The analyzer is a build dependency of this repository. Game projects can configure
their own diagnostics independently. [Microsoft.Unity.Analyzers](https://github.com/microsoft/Microsoft.Unity.Analyzers)

## Optional setup

In **Advanced > Script Validation**, explicitly set up/update the analysis host,
then enable analysis for the Editor session. Setup requires a .NET SDK capable of
building `net10.0` and a .NET 10 runtime; first setup restores pinned packages.
The transactional installation lives under
`Library/MCPForUnity/ScriptDiagnosticsHost`. A shipped-source change requires an
explicit host update. Ordinary capability checks and analysis never install,
restore packages or run project MSBuild scripts.

The isolated host pins Roslyn Workspaces and CSharp CodeStyle **5.12.0**,
Microsoft.Unity.Analyzers **1.28.0**, and Microsoft.Bcl.AsyncInterfaces **11.0.0**.
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

### Verifying the analysis host locally

Run the same locked restore and host tests as the compile workflow from the repository root.
The SDK runner checks that temporary storage and the requested artifact directory accept
a real write before starting MSBuild. Commands retain the selected temporary root
so concurrent restores can use NuGet's shared package-cache locks.
A build or assertion failure retains its diagnostics and exit status.

```sh
python tools/run_dotnet.py restore tools/tests/fixtures/script_diagnostics_host/ScriptDiagnosticsHost.Tests.csproj --locked-mode --artifacts-path .work/analysis-host/artifacts
python tools/run_dotnet.py test tools/tests/fixtures/script_diagnostics_host/ScriptDiagnosticsHost.Tests.csproj --no-restore --artifacts-path .work/analysis-host/artifacts -p:TreatWarningsAsErrors=true
```

If the inherited temporary directory is unavailable, pass `--temp-parent` and an existing
writable directory before the SDK command. Use the same temporary root or `NUGET_SCRATCH`
for processes sharing a package cache. [NuGet cache directories](https://learn.microsoft.com/en-us/nuget/consume-packages/managing-the-global-packages-and-cache-folders#temp)

The Editor reports `analysis_temp_unwritable` when the analysis host cannot use its temporary directory.

### Using the MCP CLI

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
