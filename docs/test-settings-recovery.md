# Test settings and recovery

Unity MCP respects the project's **Enter Play Mode Settings**, including Domain Reload
and Scene Reload. New test runs do not temporarily disable either reload. Test jobs
persist their identity and completion state across a domain reload; poll `get_test_job`
after a connection is re-established instead of starting a duplicate run.

## Background test progress

Both EditMode and PlayMode runs request No Throttling before native execution starts.
The temporary `ApplicationIdleTime` and `InteractionMode` preferences are global to the
OS user, so Editors coordinate through a per-user, per-machine recovery journal under
the local application-data directory, in `MCPForUnity/EditorPreferences/`. It contains
only original values, transition state and process IDs with verified process start times.
No project content, API keys or extra driver is required.

The first owner captures the original values; the last live owner restores them.
Domain reload reuses the process ownership, while dead processes and reused process IDs
are distinguished. Each write follows a durable intent, allowing another Editor or the
next startup to finish recovery after a forced termination. Recovery requires an Editor
to run again; there is no background service after all Editors exit.

Manual preference changes are preserved. A mismatching value suppresses further
overrides for the active ownership period. Short lock contention or unavailable storage
does not refuse a test run: settings remain unchanged and maintenance retries. Malformed
recovery metadata is retained for inspection rather than used to overwrite preferences.
Maintenance stops when no run or recovery record remains. No test-duration limit or
scene-size limit is added.

## Compatibility with earlier Play Mode overrides

Older versions temporarily disabled domain reload and recorded the original settings in
`Library/MCPForUnity/TestSettings/`. Those records remain recoverable. They are bound to
the project, machine, user and run, with original and intended temporary values, a schema
version, recovery status and the current setting transition. Publication uses adjacent,
flushed OS-atomic primitives. Unknown schemas, malformed records and foreign owners
cannot authorize restoration or replacement of an active recovery record.

Recovery accepts only the recorded transition's original, before-setter or after-setter
values. If another action changed the settings, recovery preserves them and records a
conflict. Completed records are harmless if best-effort deletion fails. The previous
version's SessionState originals remain usable in that same Editor process.

The old unbound two-line `Library/MCPPlayModeOptionsBackup.txt` file alone does not
identify a user or run. A new process does not automatically apply it. Preserve it for
manual inspection and verify **Project Settings > Editor > Enter Play Mode Settings**
before clearing it. Existing public test APIs remain available.

## Verification scope

Isolated native regressions cover both-mode startup, concurrent ownership and completion
orders, process identity reuse, manual changes, busy storage, interrupted apply/restore
setters, malformed metadata, reload recovery and legacy settings records. Preference
tests use owned journals and an in-memory backend rather than the user's global values.
Actual Windows Editor probes exercise test jobs with Domain Reload both enabled and
disabled, including observed PlayMode transitions and released native admission. These
probes do not establish execution on other operating systems or Unity versions.
