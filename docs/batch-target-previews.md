# Supported batch target previews

`batch_execute(validate_only=true)` preserves the existing action-name check.
Add `preview_scope="targets"` to inspect supported concrete targets without
executing child commands, saving assets, creating folders or refreshing Unity.
All members still require their ordinary permissions and Editor consent.

Supported initial operations:

- `asset_edit` / `manage_asset`: move or rename an imported file into an existing
  asset folder. Check contained paths, source GUID and streamed file/meta hashes,
  destination collision, parent identity and script mutation consent.
- `gameobject_edit` / `manage_gameobject`: modify a loaded scene instance ID's
  name, active state or local position/rotation/scale in the active scene. Inactive
  hierarchy targets require explicit activation, matching the execution handler.
  Check parameters, scene,
  object/component identity, dirty revisions, ancestor activation and transforms.
- `prefab_contents` / `manage_prefabs`: apply an already prepared
  `preview_modify_contents` plan. Observe its source/dependency fingerprint and
  planned fields without loading prefab contents or consuming the plan. The
  existing prefab owner rechecks and consumes it during application.

Unsupported fields (including explicit nulls)/actions, directory moves, missing destination parents,
name-based/future object references and repeated targets remain **incomplete**.
Known ancestor transform/activation writes also make a batch incomplete in
either command order. Independent siblings may share read-only ancestors, and
an ancestor name-only edit does not create a transform dependency. Shared asset
source/destination paths remain conflicting targets. The preview does not
simulate earlier commands. A rejected or incomplete batch
returns no applicable guard; split independent supported edits or use a domain
planner that models the dependency. Ordinary unguarded batches remain available
with their existing limits and behavior.

A successful result includes `data.preview`. Submit the same `commands` plus the
unmodified `preview` object to `batch_execute`. The server routes this to the
reserved `batch_apply_preview` command. Older peers cannot silently ignore the
guard and fall back to ordinary execution. Guards are signed by the current
Editor domain and bind the exact command content and registered handlers. They
retain no Unity objects, commands or per-plan server memory. Domain reload makes
them unusable. They grant no permissions or consent.

Application rechecks all targets before the first command, and each target again
immediately before its mutation. Permission/consent checks remain in the existing
owners. If an earlier command or callback changes a later target, remaining
commands stop with indexed `not_run` / `stale_preview` outcomes. Already completed
changes remain visible; `rollbackPerformed` is false. No cross-command transaction,
rollback, automatic replay or callback prediction is promised. A lost reply retains
the existing unknown-outcome contract. Compare guards are not one-shot operation
receipts; the underlying prefab plan's own consumption rules still apply.

Asset hashing streams input; it adds no new asset-size, batch-size or operation-time
limit. Strong previews are opt-in and never run on the ordinary batch path.

On Windows, target preview file hashing and guarded application hash checks run
on a worker, with cancellation between 128 KiB reads. Checked paths, scalar hashes
and explicitly owned read handles cross that boundary; no Unity objects do.
Unity object, handler, permission and destination checks stay on the Editor
thread and are repeated after file work; earlier scalar observations are
rechecked before the whole preview is accepted. Full file and meta hashes still
run at preparation, initial application verification and each child's
compare-before-dispatch check. No metadata-only hash cache is used. Cancellation
settles the file reader before returning and never starts a mutation. The
synchronous `ValidateCommand` C# compatibility entry remains available; the MCP
registry uses the cooperative asynchronous entry.

Read handles remain owned by the active observation until its Editor checks
finish, preventing cooperating writers from changing already-hashed bytes during
later file work. The applying caller retains the whole-batch handles through the
first child rehash, rechecks state after each await, and releases handles
immediately before dispatch without yielding. All handles are released on
success, rejection and cancellation. Windows read-sharing behavior is covered by
native tests. Other platforms keep synchronous streamed hashing, avoiding a new
Editor yield while their file-sharing guarantees remain unverified; their
fallback is covered by compilation. This is transient read sharing, not a
filesystem transaction. External processes that bypass the platform's sharing
rules remain outside the compare-before-dispatch guarantee.
