# Optional Project Auditor analysis

`manage_project_auditor` is an experimental integration for the official
`com.unity.project-auditor` **1.0.1** API on Unity 2021.3 or newer. The package must
already be installed and compiled in the selected project. This tool never installs
packages, changes the active build target, runs fixers, or writes an exported report.
Other package versions return an explicit unsupported-version result.

The package bundles its diagnostic rules. `availability` checks the loaded API and
validates the two bundled rule databases without constructing an analysis session.
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

The adapter was reviewed against the installed official 1.0.1 source, including
`AnalysisParams.OnModuleCompleted`, the `AnalysisResult` enum, `ReportItem` and
`DescriptorId.GetDescriptor()`. Rule registration uses the exact version's internal
`GetDescriptorIDs` observation, so widening version support requires another API review.
Official references: [command-line analysis](https://docs.unity3d.com/Packages/com.unity.project-auditor@1.0/manual/run-from-command-line.html)
and [API namespace](https://docs.unity3d.com/Packages/com.unity.project-auditor@1.0/api/Unity.ProjectAuditor.Editor.html).

`ProjectAuditorContractTests` covers missing rules versus empty success, duplicate
requests, ownership, failures, reload, stable pagination and capture limits without the
optional dependency. The real `ProjectAuditorNativeIntegrationTests` method is
**Explicit** and requires a controlled fixture project. Merely installing Auditor does
not opt ordinary EditMode runs into this test. Deliberately selecting it with missing
packages or the wrong profile fails its prerequisites; it does not skip the required
positive case.

The fixture profile contains the current MCP package, Test Framework, NUnit,
Editor Coroutines, uGUI, Newtonsoft JSON, Project Auditor 1.0.1 and its Mono.Cecil
dependency, plus the Editor's built-in `com.unity.modules.*` packages. The observed
Unity 6000.0.69f1 profile used Test Framework 1.6.0, NUnit 2.0.5, Editor Coroutines 1.1.0,
uGUI 2.0.0, Newtonsoft 3.2.2 and Cecil 1.11.4. Keep other optional runtime packages out
of this fixture's active manifest; preserve their cached source directories and restore
the original manifest/lock after an isolated profile change. No package installation or
profile change is performed by the test or production tool.

This restriction concerns the fixture's exact local-file oracle, not production
availability. A real combined ProBuilder/URP run completed 11 modules and produced 12426
diagnostics with 262 registered rules. Its 4096 retained rows included 1883 real `PAC2002`
diagnostics from runtime packages, while the local fixture was outside that bounded
prefix. `observation_truncated=true`, `diagnostic_count_exact=false` and `clean=null`
correctly described that result. Full optional-package projects can legitimately
truncate before a particular local file, so they cannot prove this exact fixture oracle.
The production cap remains 4096 rows and 4 MiB.

Select the exact required method, and require it to execute and pass with zero skips:

```text
-testFilter MCPForUnityTests.Editor.Tools.ProjectAuditorNativeIntegrationTests.InstalledOfficialPackageProducesActualFixtureDiagnostic
```

It starts the default production analysis and requires the real `PAC2002` diagnostic
for `Assets/Scripts/ProjectAuditorAuditFixture.cs`, with its native category, severity,
areas, path, line, description and recommendation. A terminal status alone cannot pass.
The optional CI catalog (`tools/unity-optional-tests.json`) currently does not select
this profile or method; its existing combined optional-package fixture must not gain
this required test without preparing the separate controlled profile first.
