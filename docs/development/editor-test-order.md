# Compare two Edit Mode tests

On a licensed Editor host, prepare a fresh Unity test bundle and run two exact NUnit test names. Replace the Editor version and paths with the installed version. The first command writes under the repository's ignored QA directory.

```powershell
python tools/unity_ci_packages.py prepare --unity-version 6000.0.69f1 --unity-data 'C:/Unity/Editor/Data' --output .unity-ci/order-baseline
python tools/run_test_order.py --prepared .unity-ci/order-baseline --editor 'C:/Unity/Editor/Unity.exe' --a MCPForUnityTests.Editor.Services.DomainReloadOrderControl.Subscribe --b MCPForUnityTests.Editor.Services.DomainReloadOrderControl.CheckSubscribers --repeats 2 --output .unity-ci/order-results
```

The runner starts one Editor per cell for A, B, A→B and B→A, then repeats each condition. Each launch gets new project and package copies checked against the same file manifest. Tests in a pair share the same Editor. The observer records test start and finish callbacks, the domain identity on Play Mode transitions and the settings before, during and after the run. A test that stays in Edit Mode has an empty transition list. Use two other exact test names in the prepared project to check another suspected dependency. Tests that use external services, preferences or files outside the project require their own isolation.

In the supplied negative control, A registers a static listener while playing. B counts listeners during a subsequent Play Mode entry. The predicted failure is B after A. Inspect B's assertion in the fresh NUnit XML and the corresponding transitions and results in `observer.json`. The fixture cleans up the subscription and the observer restores both original Enter Play Mode settings. The input bundle remains available for the next experiment.

`order-report.json` identifies the per-cell artifacts, repeats, observed Unity version and common input digest. Exit code 1 represents a repeated order-dependent failure with passing single-test controls. Exit code 0 means every cell passed. Exit code 2 covers inconsistent outcomes, baseline failures and incomplete evidence. The runner checks the complete NUnit XML against callback case names, counts, outcomes and failure messages. Missing or contradictory evidence leaves the run incomplete. Each confirmed cell records the SHA-256 of its XML. [Unity Test Framework ordered test lists](https://docs.unity3d.com/Packages/com.unity.test-framework@1.4/manual/reference-command-line.html#orderedtestlistfile)
