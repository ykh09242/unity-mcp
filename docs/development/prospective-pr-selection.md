# Prospective PR test selection

The [hosted workflow](../../.github/workflows/prospective-pr.yml) enrolls the first
three actual PRs originally opened against beta after rollout. Activation uses
GitHub's timestamp for the beta push whose before/after Git history first
contains the workflow. This covers a push that delivers several commits.
Opening order uses GitHub creation time, then PR number. The collector lists
all PRs across every state and target. For each opening, the first base-change
event supplies the previous branch. A confirmed zero changes uses the current branch. A missing target history reserves a possible slot until it can be
resolved. A missing activation receipt leaves the population undetermined.

A metadata-only `pull_request_target.opened` workflow preserves the opening
timestamp, number, base and head. It has no checkout or token permissions.
If GitHub suppresses this event or the receipt expires, the enrolled PR stays
in its slot with an unknown opening SHA and results. Retargeting, closure,
cancellation and reruns preserve the original order.

The trusted beta cohort job runs at rollout, after ordinary PR controls finish,
and on manual dispatch. Its JSON artifact and job summary give the roster and
measured totals. Retain them and their run links beyond the 90-day artifact
window. At rollout, zero openings legitimately yields three awaiting slots.

## Hosted tools-suite controls

Before opening a PR, put the human choice in its initial description. Use up
to 32 exact pytest node IDs or test module paths, one per line.

````markdown
```prospective-tests
tools/tests/test_prospective_pr.py
```
````

The opening event supplies the human author, timestamp, base and head. A missing,
ambiguous or invalid block leaves the manual control unavailable. An empty block
declares an empty selection. After collecting the complete `tools/tests` inventory
on Python 3.14, the MCP ranking policy applies direct mappings for changed test
modules and their namesake production modules in `tools/`. The plan expands
matches into exact node IDs and retains the ranking and unmapped paths.
It uploads both lists, the full inventory and the UTC cutoff before starting any
of the three observation jobs. This hosted scope measures fork infrastructure.

Full, manual and automatic controls run in separate GitHub-hosted jobs with the
same opening SHA, Python version and locked dependencies. The full invocation
collects the entire suite afresh. The report compares every named failed case,
including setup and teardown failures. `missed_full_failures` lists observed
full failures absent from the selected failures. `additional_failures` lists
selected failures that passed in full. Skipped full cases are counted separately.
`additional_full_failures_caught_by_automatic` compares the two policies against
the same full run. `seconds_saved` subtracts the actual elapsed pytest invocation
time (including collection) from the full invocation time. Empty selections have
no duration. Installation and runner queue time are outside this measurement.

The cohort job downloads raw artifacts by the immutable IDs returned by the
producer jobs. It checks the GitHub archive digests, opening base, head,
producer and reusable workflow blobs, base observer revisions, plan inventory,
job membership, host exit markers and timing before recomputing comparisons.
The ordinary PR jobs run candidate tests on read-only hosted runners without
secrets. Per-case observations come from pytest in the candidate process.
GitHub independently observes job identity, time bounds and terminal status.

Open the trusted cohort summary or download `prospective-cohort-RUN_ID-ATTEMPT`.
A missing full execution, incomplete inventory, changed producer workflow or
expired evidence stays in the roster. Unknown quantities appear as JSON null.
The regression tests for the report use synthetic controls and contribute no
PRs to these counts.

## Team Unity projects

An instrumented NUnit project can use the same pre-run procedure with its own
real Unity jobs. Use a locked Server development environment and the
[archive receipt format](verification-test-budgets.md#replay-paired-historical-controls).
In `inputs.json`, place the fields of one replay row at the top level and add
`schema: prospective_nunit_inputs@1`, a positive `budget_ms`, and a
`manual_source` identifying the independently retained operator submission.
Supply the PR URL, candidate `commit`, `draft`, `mappings`, `history`,
`closures` and `manual_ranking` receipts available at planning time.

```sh
uv run --project Server --locked python tools/prospective_nunit.py freeze inputs.json plan.json
```

Publish the immutable plan as the CI planning job's artifact. Start the three
independent Unity invocations only after that job succeeds. Use the exact
`plans.manual.test_names` and `plans.automatic.test_names` filters, and the
whole instrumented inventory for full. Skip an empty filter. Save each XML and
its CI receipt. Add `manual`, `automatic` and `full` receipts to the original
input manifest. Each result receipt also needs the actual runner `started_at`
from CI, in addition to its source commit, archive time, path and byte digest.
Keep genuine missing results absent.

```sh
uv run --project Server --locked python tools/prospective_nunit.py assess inputs.json plan.json result.json
```

Assessment rechecks the retained planning inputs, independent native execution
IDs, archive chronology, all declared candidate input hashes, context and inventory. An independently
archived plan and trusted CI timestamps establish when the inputs became
available. Keep each opened PR in the team's roster even when assessment has
no full result. Native durations cover NUnit execution, and native results
are reported separately from the hosted tools suite.
