# Reference graph worker lifetime

A graph belongs to the authenticated user, actual Editor instance and project.
Two valid credentials for the same scope reuse its reconciler and durable worker
identity. Each foreground request retains its own exact credential checks; a live
credential from another client cannot authorize a revoked request or an authored
configuration change. Candidate handles are opaque, are never persisted, and do
not cache an authorization verdict.

The detached watcher has an empty request context. Each reconciliation attempt
selects a currently authorized read owner and pins it through actual native,
thread and database cleanup. Waiting between attempts retains no credential owner.
Revocation cancels that attempt; another valid credential can take over only after
its cleanup finishes. Old unused candidate handles expire between attempts, while
the most recent live handle remains available for a pending large job.

Normal polling is five seconds. Consecutive failed attempts back off up to thirty
seconds, and an unused watcher parks after three failures. A caught-up scope with
no active request parks after five minutes without user activity. These are idle
retention and retry policies, not execution deadlines: an active attempt is never
cancelled for its elapsed duration, and a connected job with pending work keeps
reconciling. Explicit stop, credential revocation, actual Editor replacement and
server shutdown still cancel and drain owned work.

Parking removes the live entry, credential candidates and in-memory analysis
caches. SQLite facts, authored declarations, progress and pending generations
remain intact. The next authorized graph request resolves the actual Editor and
resumes from that durable state. A stopped/disconnected scope does not certify
cached evidence as current.

Scope installation has a temporary per-scope coordinator, removed after its last
holder or waiter. The global registry lock covers only registry changes. Database
opening, retiring workers, active request cleanup and cache release happen outside
it, so a large drain for one user cannot block another user's graph. Retired
entries reject further dispatch/publication and cannot restart an untracked worker.
Shutdown also drains database opens already in flight and refuses late installation.
Overlapping resolutions on one user/transport carry an ordered observation fence:
an old status response or delayed database open cannot install an Editor scope
after a newer request has observed its replacement. The fence is released with
the last overlapping resolver, so it does not retain inactive transport identities.
Shutdown cancels pending status lookups as well as installed workers. The final
heartbeat/thread/database release is shielded from repeated cancellation, including
both orderings of actual credential revocation and server shutdown.

The service tests exercise concurrent clients, revocation during active work,
fallback after cleanup, disconnect/reconnect, unchanged durable state, long active
attempts, unrelated-scope availability during database opening/draining, and
shutdown. They use isolated real SQLite stores and controlled transports; no
native Unity input or developer installation changes are required for this unit.
