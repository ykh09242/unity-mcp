# Native Unity evaluation lanes

Each lane is a disposable Windows Unity 6000.0.69f1 project. Setup reads the product package from Git commit `3973b243680eede8639343694edcb075c060b3e5` and uses a loopback-only package registry. UPM reports the local origins for Input System and Cinemachine.

### Offline prerequisites

Use Python 3.11 or newer, Git with the registered commit present, a licensed Windows Unity Editor 6000.0.69f1, and FFmpeg on `PATH`. Use a disposable seed project previously opened in that editor. Its `ProjectSettings/` must include `ProjectVersion.txt` and `ProjectSettings.asset`. Its `Packages/manifest.json` must include the built-in AI, audio, physics, terrain and terrain physics modules. Setup takes the seed's built-in module dependencies and supplies the experiment packages. For comparisons with the recorded project configuration, retain the original seed's complete settings and built-in module inventory.

The seed's `Library/PackageCache/` must contain one resolved copy each of `com.unity.editorcoroutines@1.0.1` and `com.unity.nuget.newtonsoft-json@3.2.2`. The directory suffix can be a Unity hash. The package manifests specify the versions. Newtonsoft must include `Runtime/Newtonsoft.Json.dll`. Under the separate global cache root, setup reads `packages/packages.unity.com/com.unity.inputsystem@1.14.2/package.json` and `npm/packages.unity.com/com.unity.cinemachine/2.6.11/package.tgz` with their original payloads.

| Environment variable | Default on Windows | Value |
| --- | --- | --- |
| `LIVE_AGENT_SEED_PROJECT` | `.work/discovery-live-20261122/DiscoveryFixture` under this repository | Seed project root |
| `LIVE_AGENT_UNITY_EDITOR` | `C:/Program Files/Unity/Hub/Editor/6000.0.69f1/Editor` | Editor directory containing `Unity.exe` and `Data/`, or the path to `Unity.exe` |
| `LIVE_AGENT_UNITY_CACHE` | `%LOCALAPPDATA%/Unity/cache` | Global Unity cache root |
| `LIVE_AGENT_TRIAL_DIR` | `.work/live-agent-20261125` under this repository | Dedicated output root, initially new or empty |

On another Windows host, set overrides in the PowerShell session used for all commands. Supply the local paths for your installation.

```powershell
$env:LIVE_AGENT_SEED_PROJECT = 'D:/UnitySeeds/DiscoveryFixture'
$env:LIVE_AGENT_UNITY_EDITOR = 'D:/Unity/Hub/Editor/6000.0.69f1/Editor'
$env:LIVE_AGENT_UNITY_CACHE = 'D:/UnityCaches/cache'
$env:LIVE_AGENT_TRIAL_DIR = 'D:/UnityExperiments/live-agent-replay'
```

From the repository root on the evaluation host, prepare the shared files, start the editors, and install the private observer.

```powershell
python tools/experiments/live_editor_fixture/lane.py shared
python tools/experiments/live_editor_fixture/lane.py launch 0 1 2 3
python tools/experiments/live_editor_fixture/lane.py world 0 1 2 3
python tools/experiments/live_editor_fixture/lane.py ping 0 1 2 3
```

Each launch owns `lane-N/project`, its sibling `control` directory and its own `status` directory under the output root. The initializer records the output root and revision. Launch can reuse a stopped lane with a matching index, revision and exact project path. An existing directory without this lane receipt is rejected. A child server must use that exact status directory and the canonical instance returned by `ping`. Verify the returned project root against the assigned lane. The in-process endpoint uses the product's owned-loopback start and a dedicated, per-launch credential through the product's platform credential store. Normal shutdown retires that credential.

One caller owns a lane from prepare through final snapshot. The Python entry point is `request(lane, op, task, language, seed, timeout=120)`. A successful prepare returns observed native state and the editor identity. A subsequent snapshot requires the same task, language and seed. Each profile in a pair uses the same seed. Object identities and collateral digests describe the individual attempt and must be compared with that attempt's own baseline.

```powershell
python tools/experiments/live_editor_fixture/lane.py prepare 0 --task lookout_fov --language el --seed 42
python tools/experiments/live_editor_fixture/lane.py snapshot 0 --task lookout_fov --language el --seed 42
```

The native preflight prepares and snapshots each of the 71 task-language combinations at seed 41. Run it exclusively on idle lanes before model trials. It writes full results under the shared run directory. The actor receives only Unity tools and original-language prompts. The project contains the generic host DLL. Per-task world setup, the observer DLL, mailbox and evidence remain outside the project. The Editor loads the private DLL at the first prepare and retains that version until it exits.

```powershell
python tools/experiments/live_editor_fixture/preflight.py 1 2 3
python tools/experiments/live_editor_fixture/lane.py stop 0 1 2 3
```

Prepare recreates the evaluation scenes and assets, clears the Unity selection and undo history, closes any Frame Debugger window, and records the immutable project configuration. A configuration change since the previous ready state stops reuse of the lane. Within each attempt, native snapshots cover loaded scene identities, active scene, component state, file content, configuration and relevant task-specific facts. Terrain writes also retain a digest of the full remaining mask and terrain heights. Input authoring records actual importer notifications and saved file contents.
