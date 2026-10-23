# Test Play Mode settings recovery

Before a test run temporarily disables domain reload, Unity MCP saves and verifies a complete recovery record. If persistence fails, admission stops before either Editor setting changes. The record is bound to the project, machine, user and run; it contains original and intended temporary values, a schema version, recovery status and the current setting transition.

Records live under `Library/MCPForUnity/TestSettings/`, with a hashed owner filename. Publication uses the same adjacent, flushed OS-atomic primitives as asset updates. Unknown schemas, missing fields, malformed records and foreign owners cannot authorize restoration or replacement of an active recovery record. No additional package or driver is required.

Each individual apply and restore setter has its intent persisted first. Recovery accepts only that phase's original, before-setter or after-setter values, including Editors where nonzero options automatically enable the setting. Options are applied before the enable flag to avoid Unity replacing empty options with defaults. An unjournaled mixed pair does not gain recovery authority. Restoration changes values only when they still match the recorded transition. If another action changed them, the values are preserved and the recovery record becomes inactive with a conflict status. Successful recovery records completion before best-effort deletion, so an undeleted completed marker is harmless. If completion cannot be persisted, a warning identifies the retained recovery record and a new run cannot overwrite pending ownership.

## Legacy compatibility

The previous version's SessionState originals remain usable in that same Editor process. The old unbound two-line `Library/MCPPlayModeOptionsBackup.txt` file alone does not identify a user or run, so a new process does not automatically apply it. Preserve that file for manual inspection; verify the intended values in **Project Settings > Editor > Enter Play Mode Settings** before clearing it. Existing public test APIs are unchanged.

## Verification scope

The isolated native suite covers publication denial, injected flush and deletion failures, interruption before and after each setter, later settings changes, pending ownership, disk-only restoration, foreign and incomplete records, and existing test-run lifecycle behavior. A separate owned-process probe compiles the production guard with filesystem-backed Unity API stubs and kills only its newly started writer before restarting recovery. This demonstrates process persistence at recorded/applied/external-change boundaries, before and after all four setters, and after a completed marker could not be deleted; it is not a forced crash of a user's Editor or a power-loss transaction.
