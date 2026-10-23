# Private store initialization and recovery

The optional execution receipt store and test-wait journal use separate, explicitly
configured private directories. Each keeps one OS writer lock for its complete
lifetime. Existing user, project, authorization, quota and record-format checks
continue to apply; initializing either store does not enable the feature.

Owner and installation markers are written to exclusive private staging files,
flushed, and atomically published while holding the writer lock. POSIX also flushes
the containing directory. Windows retains the existing file flush/replace behavior
and requires the existing private inherited ACL boundary. Recovery from ordinary
process termination is tested; these checks do not simulate power-loss durability.

On restart, recognized incomplete initialization can finish automatically if no
record has been admitted. A complete unpublished installation identity is reused.
An already published identity is never regenerated. Concurrent startup admits at
most one writer; another process must retry after that writer closes.

Unknown files, malformed final markers, conflicting staged identities, linked
metadata and records without their installation identity refuse startup. The
directory is preserved for inspection. Do not delete an installation marker or
replace it with a new identity to recover an existing ledger: this would discard
the boundary used to prevent duplicate protected execution. In particular, partial
final markers left by older versions still require operator inspection rather than
being treated as a new empty store.

The regression matrix terminates owned child processes without cleanup at directory,
lock, stage-open, partial-write, flush and atomic-publication boundaries for both
stores. It also checks simultaneous first startup, stable identities after reopening,
and byte-for-byte preservation of unrecognized directories on refusal. The same
matrix runs on Windows, Linux and macOS using the existing CI environment.
