# Verified workflow compatibility

The [Unity CI version matrix](../../tools/unity-versions.json) sets exact Editor versions. The tables distinguish managed compilation (**C**), an observation in a running Editor (**E**) and an observation in an actual built Player (**P**). An APK or executable build establishes compilation and build evidence. A Player verdict requires a launched build and an observed result.

## Configured Editor roles

Each cell is **C / E / P** for the indicated Editor role. `open` means a matching feature receipt is outstanding. `—` marks a boundary where the feature has no observable behavior. Use license-free CI for its covered compilation and tooling checks. Native Editor and Player outcomes require the corresponding execution receipts for that patch.

| Editor and role | Shop asset move and state | Pointer routing and localization | Optional Addressables and recovery | URP renderer binding | Physics triggers | Stable scenario/profile/report read | Android IL2CPP fixture |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `2021.3.45f2` package floor | open / open / open | open / open / open | open / open / — | open / open / — | open / open / open | open / open / — | open / — / open |
| `2022.3.76f1` LTS | open / open / open | open / open / open | open / open / — | open / open / — | open / open / open | open / open / — | open / — / open |
| `6000.0.84f1` LTS | open / open / open | open / open / open | open / open / — | open / open / — | open / open / open | open / open / — | open / — / open |
| `6000.3.25f1` LTS | open / open / open | open / open / open | open / open / — | open / open / — | open / open / open | open / open / — | open / — / open |
| `6000.6.4f1` current | open / open / open | open / open / open | open / open / — | open / open / — | open / open / open | open / open / — | open / — / open |
| `6000.7.0b3` beta | open / open / open | open / open / open | open / open / — | open / open / — | open / open / open | open / open / — | open / — / open |
| `7000.0.0a7` Unity 7 alpha | open / open / open | open / open / open | open / open / — | open / open / — | open / open / open | open / open / — | open / — / open |

The open rows need the exact Editor and target build module, a valid license for native execution, and a matching copy of the workflow fixture. The standard package matrix can produce compile and test results for its configured surfaces. This table uses the named feature receipts below. [CI validation](ci-validation.md)

## Local feature receipts

The paths in this section identify generated files in the original test workspace. The [reproduction guide](verified-change-walkthrough.md) explains how to generate the corresponding reports in a fresh checkout.

The following artifacts were produced on Windows with Unity `6000.0.69f1`. The receipts contain the exact source, output identity and observed result for each run. The Editor and build logs identify `Product: Unity Pro`. License-free CI can run the hermetic checks and compile the package for its configured Editor roles.

| Feature | C | E | P | Receipt and source |
| --- | --- | --- | --- | --- |
| Shop asset move and readiness | Three compiled Windows x64 Mono builds | Retained serialized GUID in all three phases. Old loader key failed after the move and the updated key restored the reference. | Actual launch exits `0`, `1`, `0` with banner counts `1`, `0`, `1`. | Triplet comparison (``.unity-ci/verified-workflows-20261120/run-01/comparison.json``) binds all three source revisions and build IDs. |
| uGUI pointer routing and TMP localization | Two native EditMode cases compiled, plus the Windows Player | Direct3D 11 pointer routing at `640 × 480`, English and Korean layout checks at `320 × 240` and `960 × 480`. | Windows Mono and Direct3D 11 delivered zero clicks while blocked and one click after repair at the same point. Localization was exercised in the Editor. | Editor receipt (``.unity-ci/verified-pointer-20261120/final/receipt.json``), with base `dae3cf82de3285427cccef99d0bd409616eb4a6d`, and Player receipt (``.unity-ci/verified-pointer-20261120/final/player/receipt.json``), with base `9e6b8d4afc83b28cd5d8a12021b2ea1c11062ebc`. Both bind the local fixture inputs. |
| Addressables adapter and recovery | Two projects compiled, including the configured `1.1.5` provider with Scriptable Build Pipeline `1.23.4` | Reported provider absence, inspected and edited legacy settings, and exercised reconnect, cancellation, domain reload and restart controls. | — | Assembly probe (``.work/verified-addressables-boundary1/measurement.json``), legacy cases (``.unity-ci/verified-recovery-20261120/addressables-legacy/native-final2/EditMode/results.xml``), domain reload (``.unity-ci/verified-recovery-20261120/addressables-legacy/native-final2/PrefabCheckpointSurvivesDomainReload/results.xml``), Editor restart (``.unity-ci/verified-recovery-20261120/addressables-legacy/native-final2/ReplacementEditorRefusesPriorPrefabCheckpoint/results.xml``). |
| URP renderer binding | URP `17.0.4` Editor fixture compiled | Native bindings observed in Direct3D 11 and headless Editor runs. The graphical captures corroborate the renderer change. | — | Renderer and physics receipts (``.unity-ci/verified-render-physics-20261120/verified-render-physics-summary.json``) identify package `dae3cf82de3285427cccef99d0bd409616eb4a6d` and fixture inputs. |
| Physics trigger regression | Two standalone fixtures compiled | Layer pair and collision settings read from each fixture. | Two Windows Mono Player runs observed 0 then 1 trigger callbacks, with exits 1 and 0. | Renderer and physics receipts (``.unity-ci/verified-render-physics-20261120/verified-render-physics-summary.json``) bind both builds and native reports. |
| Stable scenario, profile and report | Stable and candidate Editor projects compiled | Stable storage wrote all three files and the candidate read the same values from unchanged bytes. | — | Before/after comparison (``.work/verified-upgrade-round5/comparison.json``) identifies both package revisions and both Python servers. |
| Android shop fixture | ARM64 IL2CPP APK built successfully | — | Open. ADB reported zero devices. | Build receipt (``.work/verified-android-pilot2/bundle/build.json``), staged input hashes (``.work/verified-android-pilot2/source.json``), and unverified device selection result (``.work/verified-android-unavailable2/result.json``). |

## Addressables boundary

The `MCPForUnity.Editor` assembly definition uses core runtime, Editor Coroutines and precompiled JSON and Roslyn references. Both compiled assemblies report no `Unity.Addressables` or `Unity.Addressables.Editor` reference. With the provider absent, `manage_addressables.inspect` returned `addressables_unavailable`. With the configured legacy package installed, the same request found the provider and returned `addressables_settings_not_found` for the explicit missing settings path. The separate legacy authoring tests exercise actual settings assets and edits. [Core assembly definition](../../MCPForUnity/Editor/MCPForUnity.Editor.asmdef) · [Native shapes configuration](../../tools/unity-ci-packages.json)

The source archive was commit `9e6b8d4afc83b28cd5d8a12021b2ea1c11062ebc`, tree `a174a693b6500a16931bb1e37aa22b56015d7507`. Each column below reflects one observed import. Cold projects had fresh `Library` directories and could use the workstation's shared compiler cache. The warm edit appended a comment in the disposable package's `AddressablesState.cs`. The compiler log identified only `MCPForUnity.Editor.dll` for that edit, and the Addressables provider assemblies stayed out of that work. Bee timings are individual compiler observations.

| Package shape | Cold project | Warm unchanged | Warm adapter leaf edit |
| --- | --- | --- | --- |
| Provider absent | 13 Csc nodes, 239/242 Bee items updated, 15.34 s | 0 Csc nodes, 0/242, 0.13 s | 1 Csc node, 3/242, 5.45 s |
| Addressables `1.1.5` and SBP `1.23.4` | 18 Csc nodes, 284/287 Bee items updated, 7.40 s | 0 Csc nodes, 0/287, 0.09 s | 1 Csc node, 3/287, 6.18 s |

The provider already loads through a runtime type check at the Editor boundary. The measurement shows the cost of rebuilding the core Editor assembly after editing its adapter source. It provides no measured benefit for moving the adapter to a separate assembly, so the existing assembly and Unity 2021.3 package floor are retained. Run the [boundary probe](../../tools/measure_addressables_boundary.py) with the selected Editor, version and a fresh `--output` directory. It records cold project import, warm unchanged import and warm adapter edit for both configured package shapes.

## Prior stable data

`ykh09242-v2.5.0` resolves to package commit `e3673045846d8c3d1f900603f4a69228f93407f5`. Its published server pin resolves to `8fdc71f691b711bfce488d260cca530d164346e1`. The upgrade fixture exports both Git revisions without changing branches. The stable Editor writes a scenario, a profile and a fixture report through its own storage APIs. A second disposable Editor reads byte copies from the selected candidate commit. The probe checks the normalized scenario, profile document, history lookup and report values. Each server resolves the stored profile and exports the same report to JUnit. The report contains synthetic scenario and failure data and serves as a storage compatibility fixture.

Candidate `9e6b8d4afc83b28cd5d8a12021b2ea1c11062ebc` ran with its checkout server source. Both Editors and servers agreed. The scenario, profile and report hashes matched before and after. Native before (``.work/verified-upgrade-round5/before/editor.json``) · Native after (``.work/verified-upgrade-round5/after/editor.json``) · Before JUnit (``.work/verified-upgrade-round5/before/export/junit.xml``) · After JUnit (``.work/verified-upgrade-round5/after/export/junit.xml``)

```powershell
python tools/run_stable_workflow_upgrade.py --stable ykh09242-v2.5.0 --candidate HEAD --editor '<Editor>/Unity.exe' --unity-version 6000.0.69f1 --output .work/verified-upgrade
```

Select an exact candidate commit to reproduce a previous comparison. The runner retains native launch and Editor evidence, both server projections, the JUnit artifacts and the before/after file digests in `comparison.json`.

## Android IL2CPP pilot

Use an Editor with Android Build Support, SDK, NDK and OpenJDK. The fixture command stages the product and only the selected test fixture scripts in a new project. It writes a separate ARM64 IL2CPP APK and a build receipt with the exact staged source and binary digest.

```powershell
python TestProjects/UnityMCPTests/Verification/build_android_shop_fixture.py --editor '<Editor>/Unity.exe' --unity-version 6000.0.69f1 --output .work/android-shop-build
adb devices -l
python tools/run_android_scenario_fixture.py --build .work/android-shop-build/bundle/build.json --serial '<owned-device-serial>' --install --output .work/android-shop-test
```

For an already installed matching fixture APK, omit `--install`. The runner selects the exact serial and its current ADB transport. It records the device fingerprint, API level and ABIs, verifies the installed APK hash and starts the resolved package activity with a unique launch ID. A passing `result.json` requires the app's observed state, launch ID, PID, app ID, scenario, build ID, source revision and Unity version to agree. The shop probe reads the loaded object and checks that the same banner as the serialized prefab reference is active in the scene. Earlier launch records and reports from a different build cannot establish readiness. [Unity Android architecture setup](https://docs.unity3d.com/6000.0/Documentation/ScriptReference/PlayerSettings.Android-targetArchitectures.html) · [ADB](https://developer.android.com/tools/adb)
