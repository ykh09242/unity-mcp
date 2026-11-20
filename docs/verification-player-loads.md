# Compare actual Player loads with static impact

Register the candidate assets, their metadata and the scripts that own the instrumented calls. Enable `UNITY_MCP_PLAYER_REFERENCE_CAPTURE=1` in the environment that launches the Player. Project code calls `ReferenceGraphRuntime.Load(scheme, key, result, ownerPath)` after a load or records a successful acquisition with `Acquire`. Supply the actual returned Unity object and project-relative owner path. A registered candidate run retains its native job directory in the standalone Player stage.

Run the same build twice, once with the optional branch inactive and once with it active. Compare the two job directories while the registered project or its retained source snapshot is available.

```sh
unity-mcp verification-candidate compare-loads --project /projects/Game --registration /captures/game/candidate.json --before /captures/game/verification-runs/<before-job> --after /captures/game/verification-runs/<after-job>
```

The reader verifies each request digest, process exit, job ID, final native report, build ID, source revision, payload and scenario. It recomputes the selected static impact from the registered source digests. Each retained hook includes the recorded owner, key, outcome and object name. For a unique registered Resources key, `registered_asset_path` identifies its catalog path. Unregistered or ambiguous keys have a null registered path.

A successful load from an analyzed script without a matching static owner, scheme and key appears as `observed_outside_prediction` when that asset's static results were retained. Sources outside the static scope and omitted findings produce `static_coverage_unknown`. Failed and unknown loads retain their native outcome. Static predictions that lack a successful hook remain in `unobserved_predictions`. An unvisited branch, an uninstrumented loader and capture loss can all leave a prediction unobserved. Each run reports its own capture availability and dropped count. `newly_observed_loads` is null when either capture was absent or lost records.

The [independent Unity projects](../TestProjects/VerificationProjects/README.md) include a native control. After building them on a licensed Windows builder, run:

```powershell
uv run --project Server --locked python TestProjects/VerificationProjects/run_load_branches.py --control-directory .unity-ci/independent-projects
```

The two launches reuse the exact Harbor payload. Both load the predicted guide. Only the second takes the environment-selected ferry route, whose resource key is computed at runtime. The maintenance route stays unobserved in both runs. `load-branches/comparison.json` contains the observed paths and both jobs retain their own evidence.
