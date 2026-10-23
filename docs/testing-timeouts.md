# Test waits and slow runners

Correctness tests wait for completion or an explicit phase event. They do not
assume a scheduler will run an operation within one or ten seconds. The separate
hang watchdog in `Server/tests/timing.py` defaults to 60 seconds per completion
phase, returns as soon as the operation finishes, and can be adjusted for the
machine running the tests:

```powershell
$env:UNITY_MCP_TEST_TIMEOUT_SCALE = '3'
python -m pytest tests
```

```sh
UNITY_MCP_TEST_TIMEOUT_SCALE=3 python -m pytest tests
```

Run these commands from `Server` with the repository development environment.
The multiplier must be positive and finite; invalid settings fail explicitly.
There is no machine-speed calibration or sleep added to successful tests.
Choose the multiplier before collection, and ensure any enclosing CI job allows
the resulting total workload. An arbitrarily stalled machine cannot be
distinguished from a deadlock by a finite wall-clock watchdog alone.

The isolated HTTP suite wrapper has a separate 900-second default multiplied by
the same setting. It includes worker startup, fixture setup, the suite and
teardown. It does not set HTTP request deadlines. Its four workers still keep
each file's fixtures together. Owned heartbeat subprocess probes receive the
computed test watchdog explicitly after their environment isolation.

## Authoring tests

- Use `await_signal(event, "phase")` when a fixture exposes a completion event.
  Its own event waiter is released on success, failure and cancellation.
- Use `await_completion(task_or_future, "phase")` when observing an existing
  operation. A watchdog failure does not cancel that operation; inspect its state
  and release or cancel it in the fixture's owned teardown.
- `watchdog_timeout()` can supply existing AnyIO, queue and task waits. Preserve
  their existing cancellation semantics and cleanup instead of globally replacing
  the event loop's timeout functions.
- Keep intentionally short product deadlines, failure-injection delays,
  negative-observation windows and performance assertions distinct. Do not scale
  them with the test watchdog. Prefer injected clocks/events when testing expiry.
- A timeout after dispatch does not prove native work stopped or a mutation did
  not run. Assert the outcome/ownership contract before retrying anything.

The multiplier currently applies to the migrated Python completion watchdogs and
HTTP wrapper. Native execution and external process harnesses have their own
documented budgets; do not assume this setting changes every timeout in the
repository or production.
