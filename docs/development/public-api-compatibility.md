# Public C# extension compatibility

The license-free compile workflow checks the supported C# extension surface against
the exact commit in `tools/public-api/baseline.txt`. The Unity 2021.3 floor and
6000.0 LTS lanes compile both revisions with the same Unity compiler, defines
and third-party references. The Editor assembly references its matching revision
of the Runtime assembly.

## Supported surface

The inventory in `tools/public-api/surface.txt` selects each assembly and
fully qualified type. A third column restricts that type to a named member.
Additional member names on the same line select further members. Names include
all corresponding baseline overloads, property accessors and `.ctor` constructors.

| Assembly | Supported API |
| --- | --- |
| Editor | Public `CreateGUI()` callbacks on `MCPForUnityEditorWindow`, `MCPSetupWindow` and `EditorPrefsWindow` |
| Editor | Custom tool, parameter and resource attributes, including public constructors and properties |
| Editor | Typed tool requests via `PropertyConversion.ConvertTo<T>` and `ParamCoercion.ReadScalar<T>`, and the success, error and pending response classes |
| Editor | Legacy `McpJobStateStore`, `ProcessTerminator` and `IProcessTerminator`, the `ServerManagementService` constructors and `StopManagedLocalHttpServer()` |
| Editor | Client configurator interface, discovery, documented model fields, subclass constructors and customization methods for JSON, TOML and CLI configurators |
| Editor | The `PlayScenarioPlayerBuild.BuildFromCommandLine()` batch entry point |
| Runtime | State provider and reset participant interfaces, registrations, state values and kinds |
| Runtime | Event registration and the producer stream used by game code |
| Runtime | Scenario target IDs and the three game-owned resource registration methods |

The checker compares compiled metadata using the SDK's ApiCompat rules, including
the optional named-parameter rule. An independent Editor extension uses registration
attributes, converts typed requests, returns response envelopes, binds the
legacy public methods, subclasses a configurator and calls all three callbacks.
A separate game assembly implements the interfaces and calls registration and
event APIs. Both clients are compiled against each revision. The original Editor
DLL exercises managed helper calls against the replacement. Legacy method
delegates bind without invoking process termination or touching stored job data.
The original game DLL also executes a managed state/reset round
trip against the candidate Runtime in each of three compile profiles (Editor,
Player and scenario-enabled Player). Unity UI callbacks and engine lifecycle
execution belong to the licensed Editor and Player tests.
[.NET SDK ApiCompat](https://github.com/dotnet/sdk/tree/main/src/Compatibility/ApiCompat)

Source selection follows the author's guides for
[custom tools](../../website/docs/guides/custom-tools.md),
[legacy helpers](../../website/docs/guides/legacy-helpers.md),
[client configurators](../../website/docs/guides/client-configurators.md)
and [game scenario integration](../play-scenarios.md).

The gate verifies both asmdef names and GUIDs and preserves their implicit
references from Unity's predefined assemblies, including project scripts in
`Assets/Editor`. It also checks declared platform availability and rejects
new or changed define requirements that need compatibility review. It checks
the documented legacy helper script GUIDs. The Windows standalone defines
are compiled in CI on both Unity lanes. Other build target configurations
require their own compile evidence.
[Unity assembly definition format](https://docs.unity.com/en-us/engine/6000.6/manual/programming-environment/script-compilation/assembly-definition-files/file-format)

## Run locally

Use the same .NET SDK as the compile workflow (11.0.100). From the repository root,
prepare a **new** output directory. This extracts the two package source trees from
the pinned Git object and builds the checker using the installed SDK. A shallow
checkout fetches the named commit from `origin` when necessary.

```sh
bash tools/public-api/prepare.sh .work/public-api-inputs
python tools/public-api/test_compatibility.py .work/public-api-inputs/checker/PublicApi.dll
PUBLIC_API_INPUTS=.work/public-api-inputs PLATFORMS=win bash tools/compile-check.sh
```

Supply the usual `UNITY_DATA`, `UNITY_VERSION`, `EDITOR_COROUTINES_SOURCE` and
reference inputs described in `tools/compile-check.sh`. The compatibility check
uses the first requested platform. Set `PUBLIC_API_DOTNET` when the SDK host is
mounted at a separate path. The fast regression fixtures exercise a compatible
addition, a widened parameter that still compiles from source, a visibility
change, a removed method or property setter, an added interface requirement,
and a renamed parameter. Additional cases change the assembly definition while
leaving both DLLs compatible.

## Update the baseline

Keep the previous pin while validating a release candidate. After that revision
is accepted, advance the pin to its full commit ID so subsequent changes protect
its newly supported public members. For a new extension type or named surface,
add its inventory entry when it exists in the approved baseline and extend the
external consumer to exercise the documented usage. Review any intentional
removal with its consumer migration before changing the contract.
