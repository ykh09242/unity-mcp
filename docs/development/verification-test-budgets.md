# Compare ranked Unity tests under time budgets

Rank the candidate's relevant tests highest first. Use exact NUnit case names
and record why each case has that priority. Include low-priority tests in the
list so the plan can account for them.

```json
{
  "schema": "verification_test_ranking@1",
  "tests": [
    {"name": "VerificationFixtures.MutationSuite.PrimaryCatalogLoads", "reason": "Changed loader key"},
    {"name": "VerificationFixtures.MutationSuite.SceneTargetResolves", "reason": "Shared checkout startup"},
    {"name": "VerificationFixtures.MutationSuite.PurchaseReceivesClick", "reason": "Checkout smoke check"}
  ]
}
```

Gather prior Unity NUnit XML that records the fixture execution context, input
digests, fixture inventory, unique execution ID and each observed case duration.
Use independent runs under the candidate's Editor version, build target, mode
and OS. Use the same execution conditions for all controls. The
[instrumented fixture](../../tools/tests/fixtures/affected_checks/mutation.md)
shows the evidence format. Up to 16 timing artifacts can be supplied by
repeating `--history`. Reserve some of the larger historical times for a
representative cold run if Editor import or initialization commonly affects the
fixture's NUnit run time.

```sh
unity-mcp --no-project-context --format json verification-evidence budget-plan \
  --ranking ranking.json --history history.xml --budget-ms 120000 --output plan-2m.json
unity-mcp --no-project-context --format json verification-evidence budget-plan \
  --ranking ranking.json --history history.xml --budget-ms 600000 --output plan-10m.json
```

The planner reserves the largest observed nonnegative difference between NUnit
run time and summed case times across history runs with timing for every case
in that run. It estimates each test at its largest observed passed or failed
case duration, rounded up to milliseconds. It walks the supplied order and
selects each case that fits. A deferred expensive case can leave room for a
cheaper case later in the order. Each row retains the rank, reason, sample count,
estimate and disposition. Unknown timing, missing overhead and unranked known
cases stay visible. A plan with deferred tests has state `incomplete` and exit 2
even when its `test_names` is usable as a narrower selection.

The estimate covers sequential NUnit execution. Record separate Editor launch,
build or import time if it is part of the PR gate. Allow a CI deadline to enforce
an operational wall-clock limit. Planning yields no execution verdict.

## Compare 2 minutes, 10 minutes and full on the same candidate

1. Produce both plans before executing the candidate controls. Preserve the
   rankings and history files used to make the plans.
2. Execute each plan's exact `test_names` on the same saved candidate and preserve
   separately generated `selected-2m.xml` and `selected-10m.xml`. If a plan has
   no test names, leave its selection unrun. Supplying an empty filter to a Unity
   runner can start the full suite.
3. Independently execute the full fixture inventory on that candidate. Keep
   `full.xml` as the control for both comparisons. Preserve the original XML
   files and runner logs from all three invocations.

```sh
unity-mcp --no-project-context --format json verification-evidence budget-assess \
  --ranking ranking.json --history history.xml --budget-ms 120000 \
  --selected selected-2m.xml --full full.xml --output assessment-2m.json
unity-mcp --no-project-context --format json verification-evidence budget-assess \
  --ranking ranking.json --history history.xml --budget-ms 600000 \
  --selected selected-10m.xml --full full.xml --output assessment-10m.json
```

Omit `--selected` for an unrun selection. The command recomputes the plan and
checks the exact selected case inventory, matching candidate observations and
independent execution IDs within each comparison. Compare the two assessment
reports' `full.execution_id` and `selected.execution_id` fields to confirm that
they share one full control and use distinct selections. Input equivalence
covers what the fixture instruments. The two run durations and `measured_seconds_saved`
come from the retained NUnit evidence. A negative saving means the selected run
took longer. `missed_failures` lists failing full cases absent from a decisive
and comparable selected run. For an empty plan, it lists the full failures
without attributing any elapsed time to an unrun selection. A missing selected
case from a nonempty plan, an incomplete full inventory or divergent common case
verdicts leaves the quantitative comparison unresolved.

Exit 0 indicates full planned coverage and a comparable result within the
observed NUnit budget. Deferred checks, inventory drift, incomparable evidence
and actual budget overruns return exit 2. An incomplete report can still contain
observed savings and misses where the exact planned subset and full control prove
them. `full.failed_checks` shows the full verdicts regardless of the time budget.

To measure sensitivity to deliberately planted faults, see the
[mutation fixture procedure](../../tools/tests/fixtures/affected_checks/mutation.md).

## Propose the ranking from a committed PR

Save the committed Git candidate draft, the project's mapping document and one
dependency-closure report per relevant entry scene. A mapping document has a
`mappings` array of objects with a `path` and exact NUnit names in `tests`.
Generate closures using [the scene dependency procedure](../verification-dependency-closure.md).
Use the same base, head and candidate source revision for the draft and closures.

```sh
unity-mcp --no-project-context --format json verification-evidence budget-rank \
  --draft pr-draft.json --mappings project-mappings.json --closure checkout-scene.json \
  --history earlier.xml --output ranking.json
```

The ranking gives direct mappings priority, then mappings for scenes with a
retained dependency route to the changed asset, then remaining tests from the
earlier NUnit inventory. Within a tier it prefers more prior failed executions,
then more direct matches, then more total matches, with exact name breaking ties.
Each proposed test records its contributing paths, scene dependency edge IDs and
prior failing execution IDs. Metadata changes fall back to mappings for their
asset. Paths absent from both direct and scene mappings appear in `unmapped_paths`.
The draft's count of skipped unsupported paths remains visible. Scope extends
only to the change paths captured by the draft. Scene coverage, unresolved
diagnostics and graph/commit binding come from each supplied closure. An earlier
static graph can omit runtime-only dependencies. Review these gaps before using
the ranked names in a narrower gate. Pass `ranking.json` to `budget-plan`; exit
2 from `budget-rank` retains available proposals and incomplete coverage.

## Replay paired historical controls

```sh
unity-mcp --no-project-context --format json verification-evidence budget-replay \
  replay.json --output replay-result.json
```

Supply `schema: verification_pr_replay@1`, `cohort_kind: historical` (or
`synthetic_fixture`), one positive `budget_ms` and an oldest-first `prs` array.
Each row identifies the PR, its full Git head in `commit` and the independently
recorded selection deadline in `plan_at`. Use ISO 8601 timestamps with a timezone.
Record an absent planning deadline as missing, with `archived_at` supplying
the ordering time for that gap. Use one row for every PR in the chosen cohort.

| Row field | Archived evidence |
| --- | --- |
| `draft`, `mappings` | Committed candidate-draft JSON and contemporaneous project mapping JSON. |
| `closures`, `history` | Lists of scene closure JSON receipts and prior instrumented NUnit XML receipts. An empty closure list documents that no scenes were supplied. |
| `manual_ranking` | Independently retained operator ranking in the format consumed by `budget-plan`. |
| `automatic`, `manual`, `full` | Three separately executed NUnit XML captures under matching conditions on the candidate. A genuinely empty planned selection can omit its selected XML. |

Each artifact receipt contains its archive-relative `path`, exact byte
`sha256`, producing Git `commit`, and `available_at` timestamp. Obtain those
fields from the CI archive index or an equivalent trusted record. The reader
checks the supplied timestamps, commits and digests. A local file's modification
time cannot establish historical availability. Plan inputs must already have
been available at the deadline. Timing history must use earlier executions from
other commits. The three evaluation receipts must follow the deadline and have
the candidate commit. Candidate bytes observed in both the Git draft and the XML must
match. Keep a version 2 draft's content-addressed input files in the sibling
`input-chunks/` directory. Missing or stale chunks leave the PR incomparable.
Before ranking any PR the reader cross-checks archive digests and native NUnit
execution IDs across available history and controls, including partial PR rows.
Contradictory receipts exclude every affected row, including re-exports of
the same execution with different XML bytes. Comparability is limited to the fixture's instrumented
inputs and environment. A missing historical manual choice or selected run
remains a gap even when the full XML lists all its cases.

Both planners use the same history and budget. The reader checks the selected
names against the recomputed plans and compares independent executions with the
same full control. Each paired row preserves full failure names, missed cases,
actual NUnit seconds, and observed budget overruns for the two approaches.
Summary metrics count only paired PRs. A genuinely empty selection can have
measurable misses with no measured elapsed time. A negative value for seconds
saved indicates a slower observed selection. Exit 2 identifies a missing or
incomparable row. A paired report can document an overrun or a missed failure.

### Available PR archives

The checked-in [historical inventory](verification-pr-inventory.json) selects the
three most recent PR-numbered first-parent merge subjects on `beta` through
`7367cafd7`. GitHub records their exact head commits and merge times. The PR
check listings show Python server/tools suites, bootstrap or reference-graph
checks, and no matched automatic/manual/full instrumented NUnit controls.
The inventory therefore has **3 historical PRs, 0 paired PRs, 0 timed pairs**.
Misses, elapsed savings and overruns are unknown for both policies. The
two-minute budget in this inventory is the requested comparison setting.
Details are in [PR 320](https://github.com/ykh09242/unity-mcp/pull/320),
[PR 321](https://github.com/ykh09242/unity-mcp/pull/321) and
[PR 325](https://github.com/ykh09242/unity-mcp/pull/325).

The synthetic command test uses a ten-second budget, one planted failing case
and a 40-second full run. Its automatic selection catches the failure in a
12-second run. Its manual selection misses the failure in an eight-second run.
These are fixture inputs for checking the report arithmetic and cutoff rules.
