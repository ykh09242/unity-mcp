# Cite an archived Player execution for another commit

Use `unity-mcp verification-evidence reuse` to decide whether an earlier Player
observation still applies to a reviewed commit. For example, a documentation
commit outside the Unity input trees can preserve a Player result. The JSON
names the original job, build, source revision, verification and scenario
outcomes, observed environment, and completion time. A retained failure stays
a failure. The command performs no native execution.

## Bind the scope before verification

From the full CLI installation, capture the immutable Git inputs for the
archived registration. Select every registered input exactly once across
`--source`, `--dependency`, and `--setting`. Options accept project-relative
paths and may be repeated. The tool also records the Git tree IDs of `Assets`,
`Packages`, and `ProjectSettings` under the selected Unity project's Git
prefix. These trees cover omitted and newly added project files without
opening their contents.

```sh
unity-mcp --no-project-context --format json verification-evidence scope \
  --project ./Game --revision "$ARCHIVED_COMMIT" \
  --source Assets/Doors.cs --dependency Assets/Door.prefab \
  --setting ProjectSettings/QualitySettings.asset \
  --runtime-inputs self_contained --environment player-environment.json \
  --output archived-scope.json
```

Supply the expected native environment as a `schema_version: 1` JSON object.
Use the fields from the native Player's `reproduction.environment`, including
`operating_system`, `platform`, `architecture`, `scripting_backend`,
`build_mode`, and `runtime_version`. A deployment that reads remote content,
untracked files, local packages outside the project, or other mutable inputs
needs `--runtime-inputs unknown` unless those inputs are frozen in the
reviewed project and build. Record specific gaps with repeated `--unresolved`
options. The default runtime input coverage is `unknown`.

Merge the printed `observations.verification_scope_sha256` into the candidate
registration's `observations` before running verification. Confirm its printed
`source_revision` matches the registration. Archive the scope, registration
(including any input chunks), terminal verification report and native
`run.json`. The report's manifest digest binds the original scope. Retrofitting
a scope into an existing registration changes that digest.

Repeat the scope command for the target commit and its reviewed environment,
write a new scope file, and bind its digest in the target registration. The
commits must both be available locally. A teammate may use another checkout at
the same repository-relative project prefix. The absolute project path in each
registration records the builder's location.

## Assess the archived result

```sh
unity-mcp --no-project-context --format json verification-evidence reuse \
  --project ./Game --verification archived/verification.json \
  --native archived/run.json --archived archived/candidate.json \
  --target target/candidate.json --archive-scope archived-scope.json \
  --target-scope target-scope.json --bundle target/build/scenario-bundle.json \
  --max-age-seconds 86400 --output reuse.json
```

Choose the maximum age for the consuming check. The example accepts an
execution completed within the preceding day. Missing, nonpositive, future or
expired completion times require a new execution. Use `--closure
target/closure.json` to check a reviewed dependency closure against the
target commit and its expanded registration.

Exit 0 with `state: retained_observation` permits citing the recorded outcome.
The original completion time and outcome always belong to the archived
commit. `native_executed` is false. A CI consumer that requires a passing
observation must inspect `original.verification_state` and
`original.scenario_state`.

Exit 2 with `state: new_execution_required` reports the invalidating changes
or gaps, including changed Git trees or selected inputs, a different build,
definition or environment, outstanding checks, unresolved static references,
or incomplete runtime loader coverage. The original scoped native loader
capture must agree with the verification report, including dropped events and
unobserved resource keys. Runtime hooks cover their instrumented calls.
Self-contained input coverage also requires operator review.

Exit 2 with `state: incomplete` reports an unreadable, inconsistent or
unbound artifact identity, such as a modified registration or scope, a
missing Git object, or a bundle that disagrees with its target registration.
Inspect the cited artifact or run the target verification to obtain new evidence.
