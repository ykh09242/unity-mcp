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
