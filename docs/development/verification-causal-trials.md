# Separate a game repair from a changed test

Use four independently executed verification runs to check whether an updated
oracle accepts the original broken game. Retain each exact registered
`candidate.json`, terminal verification report, and settled Player `run.json`.
For schema v2 registrations, keep the digest-named files in `input-chunks/`
beside each candidate. Both analyzers accept v2 root manifests up to 2 MiB.
The v1 root and each individual input chunk have a 64 KiB limit.
CI can execute the four combinations in successive
disposable copies at the same project path. The offline analyzer only reads
the selected archive. See the [registered Player workflow](../verification-profiles.md#verify-a-registered-standalone-player).

```sh
unity-mcp --no-project-context --format json verification-evidence oracle-matrix matrix.json --output result.json
```

Use the following structure in `matrix.json`. Replace angle-bracketed values
with the full lowercase SHA-256 digest from the registered inventory or
executed definition. All artifact paths are relative to this file.

```json
{
  "schema": "verification_oracle_matrix@1",
  "check": {"kind": "scenario", "id": "doors"},
  "games": {
    "old": {"Assets/Door.cs": "<broken-source-sha256>"},
    "new": {"Assets/Door.cs": "<repaired-source-sha256>"}
  },
  "oracles": {
    "old": {"definition_hash": "<original-scenario-hash>"},
    "new": {"definition_hash": "<changed-scenario-hash>"}
  },
  "trials": [
    {"game": "old", "oracle": "old", "candidate": "oo/candidate.json", "verification": "oo/verification.json", "native": "oo/run.json"},
    {"game": "old", "oracle": "new", "candidate": "on/candidate.json", "verification": "on/verification.json", "native": "on/run.json"},
    {"game": "new", "oracle": "old", "candidate": "no/candidate.json", "verification": "no/verification.json", "native": "no/run.json"},
    {"game": "new", "oracle": "new", "candidate": "nn/candidate.json", "verification": "nn/verification.json", "native": "nn/run.json"}
  ]
}
```

Each candidate must contain exactly the union of that row's game inputs and
that column's oracle inputs. Include all game and test files that can differ,
including metadata, in the two disjoint inventories. Put compiled scenario or
test source under `oracles.old.inputs` and `oracles.new.inputs`. For a named
NUnit check, set `kind` to `test`, supply its exact case name and its recorded
selection `definition_hash`, and capture the test source for both revisions.
The selection hash can stay constant when the test implementation changes.
For a Player state observation, use `kind: observation`, the exact scenario
name, and the two `expected_state` JSON objects in place of the definition
hashes. Both runs must use the same registered log marker and scenario.

The analysis checks every manifest digest, complete source inventory, finalized
native receipt, selected check and comparable runtime controls. A repeated
execution cannot satisfy a second cell. Identical captured source inventories
require the same launcher-admitted payload. For compiled oracle changes that
alter the payload, include the relevant source inputs in the oracle inventory.
Conclusions apply to the registered input scope and named check. Keep external
build inputs, data, runner and checkout location controlled across CI jobs.

With an original failure and a final pass, the two crossed executions determine
the result.

| Old game + new oracle | New game + old oracle | Result |
| --- | --- | --- |
| Failed | Passed | `repair_supported` |
| Passed | Failed | `weakened_oracle` |
| Passed | Passed | `repair_and_weakened_oracle` |
| Failed | Failed | `joint_change_required` |

Exit 0 reports `repair_supported`. Exit 1 reports the other three outcomes or
an endpoint pair that did not demonstrate repair. Exit 2 identifies missing,
replayed or incompatible evidence. Inspect each cell's job IDs and diagnostics.

## Find the smallest observed source repair

Use `verification_change_trials@1` with the same `check` structure. Copy the
complete path-to-SHA256 maps of the endpoints into `broken_inputs` and
`repaired_inputs`. Start with an empty `trials` list to discover the atomic
groups. Each group ID is the project-relative asset path. A changed asset and
its metadata stay together, including additions and deletions. Capture both
members on each side where the asset exists. Supported project settings and
package lock files form individual groups. Use `together` for a rename or
another indivisible change, for example
`"together": [["Assets/OldDoor.cs", "Assets/Door.cs"]]`. The first path in
alphabetical order becomes the merged group's ID.

```sh
unity-mcp --no-project-context --format json verification-evidence minimize-changes trials.json
```

Record the broken endpoint with `"groups": []` and the repaired endpoint
with every group. Each trial also supplies `candidate`, `verification` and
`native` exactly as in the matrix. The returned `next_trials` contains
group IDs to apply to the broken source and the expected source digest for
each resulting trial. For example, this command builds a new ZIP from
previously extracted endpoint snapshots.

```sh
unity-mcp --no-project-context verification-evidence export-change-subset trials.json \
  --broken-root snapshots/broken --repaired-root snapshots/repaired \
  --group Assets/Door.cs --archive trial-door.zip
```

The ZIP contains all selected inputs at their project-relative paths.
`overlay.json` records the complete digest map and `absent_paths`. In a fresh
disposable copy of the broken endpoint, apply those paths and remove each
listed absent path. Build and register that exact input set, execute the same
check and append its three archive paths and selected groups to `trials`.
Repeat the analysis with the additional receipts. The exporter checks every
source byte, reads at most 4 MiB per file and 32 MiB in total, and creates a
new file outside the endpoint roots. The destination filesystem must support
hard links for atomic publication. Use the normal candidate exporter to transfer a
registered [Player input proof](../verification-candidate-portability.md).

Only the same observed failure signature counts as reproducing the broken
endpoint. Choose a Player scenario with a recorded failure signature or a
state observation with distinguishing observed values. An exact NUnit case
whose retained result lacks failure details leaves minimization incomplete.
Repeated divergent observations, a changed oracle, an incomplete endpoint,
or another failure prevent a causal conclusion. Up to eight independent
groups, the analyzer requests every unmeasured smaller cardinality before
reporting `minimum_cardinality`. Above eight groups it probes partitions,
complements and individual removals. Its `one_minimal` result establishes
that removing any single remaining group reproduced the original failure.
An interacting set of several changes can be the smallest observed repair.
The limits are 128 groups, 512 trial records and 128 MiB of selected JSON
input per analysis. Exit 0 identifies a witnessed minimum under the indicated
criterion. Exit 2 preserves incomplete evidence and pending trials.
