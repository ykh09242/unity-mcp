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
   graph scope and revision for all pages.
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
retain the asset path, reason and source line when available. Static graph
collection has partial coverage. The report records the original graph revision
and scope, and identifies the unverified binding between that Editor capture and
the Git commit. A graph capture can represent another Editor state with the
same asset GUIDs.

After review, merge the selected additions into the draft input map.
`expanded_source_revision` applies when all reported additions are accepted.
Recompute the revision for any other selection. Build and register the Player
using the final inputs. The command handles up to 32 impact pages and 32
diagnostics pages, 1,024 edges, 128 graph assets, 4 MiB per input and 32 MiB
of committed source bytes. Retained graph pages are limited to 4 MiB each and
16 MiB collectively.
