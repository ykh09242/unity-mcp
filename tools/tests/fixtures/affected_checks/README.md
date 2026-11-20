# A deliberately unmapped EditMode failure

Copy `NavigationMaterialChecks.cs` into an EditMode test assembly in an owned
disposable project. Leave `Assets/__McpNavigationCheckFixture` absent on the
first run. Set `UNITY_MCP_UNMAPPED_CHECK_FIXTURE=1` in the Editor process.
The fixture creates two materials once and reopens the same assets on later
runs. One-time fixture setup creates a fresh execution ID for each run. The
test output records that ID, both asset GUIDs and SHA-256 values, and the names
of both fixture tests even when NUnit selects only one.

Use these project-owned check mappings in a Player candidate manifest. Include
both material inputs in the candidate change.

```json
{
  "mappings": [{
    "path": "Assets/__McpNavigationCheckFixture/MapMarkers.mat",
    "tests": ["VerificationFixtures.NavigationMaterialChecks.MapMarkersSupportInstancing"]
  }],
  "broader_check": "navigation-full"
}
```

Run the named test as the narrow control. For the configured `navigation-full`
check, run the entire `VerificationFixtures.NavigationMaterialChecks` test
fixture in the same Editor project. Compare the test identities, input digests
and verdicts in the two NUnit XML files. The expected narrow result is one pass.
The broader selection is expected to fail `RouteArrowsSupportInstancing`
because the second imported material disables GPU instancing. The selection
report retains that material path and the `unmapped_affected_paths` reason.

Save the narrow and complete runs as separate NUnit XML files. To measure the
broader fallback, run its fixture independently and save a third XML. Compare
the exact observed input hashes, Editor version, build target and execution mode:

```sh
unity-mcp --format json verification-evidence selection --selected narrow.xml --full full.xml --fallback broader.xml --affected-test VerificationFixtures.NavigationMaterialChecks.RouteArrowsSupportInstancing
```

Keep the original Unity test output. `MCP_VERIFICATION_EXECUTION` must differ
between runs and agree across observed cases in each run, so saving or reformatting
the same XML again cannot supply a second run. `MCP_VERIFICATION_CONTEXT`, the
input observations and `MCP_VERIFICATION_EXPECTED_CASE` inventory must agree
across the compared runs.

The full run needs a pass or fail for every declared fixture case. Reports show
which checks lack a definitive result and count passes and failures as executed.
A selection or fallback comparison with missing full checks, skipped or
inconclusive checks, or divergent outcomes leaves `missed_failures`,
`unnecessary_checks` and `measured_seconds_saved` unset (`null`). Omit
`--affected-test` when the fault's affected set is unknown. A timing delta is
one paired observation and includes time spent by the NUnit run. Exit 2
indicates incomplete or invalid evidence. Exit 0 means the comparison was
measured, including measured misses.
