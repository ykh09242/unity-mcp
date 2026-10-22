# Reference graph state contract

`services.reference_graph.store` owns durable SQLite evidence. It is the storage
component; transport authorization, Unity collection and source analysis are separate
layers. No new database package, driver or service is required.

## Isolation

Every store requires a `GraphScope(user_id, instance_id, project_id)`. The server must
resolve these from authenticated request/connection state and the selected Unity
instance. They must not be accepted as tool-supplied ownership overrides. Remote
requests with missing user identity must fail closed. Local deployments use the
server's OS-user state directory. Display names are not identity keys.

`GraphStore.open(server_state_root, scope)` hashes all three identity fields into a
filename. The database also stores that scope digest and refuses a mismatched
identity. Query cursors bind scope, node, direction and database revision. Users or
instances do not share generated evidence, work queues, external grants, rules or
manual declarations. Sessions for the same exact scope coordinate through SQLite.

The root must be server-owned local storage, outside imported Unity assets and
network/synchronized filesystems. Linked/reparse ancestors, hard-linked database
files and unrelated SQLite databases are rejected. New databases use owner-only
file mode where supported. These checks supplement OS directory permissions; they
are not a sandbox against a hostile process running as the same OS user.

## Publication and recovery

Observers record a fingerprint and monotonic source/provider generation before
analysis. A worker publishes its complete outgoing edge set only if that generation
is still current and its source remains enabled. Each replacement is transactional.
A late worker cannot restore references after a newer change or deletion. Duplicate
events do not rescan unchanged evidence. Moves update the path even if bytes match.

Batch observation/publication amortizes transactions for large import sets without
introducing a project-size ceiling. The caller supplies cooperative work batches;
there is no fixed total asset/edge limit. Query pages support up to 10,000 rows and
continuation without silently truncating fan-out. A graph revision change requires
restarting pagination, avoiding a mixture of observations from different revisions.

An aborted process rolls back unpublished edges; committed pending work survives
reopening. Analysis failure retains prior evidence with `stale=true`. Deleted assets
retain incoming references as dangling evidence instead of erasing the problem.
`queue_idle` describes pending/error work only: it does not certify a complete project
scan, successful compilation, complete static analysis or safe asset deletion.

Failed asset collection records a durable, owner-specific error and continues with
other assets. Retries back off from five seconds to five minutes; a new import
bypasses that delay. The delay limits repeated failures, not valid job duration or
project size. A failed script and its known transitive dependents keep their previous
code evidence stale until collection recovers. Reimporting identical bytes also
reconciles pending evidence. External decoding/parser failures remain visible per
file, preserve previous evidence and retry on the next scan without stopping the
watcher or discarding other files. Source and worker fences still apply to errors.
Workspace schema version 2 adds retry state transactionally, preserving version 1
evidence, progress, source grants and authored declarations.

## External grants and authored data

Changing an external source configuration invalidates its workers and removes its
previous cached edges immediately. Disabling it also excludes its work from reads
and pending reconciliation. Re-enabling cannot reveal data collected under a
previous, broader grant; new evidence must be published under the new version.
Observers must carry the source version read before enumerating external files and
the rule revision read before analyzing code/configuration. Admission checks those
versions in the write transaction, including for previously unseen owners. A stale
enumerator cannot adopt a newer grant or rule version implicitly. Workers also carry
the resulting observation generation through publication.
File admission and current OS permissions must still be checked by the reader.

Manual declarations are labeled `declared`, independently of caller-provided
confidence. Updates/deletions require an expected version. Deletion retains a
version tombstone so an old edit cannot overwrite a recreated declaration.
Rebuilding generated evidence preserves declarations, source policies and analysis
rules. Unknown schema versions or corrupt/unowned databases are errors, not reasons
to silently delete authored state.

## Verification

The focused test module exercises actual SQLite files, stale publications, batch
updates, a process exiting during a write, concurrent writers, reopen recovery,
revocation, declaration tombstones, large fan-out pagination and all three ownership
dimensions. The Python workflow runs it on Windows, Linux and macOS with Python
3.11 and 3.15. A passing workflow is evidence for the state layer on that runner;
licensed Unity execution remains a separate check.
