# Remote operation permissions

Remote-hosted HTTP and plugin WebSocket authentication can carry a server-owned
`metadata.unity_mcp_permissions` claim in the configured validation response:

```json
{
  "valid": true,
  "user_id": "example-user",
  "metadata": {"unity_mcp_permissions": ["read", "test"]}
}
```

This is the Unity MCP server's validation protocol. It does not advertise a
particular external identity provider, OAuth scope or provider-side revocation API.
Only trusted validator output supplies authority. Tool annotations, plugin
descriptors, catalog membership and request arguments cannot grant permissions.

## Migration and validation

The four exact names are `read`, `test`, `edit` and `run`. They are independent:
`run` does not imply `read`, and `edit` does not imply `test`. The claim must be a
JSON array containing at most four unique known strings. Unknown names, duplicate
names, non-array values and oversized arrays invalidate authentication. An
explicit empty array grants nothing in either migration mode.

By default, an absent claim preserves existing full-access behavior, including
legacy custom batches. This compatibility provenance is retained separately from
an explicit array containing all four names. Set the server environment variable
`UNITY_MCP_API_KEY_PERMISSIONS_STRICT=true` (or `1`) to grant nothing when the claim
is absent. `false` and `0` retain compatibility mode; other values fail server
configuration. Embedded applications can set `config.api_key_permissions_strict`
before server creation. A missing metadata object is an absent claim; a supplied
metadata value with an invalid shape fails closed.

## Operation boundary

The server uses a closed, explicit tool/action and native-command policy in
`Server/src/services/operation_permissions.py`. Unknown tools, actions and resource
routes are denied for explicitly scoped credentials, even with all four grants.
Adding a built-in operation requires reviewing its policy entry. This policy is
operation admission, not a sandbox for arbitrary user scripts, Unity callbacks,
Editor extensions or already-running native jobs.

| Grant | Examples |
|---|---|
| `read` | Asset information and labels, scene hierarchy, console reads, job observations, known resources and catalog subscriptions |
| `test` | Start/cancel Unity test jobs, including clearing an orphaned test job |
| `edit` | Asset/scene changes, component writes, label changes, console clearing, tool-group changes |
| `run` | Play Mode/input control, code/menu execution, build execution, profiler/recording control |

Some operations require a combination. Custom tools always require `edit` and
`run`, regardless of claimed read-only annotations. Refresh/import-and-compile
requires both. Generation, UI rendering and camera/Blender capture/import operations
also require both because they execute work and can write project assets. Script convenience
wrappers (`create_script`, `delete_script`, `apply_text_edits`,
`script_apply_edits`) require `read`, `edit` and `run` for mutations because they
inspect/verify/await readiness and explicitly compile. Confirmed preview routes
through the edit wrappers retain their existing `read` and `edit` requirement.
Public `manage_script` create/delete also require all three before their
unconditional ready wait. Native script create/delete/edit/text/prepared-apply
actions require `edit` and `run`; dedicated native reads/previews still require
only `read`. Native menu execution always requires `run`, even if its
arguments claim an inspection action; public menu inspection routes to the
separate read-only native inspector. Live model catalog refresh/listing and
Blender update checking require `run` because their native handlers start
background refresh or subprocess work. Asset-generation status remains a read
of the existing job record.

All `manage_packages` mutations (add, remove, embed, resolve and registry changes)
require `edit` and `run`: Unity may import, re-resolve or compile package code.
Queries/status remain reads. Explicit Editor consent remains necessary separately.
`manage_editor` deployment/restoration also requires both grants because it replaces
the package's executable code. Generic `manage_asset` content/tree mutations
(`import`, `create`, `modify`, `delete`, `duplicate`, `move`, `rename`), including
the `asset_edit` alias and batch members, require both grants. The server cannot
prove from a caller's path, extension or asset type that the resolved object,
folder subtree, importer or dependent generated source is inert. This conservative
floor also applies to ordinary texture/content changes through that generic API;
label changes and empty-folder creation retain `edit` alone.
Package inputs reject HTTP(S) userinfo, SSH passwords and arbitrary URL queries;
credential-free SSH/scp Git references and Git `?path=` selectors remain supported.
Use Git/UPM's external authentication. Responses redact all URL userinfo, queries
and fragments, including legacy jobs, registry records and native errors.

`animator_set_parameter` retains automatic Play/Edit behavior and requires `edit`
and `run`, because Edit mode persists controller defaults. Set the top-level
`runtime_only` Boolean to `true` for a `run`-only operation: the native handler
checks actual Play mode and refuses Edit mode before changes. Nested properties
and caller mode claims cannot lower this policy. Defaults can only be written to
writable, physically contained AnimatorController assets under `Assets/`;
package controllers and transient controllers are rejected.
The CLI exposes this as `animation animator set-parameter ... --runtime-only`.

Public refresh with `wait_for_ready` enabled (the default) additionally requires
`read` for its promised ready observation. Dispatch-only refresh can use
`edit` and `run` without `read`. Scene validation with a repair flag additionally
requires `edit`; ordinary validation remains a read. Native repair flag aliases
and coercible values cannot turn a read-only validation into a write.

`manage_ugui` text diagnostics (`diagnose_text`) and default raycast diagnostics
(`diagnose_raycast` with `allow_custom_filters` omitted or exactly `false`)
require `read`. Opting into custom raycast filters with exactly `true` requires
all of `read`, `edit` and `run` on both authorities: arbitrary filter callbacks
can change state or dispatch events as well as execute code. This requirement
applies even when no custom filter is currently present. For scoped credentials,
supplied non-Boolean flags, including null, strings and numbers, are denied
before probes or dispatch. These grants do not sandbox callbacks or promise
rollback, interruptibility, an atomic snapshot or a hard callback deadline.
The shared rule applies to public calls, native dispatch and every batch member.

Every normalized batch member is checked before settings, preflight or native
dispatch. Scoped batches accept only classified canonical built-ins and retain
the existing prohibition on nested batches and per-member instance routing.
Custom names and `execute_custom_tool` members are refused in scoped batches;
registered custom tools remain callable directly with `edit` and `run`. Legacy
absent-claim credentials keep their existing custom-batch behavior. If either the
requester or selected authenticated plugin is explicitly scoped, its restriction
still applies.

At final native dispatch, the requester and authenticated same-user plugin must
each authorize the canonical serialized command. The effective authority is
their intersection. A permitted requester cannot bypass a restricted plugin,
and a permissive plugin cannot elevate the requester. The check occurs again
after yielding authorization checks, immediately before socket I/O. Permission
denial before dispatch sends no native command. Authorization ending after a
dispatch retains the existing unknown-outcome behavior; it does not claim the
native operation was rolled back.

The requester is checked before selection. Once selection/catalog admission has
resolved the authenticated plugin, both authorities must also admit the complete
normalized public operation before its handler starts. This preserves public
wrapper requirements, such as a promised ready read, that are absent from the
reduced native payload.

Optional hidden work does not upgrade a grant. A forbidden editor-state settings
read uses the existing batch-size default. A forbidden preflight observation or
refresh is skipped using the existing proceed fallback, and a forbidden optional
readiness ping is skipped without reporting that readiness was observed. An
operation with indispensable work requires its combined permissions before it
starts. Resources and subscriptions require `read`; scoped subscriptions support
the existing editor-state URI and reject unknown resource filters.

## Connected lifetime

Validated grants are immutable members of the private CORE07 credential lease.
Renewal compares normalized grants and compatibility provenance as well as the
principal. A change in either direction retires the old generation, cancels its
owned work and fences old cache/detached/subscription activity. The changing
renewal is rejected; a fresh authentication obtains the new generation. A
different credential for the same user remains independent. Reordering the same
explicit grant set does not change authority.

External changes are observed only at the existing finite revalidation boundary
or explicit invalidation. This is not instantaneous external revocation or an
external compare-and-swap guarantee. Existing bounded renewal, cancellation,
capacity accounting and truthful post-dispatch uncertainty still apply.

Local HTTP token authentication and stdio behavior are unchanged. Remote-hosted
REST execution and legacy register-tools routes remain unavailable; permissions
do not enable either route.
