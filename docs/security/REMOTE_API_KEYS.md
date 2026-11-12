# Remote API key persistence

Remote HTTP API keys use the existing platform secure store: Windows Credential
Manager, macOS Keychain, or Linux libsecret with the existing encrypted-file fallback
when no supported native store is available. Plaintext EditorPrefs are no longer a
write destination. Store failures fail the operation; no plaintext fallback is used.

The record identifier is `remote-http-` plus the SHA256 digest of the normalized
origin (scheme, lowercase IDN host and effective port). Paths and queries share the
same origin record. Different schemes, hosts or ports have separate records. Provider
environment-variable overrides do not apply to these remote credential records.

An existing origin-specific EditorPrefs key migrates on its first read. A successful
secure-store write must precede deleting the plaintext preference. Migration failure
preserves the legacy value and fails the read, preventing insecure fallback. An
existing secure record wins over stale plaintext. Explicit removal deletes the secure
record before clearing the origin-specific legacy preference; failed removal preserves
the legacy recovery value and reports failure.

The older global key has no trustworthy origin. It remains unavailable for connection
use until the user explicitly binds it to the currently displayed origin. Binding uses
the same secure write and only then deletes the global preference. The Clear API Key
button removes the current origin's secure/legacy records and the unbound global
preference, in that order. A failed secure deletion stops before clearing legacy values.
Clearing the input field removes only that origin's records. Neither operation removes
credentials belonging to another origin. Local deletion remains available after remote
HTTP policy is tightened.

Credential failures expose a generic recovery message, never backend command output.
The connection UI catches storage failures, retains the prior credential/connection
state for failed saves and clears, and shows an actionable message to restore secure
store access and retry. Passive refresh does not mark an unreadable origin as approved.
`RemoteApiKeyStorageTests` use injected in-memory preferences and key stores. Existing
remote configuration, client status, lifecycle snapshot and connection UI fixtures also
replace credential access before exercising these paths; they do not read or write
the user's stored credentials.
