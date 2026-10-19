# Tool consent scopes

Tools that require explicit consent are off until the local Editor user grants them for the current project. This setting applies to callers already authorized to use that Editor. It does not replace credential permissions, native admission checks or transport ownership checks, and it is not a sandbox against trusted code already running inside Unity.

In the Tools panel, enabling an unconsented tool asks for one of two scopes:

- **This Editor session** is the default. It belongs to the current project and Editor lifetime. Unity SessionState retains it across assembly reload; closing the Editor clears it.
- **Remember for this project** persists the grant in the current OS user's Editor preferences. It is not a grant for another project's Editor.

Cancel leaves the tool unchanged. **Choose consent scope** explicitly replaces the previous scope; choosing a session grant removes any remembered project grant. **Revoke consent** clears both scopes and disables the tool. Bulk enable does not create dangerous grants or retry a failed grant. Bulk disable revokes them.

Project scope uses the normalized absolute project root, independently of the transport's project hash or connection/session identifiers. Opening another root or moving/copying the project requires new consent. A reconnect, tool rescan, settings retry or execution-receipt lookup cannot create or renew a grant. Old global consent and enablement preferences are preserved for inspection but do not authorize the scoped policy; choose a scope again for the current project.

Consent and dangerous-tool enablement publish together. The service first denies the project/tool, verifies each scope and enablement write or deletion, then publishes only the fully verified state. Failed publication stays denied in the current managed domain. The compatibility `SetToolEnabled(name, true)` can create a session grant only for a clean first enable; after a failed grant or revoke, it performs no implicit retry. Recovery requires a new explicit scope choice.

Revocation reports what storage actually confirmed. A denial latch survives service replacement and service-locator Reset in the current managed domain. A verified SessionState denial protects through assembly reload in that Editor. Verified removal of the remembered grant and disabled project enablement also establishes restart denial. If every relevant storage operation fails while an old grant remains, only current-domain denial is guaranteed: reload or restart may restore that old grant. The panel retains the unconfirmed-persistence warning and offers explicit retry; it does not describe that state as persisted revocation.

Native preference, SessionState and project-identity access occurs on the Editor main thread. Synchronous off-thread queries deny and setters do nothing before accessing those stores. WebSocket registration continues to use its existing asynchronous main-thread capture, so a worker query is not substituted for an authoritative catalog capture.

Native admission rechecks effective enablement at dispatch even when a remote catalog is stale. A batch also rechecks each child. If consent for `batch_execute` is revoked while a child is active, that child may settle; the next boundary leaves the remaining suffix `not_run` with `batch_disabled`. No unrun item receives attempted/progress credit. Initial parent admission remains the responsibility of the existing dispatcher/preview path; this does not create a sandbox around arbitrary trusted in-process calls to a handler. Revocation neither rolls back entered work nor interrupts a retained execution-receipt worker or domain job. Historical receipt lookup/replay keeps its existing authority and retention contract.
