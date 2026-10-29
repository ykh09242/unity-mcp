# On-demand feature readiness

Read `mcpforunity://capabilities` or choose **Check readiness** inside a supported
Editor tool's **Details**. The enabled toggle controls admission; it does not
prove package, selected-target, SDK, scene or toolchain readiness. Editor labels
are explicit snapshots and require another check after changes.

The existing five-source capability query asks `get_project_info` for optional
`include_readiness: true` metadata. Ordinary project-info reads and other tool
calls do not probe these features. The original query deadline, generation/owner
checks and response limits remain unchanged. Older peers yield unknown evidence.

| Feature | Observed evidence | Remaining explicit check |
| --- | --- | --- |
| Build | Active platform module and configured Player backend | Preview the requested build; verify backend/SDK/license/subtarget toolchain |
| Render pipeline | Current Built-in/URP/HDRP/custom pipeline | Inspect settings and the operation's selected target |
| Cinemachine | Registered package version and the existing CinemachineCamera/Brain API detector | Inspect camera targets; CM2 lacks the required CM3 camera API |
| ProBuilder | Registered version and loaded ProBuilderMesh type | Inspect mesh topology and requested operation compatibility |
| Netcode for GameObjects | Registered version and NetworkManager type | Explicit scene preflight; transport/peers/hosting remain unverified |
| Physics | Compiled Physics and Physics2D module boundaries | Both modules are required by the shared physics tools; inspect requested settings or targets |
| Screen Capture | Compiled Screen Capture module boundary | Inspect the capture source; camera and Scene View rendering remain available without Game View capture |

Missing package, unsupported package version, missing platform module, disabled
tool, busy Editor and unavailable evidence have distinct reasons. Known package
or module support never produces `ready: true` for an unspecified operation.
`missing_engine_module` is distinct from an unobserved package or platform SDK.
See [optional Unity modules](optional-unity-modules.md) for required dependencies,
per-tool behavior and compilation coverage.
Multiple blockers remain visible. API absence with an installed package remains
unknown, since assembly loading/compilation may be incomplete. Versions outside
an explicitly known incompatibility are not automatically rejected.

Only small package-version strings are cached. Package registration start/end
invalidates them, and domain reload replaces the cache. Failed metadata reads are
not cached as absent packages. Pipeline, target/backend, enablement and Editor
busy state are read again for every explicit query. No package install/resolve,
scene traversal, tool enablement, build, compilation or refresh is triggered.

Unity references: [package registration events](https://docs.unity.com/en-us/engine/6000.0/script-reference/unityeditor/packagemanager/events/registeredpackages)
and [Cinemachine upgrade guide](https://docs.unity.cn/Packages/com.unity.cinemachine%403.0/manual/CinemachineUpgradeFrom2.html).


## Scoped capability reads

`mcpforunity://capabilities` and doctor keep their complete five-source view.
For focused inspection, read `mcpforunity://capabilities/scoped?features=build`
or `mcpforunity://capabilities/scoped?features=build,input&sources=manage_recording`.
Both options accept comma-separated exact names and combine as a union; omitted
options retain full discovery. Empty or unknown names fail before native dispatch.

Features: `input`, `ui_click`, `recording`, `play_scenario_editor`,
`play_scenario_player`, `build`, `render_pipeline`, `cinemachine`, `probuilder`,
`netcode`, `physics`, `screen_capture`. Sources: `get_editor_state`, `get_project_info`, `manage_input`,
`manage_recording`, `manage_play_scenario`. Source selection includes the features
that source describes. Current Editor evidence is always read, even for a narrow
selection. Player support still requires the existing bundle preflight.

Unrequested features and sources remain unknown with `reason: not_requested`;
they are not unsupported or failed. `partial` continues to describe completeness
of the full view, so a successful narrow read is normally partial. The limits
schema retains a default `max_probes` of five and also accepts six; current queries
disclose a maximum of six to cover one state-only retry on `read_invalidated`.
Requested observations and that retry share the existing single read budget and
final connection generation, project and principal checks.

No completed capability response is cached (TTL zero). The native readiness owner
already reuses stable package-version metadata and invalidates it on package
registration events; current readiness is always recomputed. CLI doctor and full
tool inspection keep their existing complete behavior.

The HTTP/SDK regression measures actual production command dispatches with a
controlled native responder: stable full discovery sends five commands, build-only
sends two, and build plus input sends three. Selected metadata sources run
concurrently, then current Editor evidence is read. Only an invalidated state
observation may be retried once, within the same total budget. These counts do not
claim real Unity latency.
No workload size, memory allowance or execution timeout is reduced.
