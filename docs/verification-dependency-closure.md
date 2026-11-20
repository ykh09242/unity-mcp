# Review project configuration and additional Player inputs

The Git candidate draft captures the exact bytes of
`ProjectSettings/GraphicsSettings.asset`, `ProjectSettings/QualitySettings.asset`
and `Packages/packages-lock.json`. Each changed file contributes to the source
revision. The draft reports graphics and quality changes as `render_settings_changed`.
A custom renderer asset under `Assets` can also have its own mapping. For example,
map `ProjectSettings/QualitySettings.asset` to the project's quality-renderer
test to select it when only the assigned render pipeline changes.

For the package lock, `configuration_changes` distinguishes a change in
dependency resolution from JSON whitespace or object-key ordering.
Arrays retain their order. The draft carries a `package_lock_evidence` receipt
on each affected side, including the exact-byte and canonical JSON hashes.
Preserve that field in the corresponding Player registration. With both receipts
present, a representation-only change remains visible in `changed_paths` and
keeps its distinct source revision while selecting zero mapped checks for
that path. A lock change without comparable receipts retains its mappings and
appears as `package_resolution_unverified`. Use project-owned test and scenario
names in `mappings` as described in [verification profiles](verification-profiles.md).

## Expand a committed draft from the reference graph

1. Generate a committed draft using the base and head of the PR.
2. In the intended Unity project, request `manage_reference_graph` action
   `impact` for the entry asset's `asset:<guid>` node with `direction="outgoing"`.
   Omit `max_depth` to request every reachable depth. Save each response in
   continuation order through the final null cursor. For dynamic references,
   also save `diagnostics` responses for the traversed nodes. Use the same
   graph scope and revision for all pages. To verify the Editor checkout,
   include `commit` with the full candidate Git object ID on every impact
   and diagnostics request, including continuations. For example:

   ```json
   {"action":"impact","options":{"node":"asset:11111111111111111111111111111111","direction":"outgoing","limit":12,"commit":"<full candidate commit>"}}
   ```
3. Pass those saved JSON documents or their successful response envelopes to
   the proposal command. The selected Git checkout must contain both commits.

```sh
unity-mcp --no-project-context verification-candidate dependency-closure \
  --project . --draft pr-draft.json --root Assets/Scenes/Room.unity \
  --graph-page impact-1.json --graph-page impact-2.json \
  --diagnostics asset:11111111111111111111111111111111 room-diagnostics.json \
  --output room-inputs.json
```

`proposed_inputs` includes each additional asset. `traversed_edges` records its
first confirmed route, including intermediate assets already in the PR. The
parent edge ID in `via` reconstructs routes such as scene, prefab, material,
then texture. `input_hashes` contains the SHA-256 of the additional asset
and metadata files. A proposed file must have the same Git blob identity on
both sides. Each accepted node's metadata GUID must match the graph and its
candidate bytes must match any existing draft entry.

Review `unresolved` for dynamic keys, unconfirmed edges, mismatched asset
identities, unavailable diagnostics and partial traversal. Provider issues
retain the asset path, reason and source line when available. Edges
with unsupported owners or owners outside the confirmed traversal remain
unresolved under their recorded edge IDs. Static graph
collection has partial coverage. The report records the original graph revision
and scope. Pages archived without `commit` retain `graph_commit_binding:
unverified`. For a capture with commit evidence, the command reports
`graph_commit_binding: verified` only after verifying every supplied page.

The Editor samples the selected inputs before and after collecting fresh
direct AssetDatabase dependencies among the page's endpoints. Each sample includes the actual
asset and metadata SHA-256, GUID, file stamps, imported dependency hash,
Editor journal position, active target, Unity version, Git HEAD, compilation
and import status, and the number of dirty loaded scenes (including untitled
scenes). Git must be on the Editor's PATH and the open project must be inside
the selected checkout. Verification compares each sample with the observation
that supplied the graph's dependencies and with the actual blobs in the named
commit. A dirty scene, differing checkout, changing observation, source or
metadata mismatch, or missing collection stamp prevents verification. If an
older collection lacks metadata stamps, recollect with the updated Editor
and server. Archive fresh pages after imports and compilation settle and
after saving any scene changes you want included.

The first pass with `commit` registers its endpoints for import observation.
Collect the paths in `commit_evidence.imports_required` across the traversal.
When authorized to reimport them, run the ordinary `manage_asset` action
`import` on each path. For those registered paths the Editor takes SHA-256
samples on both sides of a synchronous forced import, observes the actual
postprocess callback, and records the resulting Unity dependency fingerprint.
The graph collector retains that witness with its publication. An intervening
import, deletion, move, domain reload or eviction can invalidate the witness.
The Editor retains up to 2,048 registered paths in FIFO order. Reconcile with
`manage_reference_graph` action `sync`, wait for its native collection to
be current, and start again at page one. The final pages must come from one
revision. A file replacement that preserves GUID, length and timestamp still
fails when its exact bytes differ from the imported bytes.

This binding covers the observed asset endpoints and page contents. A
different working-tree file outside those endpoints, runtime-only references
and importer environments on other machines are outside its scope. Retained
JSON assumes a trusted capture source. Each page can sample up to 32 asset
nodes with up to 4 MiB per file and 16 MiB per sample. Reduce the page size
when a page exceeds a capture limit. For larger traversals, see
[resumable dependency scans and comparison](verification-dependency-scans.md).

After review, merge the selected additions into the draft input map.
`expanded_source_revision` applies when all reported additions are accepted.
Recompute the revision for any other selection. Build and register the Player
using the final inputs. The command handles up to 32 impact pages and 32
diagnostics pages, 1,024 edges, 128 graph assets, 4 MiB per input and 32 MiB
of committed source bytes. Retained graph pages are limited to 4 MiB each and
16 MiB collectively.
