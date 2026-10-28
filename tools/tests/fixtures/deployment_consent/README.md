# Managed package replacement consent regression

Run with the existing installed Unity managed references and .NET SDK:

```powershell
& tools/tests/fixtures/deployment_consent/RunRegression.ps1 -UnityData '<Unity installation>/Editor/Data' -SdkPath '<dotnet installation>/sdk/<version>'
```

The fixture compiles actual `ManageEditor.HandleCommand`, `ToolConsentService`
(including its status type), parameter parsing, response types, and the repository's
`ManageEditorDeploymentConsentTests`. It executes the actual dispatch branches
with fake consent/deployment services. Both actions refuse missing, revoked,
disabled or unconfirmed consent before any deployment service entry. A live grant
permits exactly one call; revoking it blocks the next call.

Only managed service boundaries and unused native Editor APIs are replaced.
Unused native boundaries throw if entered. Unity assemblies supply compile-time
types; the runner invokes a managed Mono process, never the Unity Editor. This
does not test real preference persistence, native asset refresh or package copying.
Those behaviors retain their existing native tests.

All output stays in `.tmp/deployment-consent-regression` by default. `-OutputRoot`
selects an owned alternate output directory; `-SourcePath` selects a task-local
historical `ManageEditor.cs` copy for baseline comparison. The runner retains
evidence and performs no recursive deletion, package installation or live writes.
