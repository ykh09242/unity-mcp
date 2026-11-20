# Independent Unity 6 projects

Harbor's Dock scene registers `harbor.berth` and reads the quay's vertical position. Its saved `berth-number` scenario expects the integer 3. Observatory's Summit scene registers `observatory.instrument` and reads the telescope object's name. Its `instrument-name` scenario expects the string `Zenith`. Each project owns its scene, state provider, scenario and typed expectation. Both package manifests point to the same Unity MCP package directory.

On a licensed Windows builder with Unity 6000.0.69f1 and standalone Mono support, run the following from this checkout with a new output directory.

```powershell
uv run --project Server --locked python TestProjects/VerificationProjects/run_projects.py --editor 'C:/Unity/Editor/Unity.exe' --output .unity-ci/independent-projects
```

The runner stages one distribution and its CI dependencies, opens two independent project copies, builds both through the public scenario builder and launches each verified payload. It checks every native step result, the scenario, source revision and build identity. `comparison.json` names both native reports and the unchanged distribution digest. Each job directory retains the process exit, log and native report. A failing state check returns a nonzero result and retains its job artifacts. To exercise a negative control in a disposable copy, change Harbor's expected berth to 4 while keeping Dock at 3 and run its scenario. Observatory's definition and scene live in its own project.
