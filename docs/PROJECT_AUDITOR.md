# Optional Project Auditor analysis

`manage_project_auditor` works with the official Project Auditor package or an Auditor
module supplied by the Unity Editor. `availability` inspects the loaded implementation
without constructing an analysis session. It checks the analysis entry point, constructors,
callbacks, registered descriptor reader and the diagnostic members used in the result.
An incompatible signature returns `unsupported_api_shape`.

Availability also validates the API and project-settings rule databases. It resolves
them from the Auditor's bundled data or its installed `com.unity.project-auditor-rules`
dependency. The Editor-integrated provider uses the rules package. The response includes
`integration`, the installed package version when present, and the rules provider.
`api_supported` and `rules_available` describe separate prerequisites for starting analysis.
`start` additionally checks that native modules registered descriptors. Missing rules,
no completed modules, module failures and successful analysis with zero diagnostics
remain distinct outcomes.

```json
{"action":"availability"}
{"action":"start","audit_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
{"action":"status","audit_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
{"action":"issues","audit_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","page_size":50}
```

The MCP and CLI wrappers generate `audit_id` once if it is omitted on start. Native
starts require a 32-character lower-case hexadecimal identity. Reusing a retained
identity observes the original request and never starts another scan. A separate
identity receives `busy` while this tool retains a native analysis owner. After an
uncertain response, query the original identity before requesting another scan.
Identities and cursors belong to the current Editor session; they do not identify an
analysis started in the Project Auditor window or by another integration. The upstream
API supplies no global-running observation, so external audits are not claimed idle.

An explicit start uses the current build target and default official analysis options.
The upstream API may compile Player assemblies and save its thresholds/settings in
`ProjectSettings/ProjectAuditorSettings.asset`. Start therefore requires both `edit`
and `run` permission; availability, status and diagnostic pages require `read`.
The queued request is interrupted if the Editor target or readiness changes before
native invocation. Observing a job never starts analysis. There is no cancellation
endpoint: changing a local state is not proof that native work stopped.

States are `queued`, `running`, `completed`, `failed`, `unavailable` and `interrupted`;
`busy` means a new request was refused before starting. Module completion callbacks
provide the success/failure/interruption evidence. `clean` is a boolean only for a
completed, fully observed analysis. It is null for failures, absent rules or truncated
observations. Informational insights are excluded from the diagnostic pages.

Each diagnostic preserves the official `Category`, `Severity`, `Areas`, `Description`,
`RelativePath`, `Line`, `DescriptorId` and `Recommendation` when available. Fields are
bounded and `fields_truncated` records shortening. A page has at most 100 rows and
256 KiB of item JSON. Captures retain at most 4096 rows and 4 MiB per audit; the API
adapter inspects at most 250000 incoming report items. `observation_truncated` and
`diagnostic_count_exact` describe these limits. Use only the returned opaque
`next_cursor` for the same audit.

Up to eight audit records remain for ten minutes after completion. Domain reload
retains scalar status evidence, but captured diagnostic rows are lost. Reading those
rows then returns `project_auditor_results_unavailable`; a completed empty capture
remains observable. If reload loses an invoked analysis's completion observer, status
becomes `interrupted` and native admission remains fenced for the Editor session.
Inspect analysis in Unity and restart the Editor to reset that lost ownership. When a
live retained observer proves completion in the same domain, its ownership is released.

```text
unity-mcp project-auditor availability
unity-mcp project-auditor start
unity-mcp project-auditor status <audit_id>
unity-mcp project-auditor issues <audit_id> --page-size 50
```

The adapter reads the native `AnalysisResult` from the module completion callback.
It accepts the result-only signature and the Editor signature that also reports a
module name and elapsed time. On explicit start, the registered descriptor query
establishes the actual rule count. [Package scripting API](https://docs.unity3d.com/Packages/com.unity.project-auditor@1.0/api/Unity.ProjectAuditor.Editor.ProjectAuditor.html)
and [Editor implementation](https://github.com/Unity-Technologies/UnityCsReference/blob/master/Modules/ProjectAuditorEditor/API/ProjectAuditor.cs).

`ProjectAuditorContractTests` covers missing rules versus empty success, duplicate
requests, ownership, failures, reload, stable pagination and capture limits without the
optional dependency. Each method in `ProjectAuditorNativeIntegrationTests` is
**Explicit** and requires an isolated fixture project. Ordinary EditMode runs exclude
these cases. The selected test fails if its profile prerequisites are missing.

All four profiles use a minimal base consisting of the current MCP package, Test
Framework, NUnit, Editor Coroutines, uGUI, Newtonsoft JSON and built-in
`com.unity.modules.*` packages. Keep other optional runtime packages outside these
isolated projects. Prepare dependencies before running the tests.

| Editor verified | Additional packages | Explicit method | Expected availability |
| --- | --- | --- | --- |
| 6000.0.69f1 | Auditor 3.2.0, Rules 3.2.0, Mono.Cecil 1.11.6 | `InstalledOfficialPackageProducesActualFixtureDiagnostic` | `package`, API and rules available |
| 6000.6.4f1 | Editor-bundled Rules 1.0.1 | `IntegratedEditorProducesActualFixtureDiagnostic` | `editor`, API and rules available |
| 6000.6.4f1 | Base only | `IntegratedEditorRequiresRulesBeforeStarting` | `editor`, API available, `rules_package_not_installed` |
| 6000.0.69f1 | Base only | `EditorWithoutAuditorRejectsStart` | `none`, `package_not_installed` |

The Editor-integrated profiles omit the standalone Auditor dependency. The verified
installation provides the rules archive in the Editor data directory at
`Resources/PackageManager/Editor/com.unity.project-auditor-rules-1.0.1.tgz`.
The standalone profile uses the official UPM distribution. Older official standalone
Auditor releases with bundled rules can also run the standalone profile.

Select the exact method for the prepared profile and require one pass with zero skips.
For example, the Editor with Rules uses the following filter.

```text
-testFilter MCPForUnityTests.Editor.Tools.ProjectAuditorNativeIntegrationTests.IntegratedEditorProducesActualFixtureDiagnostic
```

Each positive case starts the default production analysis and requires an exact
diagnostic count and one real `PAC2002` for
`Assets/Scripts/ProjectAuditorAuditFixture.cs`, with its native category, severity,
areas, path, line, description and recommendation. Repeating the start with the same
identity must return the original completed status. The negative cases require explicit
start rejection before native invocation, with `clean=null`. Every case observes
availability twice and checks that the package manifest, lockfile, Auditor settings and
active build target remain unchanged.

The isolated profiles keep the full diagnostic capture within its bounds. A combined
ProBuilder/URP project produced 12426 diagnostics, with code diagnostics from runtime
packages filling the 4096 retained rows before the local fixture. Its
`observation_truncated=true`, `diagnostic_count_exact=false` and `clean=null` correctly
marked the incomplete observation. Use the isolated profiles to verify the exact count
and the fixture's diagnostic.
