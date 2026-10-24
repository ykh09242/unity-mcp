# Optional Unity engine modules

MCP does not require Physics, Physics2D or Screen Capture in a project that has
removed those built-in modules. Standard Unity projects that include the modules
retain their existing behavior. The package does not reinstall them implicitly.
Enabling a tool still requires its module; use Unity Package Manager to restore
a built-in module when that feature is needed. No driver or separate native
installation is introduced.

| Module | Compile boundary | Behavior when absent |
| --- | --- | --- |
| `com.unity.modules.physics` | `MCP_FOR_UNITY_PHYSICS` in Runtime and Editor | Physics tools return `physics_modules_unavailable`; physics material creation returns `physics_module_unavailable`. Ordinary assets, components, scenes and profiling remain available. |
| `com.unity.modules.physics2d` | `MCP_FOR_UNITY_PHYSICS2D` in Runtime and Editor | Physics tools return `physics_modules_unavailable`. Core component/scene operations and available simulation-profile domains remain available. |
| `com.unity.modules.screencapture` | `MCP_FOR_UNITY_SCREEN_CAPTURE` in Runtime and Editor | Direct Game View capture returns `screen_capture_module_unavailable`; composited screenshots report a camera fallback without waiting for a frame. Camera and Scene View rendering remain available. Recording capabilities omit the unavailable Game View source. |

The existing physics implementations share 2D and 3D types. Their dispatcher and
generated capability wrappers remain registered, but require **both** physics
modules. They report the missing package IDs before executing an action. A
per-dimension physics tool split is not implemented. Scene bounds use whichever
collider module remains installed; cross-dimension conflict checks are needed
only when both dimensions exist. Simulation settings resolve each dimension
independently and retain explicit unavailable entries.

The Runtime and Editor asmdefs each derive their symbols from the installed
module package version, starting at `1.0.0`. These are assembly-local package
version defines, not user scripting symbols or inferred Unity version flags.
See Unity's [assembly inclusion documentation](https://docs.unity3d.com/Manual/assembly-definition-includes.html).

## Required boundaries

The package still requires animation and UI Toolkit. Animation types are used by
the animation tools, Blender imports, sprite setup and object resolution. UI
Toolkit backs the main Editor windows, settings and UI tools. Those dependencies
are not described as optional. Image Conversion remains required for PNG/image
processing, and UnityWebRequest remains required for supported download paths.
Editor Coroutines, Test Framework, NUnit and Newtonsoft remain automatic UPM
dependencies. Existing separate Input System and uGUI adapters keep their own
package version constraints.

## Availability evidence

`get_project_info` reports `packages.physics`, `packages.physics2d` and
`packages.screenCapture` from the compiled module boundaries. With
`include_readiness: true`, its `physics` and `screen_capture` feature rows include
module booleans under `observed`, required package IDs and
`missing_engine_module` when a module is missing. Module presence does not prove
that a native operation or capture source is ready.
These shared module rows have no single tool toggle as their authority:
`tool_enabled` is null. The Editor's per-tool readiness display applies the
selected tool's own enablement without altering aggregate module evidence.

The full and scoped capability resources preserve this bounded evidence without
additional native probes. Older Editors that omit it yield unknown support.
There is no blanket rejection of `manage_scene` or other mixed-purpose tools
because their camera and non-physics actions can remain usable.

## Compilation coverage

`tools/compile-check.sh` accepts `DISABLED_UNITY_MODULES`, a space-separated list
containing `physics`, `physics2d` and/or `screencapture`. Selected module DLLs are
removed from the compiler reference set and their symbols are omitted. Unknown
module names fail before compilation. Unity 7's bundled uGUI module symbols
follow the same selection.

After providing the usual compiler/package environment documented in that
script, run the representative absent combinations with:

```sh
PLATFORMS=linux OUT=/tmp/mcp-optional bash tools/compile-optional-modules.sh
```

Set `OPTIONAL_MODULE_MATRIX=all` for all seven non-default combinations. The
ordinary compile check covers all modules present. The absence wrapper excludes
the existing test project because its fixtures intentionally reference physics;
it still compiles the product assemblies, selected input adapters and compiler
examples. It does not alter the project or install packages.

CI retains the ordinary seven-version, three-platform-symbol matrix. Additional
individual-absence and all-absent checks use Linux symbols on Unity 2021.3 and
6000.6. Pull requests select those checks through
`tools/optional-module-paths.txt`; receiving beta pushes and manual runs include
them. This limits recurring work while covering the support floor and current
module boundaries. The other version/platform combinations remain covered by
the full default matrix, not by the optional-absence CI pass.

These checks compile real C# against Unity metadata without launching Unity.
They do not establish native Editor behavior on Windows, macOS or Linux. Native
regressions are in `OptionalUnityModulesTests` and `FeatureReadinessTests`.
