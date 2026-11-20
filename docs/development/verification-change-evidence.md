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

Each named result is classified as newly failed, still failing, changed failure,
repaired, still passing, outstanding or incomparable. An existing failure with
a different observed failure signature counts as changed. Exit 1 signals a new
or changed failure. Exit 2 indicates incomplete evidence or incompatible
conditions. Exit 0 reports no new failures in a complete comparison and can
include known pre-existing failures. An unresolved change in selected check
coverage remains visible in the JSON even when a new failure sets exit 1.
