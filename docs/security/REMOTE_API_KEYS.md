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
Native command backends distinguish missing records from unavailable storage. macOS
`security` exit 44 is only an absence candidate: the CLI also uses it for discarded
search failures. Search initialization or unknown diagnostics fail closed. A candidate
is confirmed through a status-preserving `SecKeychainFindGenericPassword` query scoped
to the exact UTF8 service/account. Only native `errSecItemNotFound` (-25300) confirms
absence; native success (the item exists), other errors or a failed probe report failure.
The query requests no password length/data or item reference, so no native output
buffer or CF object ownership is transferred. Confirmed absence makes repeated deletion
a no-op. Linux `secret-tool`
lookup/clear status 1 is treated as no match only with empty stdout and stderr; failures
with diagnostics, other statuses, startup failure and timeout throw a sanitized error.
Windows returns absence only for `ERROR_NOT_FOUND`; other `CredRead` failures throw
the same bounded recovery error. A successful native read keeps ownership of its
credential buffer through value decoding and frees it exactly once.
These errors reach the remote recovery boundary instead of returning an empty key.
The connection UI catches storage failures, retains the prior credential/connection
state for failed saves and clears, and shows an actionable message to restore secure
store access and retry. Passive refresh does not mark an unreadable origin as approved.
`RemoteApiKeyStorageTests` use injected in-memory preferences and key stores. Existing
remote configuration, client status, lifecycle snapshot and connection UI fixtures also
replace credential access before exercising these paths; they do not read or write
the user's stored credentials.

Platform result handling follows the official [Apple Security tool source](https://github.com/apple-oss-distributions/Security/blob/main/SecurityTool/macOS/keychain_find.c)
and [libsecret command source](https://github.com/GNOME/libsecret/blob/main/tool/secret-tool.c).
Windows follows the [CredRead contract](https://learn.microsoft.com/en-us/windows/win32/api/wincred/nf-wincred-credreadw)
and [CredFree ownership contract](https://learn.microsoft.com/en-us/windows/win32/api/wincred/nf-wincred-credfree).
The macOS confirmation query follows the optional-output and status contract in the
[Apple SecKeychain header](https://github.com/apple-oss-distributions/Security/blob/main/OSX/libsecurity_keychain/lib/SecKeychain.h).
That legacy macOS API is deprecated but retained by the existing Keychain framework;
if unavailable, or if CLI diagnostics differ from the recognized no-match form, the
operation fails closed and preserves legacy recovery copies. Native absence confirmation
is isolated to macOS; tests inject its exact status and never query a user's Keychain.
`PlatformKeyStoreOutcomeTests` inject command results into the production backends;
they never run native key-store commands or inspect user credentials.
