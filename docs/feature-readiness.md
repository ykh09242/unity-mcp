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

Missing package, unsupported package version, missing platform module, disabled
tool, busy Editor and unavailable evidence have distinct reasons. Known package
or module support never produces `ready: true` for an unspecified operation.
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
