# Compare a change using retained verification evidence

Save the `data` document returned by `manage_verification report`, or its
successful response envelope, on each side. Retain the corresponding Player
`run.json` from the reported child artifact directory. Prepare the candidate
with the baseline registration ID. The comparison checks its captured baseline
manifest hash and source token against the actual baseline report.

```sh
unity-mcp --no-project-context --format json verification-evidence compare \
  --baseline before/verification.json before/run.json \
  --candidate after/verification.json after/run.json --output comparison.json
```

Each native artifact must match the owned, settled child, scenario, source,
build ID, launcher-admitted payload and finalization evidence. Runtime platform,
backend, architecture, definition, repetitions, execution controls and recorded
project identity must agree. A deliberate source or payload change is expected
in a PR pair. The command reads only the paths passed by the caller. An earlier
successful run from another job cannot satisfy the candidate.

An Editor result needs a matching selected check, execution record and
acknowledged, settled Editor child. The records identify the check by kind,
name and definition hash. They also bind the result to the candidate source.
An individual NUnit result needs the exact returned case name and state from
the recorded test job. Older archives without these fields leave the selected
Editor check incomplete. A profile with no observed analysis or test job also
remains incomplete.

Each named result is classified as newly failed, still failing, changed failure,
repaired, still passing, outstanding or incomparable. An existing failure with
a different observed failure signature counts as changed. Exit 1 signals a new
or changed failure. Exit 2 indicates incomplete evidence or incompatible
conditions. Exit 0 reports no new failures in a complete comparison and can
include known pre-existing failures. An unresolved change in selected check
coverage remains visible in the JSON even when a new failure sets exit 1.
The comparison includes outstanding selection reasons such as an unmapped
changed path. Missing final source confirmation, incomplete input chunks and
Player reporting errors prevent the affected run from establishing a regression.
An unrelated incomplete Editor check leaves an independently observed Player
regression visible. Two failed results need comparable failure signatures
to distinguish a changed failure from an existing one.

## Find the first failing archived build

List the intended sequence oldest first in a `verification_build_history@1`
JSON document. Each `builds` entry has a distinct `build_id`, its exact
64-character `source_revision`, and `runs`. Each run specifies two relative
paths named `verification` and `native`, below the manifest directory. Use an
empty `runs` array for a known build whose artifacts were unavailable. Independent
repeats of a build go in the same row. The order comes from the supplied CI
inventory. The conclusion covers the builds in that inventory.

```sh
unity-mcp --format json verification-evidence first-failure history.json \
  --kind scenario --id doors --output first-failure.json
```

The command verifies the recorded build, source and unique jobs on every trial.
Repeated trials of one build must share its candidate manifest and payload
hash. A missing referenced file leaves a gap in that build's history. An
invalid or contradictory archive produces an error with exit 2.
A complete passing prefix followed by consistent failures identifies the first
failing build with exit 1. Consistent passes return 0. Missing artifacts,
contradictory repeats, an unmatched runtime or a later return to passing leave
the range open with exit 2. The result includes the ordered timeline, observed
passing/failing endpoints and unverified builds. For observed Editor checks,
each trial also identifies the Editor jobs in `check_job_ids`.
