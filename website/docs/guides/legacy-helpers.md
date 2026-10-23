# Migrating legacy editor helpers

`McpJobStateStore` and `ProcessTerminator` remain available with deprecation warnings.
Their public signatures and Unity metadata are retained for external extensions.
Neither helper is called by the repository's production code. The
`IProcessTerminator` interface and `ServerManagementService` constructor parameter
remain compatible; injecting a terminator does not enable PID-based server shutdown.

## Tool observations and durable execution

`McpJobStateStore` is legacy project-local JSON scratch storage. Its writes are not
atomic, reads are unbounded, names are not safe for untrusted input, and files do not
isolate users or Editor instances. Do not use it for credentials or crash recovery.
A saved `running` status does not prove an operation survived a reload or restart.

For small, transient observations within one Editor session, use
[Unity SessionState](https://docs.unity3d.com/6000.0/Documentation/ScriptReference/SessionState.html)
with a namespaced key. It survives assembly reloads and is cleared when the Editor
exits. It is not a durable restart or crash-recovery store.

Operations that must survive an Editor restart need an operation-owned, versioned
journal: validate project/user identity, retain an exclusive execution owner, bound
individual records, atomically publish completed writes, and reconcile ambiguous
submission with the actual provider. Preserve uncertain outcomes and inspect them;
do not automatically submit a mutation again just because its observer disappeared.
Existing operation-specific stores are not a generic persistence API for extensions.

Migrate only known extension-owned legacy files after validating their names and
contents. Retain an ambiguous checkpoint for inspection; do not treat a missing or
malformed legacy file as evidence that work never executed. No automatic migration
or deletion of legacy files is performed.

## Stopping a managed server

Call the owning `ServerManagementService` instance's
`StopManagedLocalHttpServer()` for a managed local server. This uses the current
ownership and authenticated shutdown protocol. A refusal or unconfirmed response
must be inspected; it is not permission to invoke a PID-only fallback.

`ProcessTerminator.Terminate(int)` retains its old behavior for compatibility,
including platform-specific force termination. A PID alone cannot establish that
a process is the server this Editor owns. New integrations should use the service
instead. The compatibility tests exercise only invalid/self PID refusal; the active
server ownership tests cover the supported shutdown path.
