# A deliberately unmapped EditMode failure

Copy `NavigationMaterialChecks.cs` into an EditMode test assembly in an owned
disposable project. Leave `Assets/__McpNavigationCheckFixture` absent on the
first run. Set `UNITY_MCP_UNMAPPED_CHECK_FIXTURE=1` in the Editor process.
The fixture creates two materials once and reopens the same assets on later
runs. It writes both asset GUIDs and SHA-256 values into the test output.

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
and verdicts in the two NUnit XML files. The narrow run passes its single
check. Broader selection includes `RouteArrowsSupportInstancing`, which fails
because the second imported material disables GPU instancing. The selection
report retains that material path and the `unmapped_affected_paths` reason.

Save the narrow and complete runs as separate NUnit XML files. To measure the
broader fallback, run its fixture independently and save a third XML. Compare
the exact observed input hashes, Editor version, build target and execution mode:

```sh
unity-mcp --format json verification-evidence selection --selected narrow.xml --full full.xml --fallback broader.xml --affected-test VerificationFixtures.NavigationMaterialChecks.RouteArrowsSupportInstancing
```

The result lists missed failures, observed duration differences, disagreements
and checks outside the supplied fault's known affected set. Omit `--affected-test`
when that set is unknown. Each case records `MCP_VERIFICATION_CONTEXT` and its
input asset path, GUID and byte digest. Missing context, changed inputs, and reused
artifacts cannot establish a matched comparison. A timing delta is one paired
observation and includes time spent by the NUnit run. Exit 2 indicates incomplete
evidence. Exit 0 means the comparison was measured, including measured misses.
