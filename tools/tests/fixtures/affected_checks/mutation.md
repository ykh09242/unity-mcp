# Measure which Unity tests catch a planted fixture fault

Use an owned disposable Unity project with Unity Test Framework and uGUI. Copy
`MutationVerificationChecks.cs` and `VerificationMutationTests.asmdef` from
this folder to the project's `Assets/VerificationMutationTests/` folder.
Set `UNITY_MCP_MUTATION_FIXTURE=1` in the Editor process environment. Leave
`Assets/__McpMutationChecks/` absent on the first clean run. The fixture creates
its scene and inputs once. Keep their `.meta` files together with the assets for
all runs. Run the fixture in isolation so scene, UI and project state stay stable.

Each suite case exercises Unity functionality against the imported fixture.
The loader uses `Resources.Load<TextAsset>`. The scene check opens the configured
Unity scene additively and reads its anchor. The input check routes a left click
through uGUI `ExecuteEvents.ExecuteHierarchy` and observes the target button
callback. This is a routed event test at the uGUI dispatch boundary. Physical
input, hit-testing, Player builds and devices require their own controls.

| Mutation ID | Asset below `Assets/__McpMutationChecks/` | Replace clean bytes with | Held-out witness method |
| --- | --- | --- | --- |
| `loader-key` | `PrimaryKey.txt` | `McpMissingMutationCatalog` | `PrimaryCatalogResource` |
| `scene-reference` | `SceneKey.txt` | `LostCheckout` | `CheckoutSceneAnchor` |
| `routed-input` | `PointerRoute.txt` | `intercept` | `PurchaseRoute` |
| `fallback-loader-key` | `FallbackKey.txt` | `McpMissingMutationCatalog` | `FallbackCatalogResource` |

1. On clean inputs, run all of `VerificationFixtures.MutationSuite` and save
   `baseline.xml`. Save the clean bytes of each input you intend to vary.
2. Execute only the corresponding `VerificationFixtures.MutationWitnesses`
   method on clean inputs and save `witness-clean.xml`. The witness test already
   exists before the fault and has a separate inventory from the evaluated suite.
3. Change exactly one fixture asset according to the table. Keep the same asset
   paths and GUIDs. Run a chosen subset of `MutationSuite` and save `selected.xml`.
4. Independently run all of `MutationSuite` on the same mutated inputs and save
   `full.xml`. Run only the corresponding held-out witness and save
   `witness-mutant.xml` from its independent execution. Keep the original XML
   and logs even if Unity exits nonzero for an assertion failure.
5. Measure the result. Restore the clean bytes and repeat for a different fault.

For each capture, use the installed Unity Editor executable, the same project
and a distinct results and log path. Use a separate Editor run per capture.
For example, this full-fixture filter keeps the held-out witness outside the
evaluated inventory.

```sh
"$UNITY" -batchmode -nographics -projectPath "$PROJECT" -runTests \
  -testPlatform EditMode -assemblyNames VerificationMutationTests \
  -testFilter 'VerificationFixtures.MutationSuite' \
  -testResults "$RESULTS/full.xml" -logFile "$RESULTS/full.log"
```

Use the complete method name as the filter for a one-test selection or the
held-out witness. To study a selected catch for `loader-key`, select
`VerificationFixtures.MutationSuite.PrimaryCatalogLoads`. To study a selected
miss, select `VerificationFixtures.MutationSuite.PurchaseReceivesClick`.
The fallback-key mutation exercises a branch outside the evaluated suite.

```sh
unity-mcp --no-project-context --format json verification-evidence mutation \
  --mutation-id loader-key --mutated-asset Assets/__McpMutationChecks/PrimaryKey.txt \
  --baseline baseline.xml --selected selected.xml --full full.xml \
  --witness-test VerificationFixtures.MutationWitnesses.PrimaryCatalogResource \
  --witness witness-clean.xml witness-mutant.xml --output loader-key.json
```

The evaluator requires all baseline suite cases to pass, the clean witness to
pass, and the mutated witness to fail with the corresponding assertion marker.
It compares the observed context and asset path, GUID and hash evidence. Only
the named fixture asset may change. A suite catches the fault when an observed
case fails with that same marker in its NUnit failure message. Reports retain
the executed cases, catching cases and unmatched failures in each scope.

| State | Exit | Meaning |
| --- | ---: | --- |
| `caught_by_selection` | 0 | The complete control and the selected checks witnessed the fault. |
| `missed_by_selection` | 1 | The full suite witnessed the fault and the selected cases passed. |
| `missed_by_full` | 1 | The held-out witness proved the mutation while the full suite passed. |
| `incomplete` | 2 | A control was invalid, unavailable, unresolved or inconsistent. |

To compare an older selection rule with a new one, run them independently on
the same planted fault and produce one report per selection. Keep the clean,
full and held-out controls identical between those comparisons. Compare the
individual `state` and `caught_tests` results across the declared fault set.
Keep full-suite escapes in the denominator when scoring demonstrated mutations.
Report incomplete rows separately. Retained evidence measures this fixture and
the captured execution conditions.
