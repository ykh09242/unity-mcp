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
The preview does not simulate earlier commands. A rejected or incomplete batch
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
