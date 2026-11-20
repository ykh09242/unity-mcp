# Resolve a numeric scene load across a build-list reorder

Keep a before and after project snapshot at the original project-relative paths. Include `ProjectSettings/EditorBuildSettings.asset` and the selected C# loader. The command reads Unity text scene lists, removes disabled entries before assigning build indices, and reports each changed literal call with its path, line, column, method, integer and old/new scene GUIDs and paths.

```sh
unity-mcp verification-candidate scene-index-diff before after --source Assets/Scripts/Loader.cs
```

The selection covers direct decimal `SceneManager.LoadScene` and `LoadSceneAsync` calls in one unchanged source file. It reports syntactic sites, including conditional code. Variables, constants, named arguments, aliases, string overloads, raw-string sources, build-profile overrides and custom build scene lists require separate inspection. Source and settings inputs have a 1 MiB limit. An out-of-range index yields a null scene. A changed list is a reorder when it contains the same enabled path/GUID pairs in a different order.

The maintained Windows control builds the actual enabled list with the Unity Editor. Its startup scene calls `SceneManager.LoadScene(1)`. Swapping World and Menu changes the loaded destination. The repaired source calls index 2 in the reordered build. Each Player logs the callback's `Scene.path`, `Scene.buildIndex`, process ID, build GUID and a per-launch trial ID. The comparison checks the Editor's source and settings digests, enabled build list, native process exit and callback. A repair restores the original scene when the observed path and its build-settings asset GUID both match the baseline.

```sh
python TestProjects/UnityMCPTests/Verification/run_scene_index_control.py --editor "<Unity.exe>" --project "<disposable project>" --output "<fresh output>"
unity-mcp verification-candidate scene-index-diff "<output>/before" "<output>/after" --repaired "<output>/repaired" --source Assets/Scripts/ScenarioPlayerFixture/SceneIndexProbe.cs --player-line <line-from-affected_calls>
```

Use a prepared copy of UnityMCPTests under `.unity-ci` or `.tmp`, a licensed Windows Editor with Standalone support, and an output directory outside the project. The run retains the scene files, source and settings, build receipts, three Players, process reports, logs, and `comparison.json`. An offline comparison without the native captures reports the Player observation as unavailable.
