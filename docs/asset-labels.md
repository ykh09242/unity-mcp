# Asset labels

`manage_asset` supports `get_labels`, `set_labels`, `add_labels` and
`remove_labels`. Existing CRUD/search actions keep their defaults. The tool as a
whole retains its destructive annotation because some actions write assets.

Read one imported asset or folder:

```json
{"action":"get_labels","path":"Assets/Characters/Player.prefab"}
```

Read `data.results[0].revision` and submit it with a mutation:

```json
{"action":"add_labels","path":"Assets/Characters/Player.prefab","label_revision":"<observed revision>","labels":["playable"]}
```

The Python tool uses `label_revision` and `label_targets`; the native/CLI wire uses
`labelRevision` and `labelTargets`. A batch has 1-32 closed rows, for example:

```json
{"action":"remove_labels","label_targets":[{"path":"Assets/a.txt","revision":"<a revision>"},{"path":"Assets/b.txt","revision":"<b revision>"}],"labels":["obsolete"]}
```

Single `path`/`label_revision` and batch `label_targets` are mutually exclusive.
Get rows contain only `path`; mutations require a revision for every row. All rows
share one operation and requested label list. There is no recursion or glob
expansion. Folder labels affect only the folder itself. New label actions refuse
unrelated CRUD/search options and unknown native fields.

`set_labels` replaces the entire set; `[]` clears it. `add_labels` unions exact
strings and `remove_labels` subtracts exact strings. Empty add/remove lists and
already-satisfied sets perform no SetLabels call. Duplicates are removed, output
is sorted ordinally, and the tool never trims, changes case or normalizes Unicode.
Requested labels cannot be empty, whitespace-only, contain control characters or
have edge whitespace. Unrelated observed labels are preserved unchanged.

Unity's documentation does not specify global label spelling/case interning or
immediate persistence timing. The tool requires exact post-write readback. If
Unity returns different spelling or cannot confirm the requested set, the response
is `asset_label_outcome_unknown`; it does not report a normalized success. Native
controls record raw getter spelling and raw meta persistence separately. No
minimum-version native result is implied by this source change.

## Revisions and concurrent changes

Assets reads return `guid`, complete `labels`, `read_only:false` and an opaque
172-character revision:

`mcp-al-v1_<guid32>_<raw-meta-sha25664>_<ordinal-label-array-sha25664>`

The raw meta digest includes every byte, including line endings. The label digest
uses the complete deduplicated ordinally sorted JSON string array. Do not construct
revisions manually. Packages can be read, but return `read_only:true` and
`revision:null`. All package mutations are refused, including embedded packages
and apparent no-ops. Built-in resources, meta-file targets, missing/unimported
assets, duplicate canonical targets/GUIDs and the `Assets` root are refused.

A stale revision returns `asset_label_conflict`, with `data.reason` distinguishing:

- `asset_identity_changed`: the observed GUID differs.
- `labels_changed`: the exact label-set digest differs.
- `metadata_changed_without_label_set_change`: the raw meta differs while labels
  match. This includes serialization-only changes; it does not prove an importer
  field changed.

All batch targets are admitted and observed before any write. Each target is
observed again immediately before its write. Writes run sequentially in request
order and **are not atomic**. On a late conflict/error, earlier confirmed changes
remain; later rows are `not_attempted`. `data.partial:true` means a confirmed
changed row precedes an incomplete row. Success requires every row to be observed
changed/unchanged. A pre-write refusal does not mean earlier rows were rolled back.

An exception after entering SetLabels or an unconfirmed readback is
`asset_label_outcome_unknown`; its row is `outcome_unknown`. Row label/revision
fields describe the observation named by `observation` (`before_attempt`,
`pre_write_recheck`, `conflict_recheck`, `read` or `readback`), not an assertion that
an uncertain write left those values intact. Inspect current state before choosing
the next operation. There is no automatic retry of a label mutation after reload,
raw-meta rollback, automatic rollback of earlier rows or added Undo guarantee.

These revisions are observed preconditions, not locks or authorization. Unity has
no label compare-and-swap API: an external writer can race after the last
observation, including between label and meta reads. Matching revisions do not
provide filesystem transaction isolation.

## Bounds and side effects

The tool enforces 128 labels per asset, 256 UTF-16 units per label, 16 KiB of
requested UTF-8 label text and 32 KiB of aggregate target-path UTF-8 text. Cumulative
observed/planned label text is limited to 64 KiB, including pre-write rechecks and
readback. Meta observations read at most 1 MiB per file and 4 MiB cumulatively per
request. A changed observation may exhaust the remaining budget: refusal before a
write is bounded; inability to confirm a write is reported as unknown. Results are
never truncated to admit a mutation. Bounded row/string counts and text budgets
keep serialized native responses below 1 MiB, including JSON escaping/row fields.
Intrinsic malformed/budget requests fail before context resolution or I/O.

Assets mutation queries meta editability and refuses read-only/inaccessible
metadata. It does not check out assets, make files editable or change attributes.
Existing script consent also applies conservatively to compilation-sensitive
targets. Consent is not requested for get; package-write refusal precedes consent.

Get uses `LoadMainAssetAtPath` and `GetLabels(Object)` and may load objects or invoke
Unity callbacks; it is not metadata-only search and makes no process-heap or
zero-callback promise. Native API-internal allocations/time are outside managed
row/text/file-byte limits. Label operations do not explicitly call Refresh,
ImportAsset, SaveAssets, SaveAndReimport or StartAssetEditing/StopAssetEditing.
Preflight waits for compilation without refreshing dirty assets. The search
snapshot cache is invalidated immediately before each actual SetLabels attempt,
including attempts whose outcome becomes unknown. Known no-ops/pre-write refusals
do not explicitly invalidate it.

## CLI

```text
unity-mcp asset labels get Assets/a.txt
unity-mcp asset labels add Assets/a.txt --revision <revision> --labels-json '["keep"]'
unity-mcp asset labels set --targets-json '[{"path":"Assets/a.txt","revision":"<revision>"}]' --labels-json '[]' --force
```

Options accept at most 128 KiB of UTF-8 inline JSON per option, without implicit
`@file`/stdin expansion; decoded target/label limits still apply.
Mutations ask for one existing destructive confirmation; `--force` skips that
prompt, never revision validation. Intrinsic validation occurs before config,
confirmation or network command. One CLI invocation sends one command and keeps
structured conflict/unknown responses intact.

## API references and compatibility

The implementation uses longstanding Object-based
[GetLabels](https://docs.unity3d.com/ja/2022.3/ScriptReference/AssetDatabase.GetLabels.html)
and [SetLabels](https://docs.unity3d.com/cn/2021.2/ScriptReference/AssetDatabase.SetLabels.html),
without assuming the newer GUID overload is present on the minimum version.
[Current SetLabels documentation](https://docs.unity3d.com/6000.0/Documentation/ScriptReference/AssetDatabase.SetLabels.html)
warns against label changes inside asset-editing batches.
[IsOpenForEdit](https://docs.unity3d.com/6000.0/Documentation/ScriptReference/AssetDatabase.IsOpenForEdit.html)
is queried rather than initiating checkout. Official Unity 2021.3 English pages
were unavailable in the design lookup; minimum-2021.3 compilation/runtime,
provider-specific editability and actual native spelling/persistence remain
verification limits until separately observed.
