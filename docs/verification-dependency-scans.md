# Compare the dependencies of a committed Unity asset

Use `verification-candidate dependency-scan` once per side of a change. It
analyzes retained outgoing `manage_reference_graph impact` pages for a single
entry asset, including dependencies between nodes that were already visited.
The baseline checkpoint retains the evidence needed to trace consumers after a
prefab or reference disappears from the candidate.

## Capture and resume

1. Open the baseline project and collect the entry asset's outgoing impact
   pages through the final null cursor. Use its `asset:<guid>` node, omit
   `max_depth`, and request commit evidence for the exact baseline commit.
   Save each successful response in its original order. A legacy capture can
   be reviewed with `graph_commit_binding` set to `unverified`.
2. Supply the captured pages and a Git checkout containing the commit.
   Start and resume using the same local checkpoint filename. Each completed
   page commits its graph edges, discovered asset and metadata hashes, and
   continuation position in one SQLite transaction.

```sh
unity-mcp --no-project-context verification-candidate dependency-scan \
  --project . --commit "$BASE_SHA" --root Assets/Room.unity \
  --checkpoint room-base.sqlite --graph-page base-1.json

unity-mcp --no-project-context verification-candidate dependency-scan \
  --project . --commit "$BASE_SHA" --root Assets/Room.unity \
  --checkpoint room-base.sqlite --graph-page base-2.json \
  --graph-page base-3.json --output room-base.jsonl
```

3. Repeat for the candidate commit using its corresponding asset path and a new
   checkpoint. The two paths may differ when the entry asset kept its GUID.
4. Compare the completed checkpoints.

```sh
unity-mcp --no-project-context verification-candidate dependency-diff \
  --baseline room-base.sqlite --candidate room-head.sqlite --output room-diff.jsonl
```

The scan prints the graph revision, query ID, last accepted page number, next
cursor, and completed asset count. Omit `--graph-page` to inspect progress
or export an already completed scan. After an interruption, supply the next
remaining page. Replaying an identical retained file is idempotent. A skipped
page requires the missing original page. A revision, capture guard, or query
change requires fresh pages from page 1 for that root in a new checkpoint.
The opposite side remains available. A graph whose native collection is
behind the Editor requires a refresh and fresh capture.

Each input page is limited to 4 MiB. Git reads use batches with a 4 MiB
per-file bound. SQLite stores the complete relation set, reachability queue,
and reverse index on disk. Exports stream in deterministic order. A complete
inventory begins with the capture identity, counts, and a closure source
revision computed from the sorted reachable asset and metadata hashes. The
following JSON Lines carry each asset, native dependency, and unresolved edge.
This inventory supports comparison independently of the smaller Player
registration and reviewed input proposal limits. See [reviewed dependency
closure](verification-dependency-closure.md) for registration inputs.

## Read the comparison

The first JSON Line contains the baseline and candidate commits, query
identities, graph revisions, binding status, and root GUID. Subsequent rows use
these fields:

| Kind | Interpretation |
| --- | --- |
| `relation` | `removed` and `added` compare an owner's observed outgoing edges where both scans reached that owner. `left_closure` and `entered_closure` mean only one side reached the owner. Its relationships elsewhere in the project remain unknown. |
| `retargeted` | Both committed Unity YAML files contain the same document class, local file ID, and serialized field path, and that slot points at a different GUID or target file ID. Each side's graph also contains the corresponding native asset dependency. The record gives both target paths, target file IDs and the exact field. |
| `consumer` | `owner` and `owner_path` identify the changed relation's source. `node` and `path` identify that owner or an upstream prefab or scene. `side` identifies the graph used to find it. |

An impact page records whether its revision's native asset catalog admitted
all catalogued asset GUIDs. When the opposite capture filtered assets, or an
older capture lacks this evidence, a target seen elsewhere in that capture is
still comparable. A missing endpoint whose visibility is unknown yields
`target_visibility_unknown` or `owner_visibility_unknown`. Those records alone
do not establish changed consumers.

Retarget attribution covers unambiguous Unity YAML object-reference slots in
text assets up to 512 KiB. Binary assets, missing field evidence, ambiguous
documents, and unsupported formats can still contribute added/removed native
relations. Review the binding status before attributing the retained graph to
a commit. Native graph collection has partial static coverage. Other providers'
edges and endpoints outside the supported committed asset types appear as
unresolved inventory records. The comparison refuses incomplete
page sequences and unresolved native input identities. Consumer results cover
the selected root's native closure. Compare other entry scenes separately when
their consumers are relevant.
