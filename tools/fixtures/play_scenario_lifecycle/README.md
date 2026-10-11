# Unity MCP resource lifecycle sample

A minimal, independent Unity 6000.0.69f1 project generated from this template. It uses the current clean checkout of the MCP package; no user's game project is required.

The sample owns a runtime clone of a persistent SessionConfig asset, an actual event subscription and an actual MemoryStream. Returning to Menu normally destroys/unsubscribes/closes them before their tracking lifetimes end. The source asset stays unchanged. The handle example is a managed stream, not an assertion about OS handle-table counts.

## Create a fresh project

Run from the repository root with its existing Python environment:

~~~powershell
$samplePython = (Resolve-Path 'Server/.venv/Scripts/python.exe').Path
& $samplePython tools/create_play_scenario_lifecycle_sample.py --output '.tmp/lifecycle-demo-01'
~~~

The destination must not already exist. The command creates:
- project/: the independent Unity project.
- package/: only the clean tracked MCP package source.
- sample-provenance.json: package revision and file hashes.

It never launches Unity or installs dependencies. Without a local cache override, Unity resolves the pinned registry dependencies when you open the generated project. To reuse an already available cache, pass --package-cache with a directory containing com.unity.editorcoroutines 1.1.0, com.unity.ext.nunit 2.0.5, com.unity.nuget.newtonsoft-json 3.2.2, com.unity.test-framework 1.6.0 and com.unity.ugui 2.0.0. It validates package names and versions before creating anything.

Do not open this template folder as a project. Open the generated project with the already installed Editor. Choose **Lifecycle Sample > Create scenes and saved scenarios**, then open **Assets/Generated/LifecycleSample/Menu.unity** and enter Play Mode. The builder is guarded by ProjectSettings/LifecycleSample.json, preserves existing generated assets, refuses dirty scenes and writes settings only in this dedicated sample.

Menu has **Start normal session**, **Start intentional retention**, and **Release retained resources**. Game has **Publish event**, **Remove Player (failure control)**, and **Return to menu**. Intentional retention keeps all three resources alive after returning. Use explicit release before another normal run. Stopping Play Mode also releases the sample's owners.

## Saved scenarios

The builder saves these through the actual ManagePlayScenario service. Open **Window > MCP for Unity > Play Scenarios** to run them in the generated project, or use an already connected MCP/CLI instance pinned to that project.

| Name | Expected result | Purpose |
| --- | --- | --- |
| sample-normal | succeeded | Start game; cleanup clicks Return and waits for Menu. Repeat to check fresh ownership. |
| sample-failure | timed_out / target_missing | Remove Player, require it to exist, then verify cleanup after the failure. |
| sample-cancel | cancelled when cancelled during Game ready | Hold the acquired state long enough to cancel; cleanup still returns. |
| sample-retained | failed / resource_assertion_failed | Deliberately retain one clone, subscription and stream after Return. |
| sample-release | succeeded | Explicitly release retained owners from Menu. |
| sample-teardown | succeeded | Load Menu directly; OnDestroy must release ownership without the Return button. |
| sample-retained-teardown | failed / resource_assertion_failed | Directly unload Game while intentional retention is enabled; release explicitly afterward. |

Every definition enables zero new-retained-resource limits. The retained case is an expected negative control, not a recommended ownership pattern. Releasing resources after a failed report does not rewrite that report into success. These checks require the Editor; the standalone Player does not support resource assertions.

## Repeat the native checks

Use a new destination for each independent reproduction so older projects/reports are preserved. The native tests generate the scenes and scenarios automatically. With a fresh generated project, run this from the repository root:

~~~powershell
$sampleRoot = (Resolve-Path '.tmp/lifecycle-demo-01').Path
$sampleProject = Join-Path $sampleRoot 'project'
$sampleEditor = 'C:\Program Files\Unity\Hub\Editor\6000.0.69f1\Editor\Unity.exe'
$sampleResults = Join-Path $sampleRoot 'verification'
New-Item -ItemType Directory -Path $sampleResults -ErrorAction Stop | Out-Null
$sampleEvidence = Join-Path $sampleProject 'Library/MCPForUnity/PlayScenarioIntegrationEvidence'
$sampleSession = & $samplePython tools/check_play_scenario_evidence.py $sampleEvidence --initialize
$sampleArguments = '-batchmode -nographics -projectPath "{0}" -runTests -testPlatform EditMode -testFilter UnityMcpLifecycleSample.Tests -testResults "{1}" -logFile "{2}"' -f $sampleProject, (Join-Path $sampleResults 'results.xml'), (Join-Path $sampleResults 'editor.log')
$sampleProcess = Start-Process -FilePath $sampleEditor -ArgumentList $sampleArguments -WindowStyle Hidden -Wait -PassThru
if ($sampleProcess.ExitCode -ne 0) { throw "Native sample tests failed. Preserve $sampleResults and the project reports." }
& $samplePython tools/check_play_scenario_lifecycle.py $sampleEvidence --identities (Join-Path $sampleProject 'Library/LifecycleSampleEvidence') --results (Join-Path $sampleResults 'results.xml') --runner-outcome success --session-id $sampleSession
if ($LASTEXITCODE -ne 0) { throw 'Native body/report/identity evidence validation failed.' }
~~~

The batch test run must report **4 passing tests, no skipped tests**:
- Real clone/event/stream ownership and idempotent disposal.
- Three normal repetitions, missing-player timeout, successful reentry, cancellation, successful reentry in one Play session: seven acquired/released sessions.
- A separate retained-resource negative case, explicit release and a successful normal reentry: two acquired sessions plus a release-only run.
- Direct Game scene teardown in normal and intentional-retention modes, explicit release and successful reentry: three acquired sessions plus a release-only run.

Inspect these outputs:
- Library/MCPForUnity/PlayScenarioRuns/: original terminal run reports.
- Library/MCPForUnity/PlayScenarioIntegrationEvidence/: twelve exported reports, three end-body receipts and the fresh session nonce.
- Library/LifecycleSampleEvidence/: per-acquisition native clone IDs, registered resource IDs and actual event/stream/native-lifetime observations. The sample checker requires these files, verifies their receipt SHA-256 hashes, and matches every acquisition to its job/iteration and lifetime observations.
- verification/results.xml: all native NUnit outcomes.

Registration IDs are scoped to their Play session, not globally unique across domain reloads. The negative report must contain one SO, one subscription and one handle with the observed IDs and owner label; successful cleanup must return the original identity sets. Memory growth and forced GC are not leak oracles.

The tests use the real Editor scenario service directly, including native scene/UI execution and persistence. They do not claim a new external MCP transport connection, OS mouse input, screenshot agreement, another Unity version's native execution or whole-game/unregistered-resource coverage. The repository's ordinary license-free CI does not execute this standalone sample; use the explicit native command above.

Generated scenes, assets, Library, logs, binaries and package-lock files stay in the generated project. Only the template source, metadata, minimal settings, generator and tests belong in Git.
