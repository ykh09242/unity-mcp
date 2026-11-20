# Reproduce a reviewed standalone Player pair

From a clean verifier source checkout and a clean checkout of the Unity project, run the following command on the operating system targeted by the two Players. The project Git repository must contain both reviewed commits and the verifier repository must contain the recorded MCP package commits. The command uses the server's locked environment. The Editor, a connected MCP server and a Unity license are unnecessary.

On Windows, both the approved build producer and the team member reproducing the result should create their verifier and project checkouts with automatic line-ending conversion disabled. This clone command applies the setting to the initial checkout and saves it in that clone. Run it for each repository, substituting its URL and a new directory.

```sh
git -c core.autocrlf=false clone --config core.autocrlf=false <repository-url> <new-directory>
```

The verifier hashes the exact committed and working-tree bytes, including Unity `.meta` files. A checkout filter or explicit `.gitattributes` rule that changes those bytes can leave Git status clean while the proof fails. The hosted Windows jobs configure their disposable runner before checkout.

An unavailable reproduction can include `checkout_diagnostics` before either Player starts. Each row gives the project-relative path, the reviewed SHA-256, the committed blob's SHA-256 and the checkout SHA-256 when available. The verifier reports these differences without rewriting inputs or downloading objects.

| Diagnostic | What to re-materialize or review |
| --- | --- |
| `line_endings` | The files agree after LF/CRLF normalization. Recreate the checkout with byte-preserving Git settings and matching `.gitattributes`. |
| `unhydrated_lfs_pointer` | The checkout contains the committed Git LFS pointer for the reviewed object's SHA-256. Provision that object through the trusted LFS workflow and verify the proof and commit are compatible with exact-byte admission. |
| `content_mismatch` | The checked-out bytes differ beyond LF/CRLF. Compare the reported digests and materialize the reviewed source or restore the build producer's checkout rules. |
| `missing`, `committed_mismatch` | Restore the missing input or review the source proof against the recorded commit's Git blob. |

```sh
uv run --project Server --locked unity-mcp --no-project-context verification-candidate reproduce --project /checkouts/game --manifest /trusted/transfer/pair.json --sha256 <independently-reviewed-pair-sha256> --output /new/evidence
```

The verifier consumes local, operator-provisioned builds. Each side has a fixed location below the transfer directory. Use `--artifacts <directory>` when the builds, proofs and closure files are installed separately from the pair manifest.

```text
pair.json
project.bundle                          (optional reviewed standalone project history)
baseline/capture.json                    (builder-produced pairs)
baseline/proof.json
baseline/input-chunks/<sha256>.json      (for chunked proofs)
baseline/closure.json
baseline/build/scenario-bundle.json
baseline/build/<complete native payload>
candidate/proof.json
candidate/capture.json                   (builder-produced pairs)
candidate/input-chunks/<sha256>.json    (for chunked proofs)
candidate/closure.json
candidate/build/scenario-bundle.json
candidate/build/<complete native payload>
```

## Prepare the review record

For builder-produced pairs, point the project at the tracked `MCPForUnity` folder of its clean native package checkout. Pass `--mcp-pin-build-source` to the Unity batch command that invokes the scenario build. The Editor resolves the installed package from its build assembly and samples both Git checkouts before and after the build. For each tracked package file, including Unity metadata, it checks the working file's Git blob ID with the checkout's attributes and also hashes its exact bytes. The raw checkout SHA-256, project and package commits, package tree, build ID and source revision travel in the inventoried native payload. Capture rechecks the raw fingerprint on the builder. A hidden edit, an extra ignored package file or a build lacking `build-source.json` fails capture. Package verification supports 8,192 regular files, 64 MiB per file and 512 MiB in total. [Git file hashing and checkout filters](https://git-scm.com/docs/git-hash-object)

On the approved builder, use the clean project and native package checkouts that supply each build. Prepare a committed draft, review the outgoing dependency closure and build the scenario with its `expanded_source_revision`. Retain the actual closure from that build. The verifier checks the named commit, both source revisions, each added dependency hash and the selected committed bytes. [Git input drafts](verification-candidate-draft.md) · [Dependency closure](verification-dependency-closure.md)

Capture each side immediately after the approved build has produced its local registration under `UNITY_MCP_VERIFICATION_ROOT`. Pass the actual package source checkout used in resolving the project. Keep the builder's project and package checkouts fixed throughout the build and capture. Each capture creates a fresh directory containing the portable proof, chunks when present, complete native payload, closure and a final `capture.json` binding their identities to both Git commits.

```sh
unity-mcp --no-project-context verification-candidate capture-pair-side --project /build/broken/game --package-checkout /build/broken/package --candidate-id moved --closure /build/broken/closure.json --output /review/transfer/baseline
unity-mcp --no-project-context verification-candidate capture-pair-side --project /build/fixed/game --package-checkout /build/fixed/package --candidate-id repaired --closure /build/fixed/closure.json --output /review/transfer/candidate
unity-mcp --no-project-context verification-candidate pack-pair --project /build/fixed/game --artifacts /review/transfer --expectations /review/expectations.json
```

The assembly command checks both captures, rehashes the staged payloads and verifies the committed source bytes against the supplied Git history. On success it publishes `pair.json` last and prints its SHA-256. A failed capture or assembly can be retried in a fresh directory. The operator independently reviews the selected builds, outcomes and exact manifest bytes before publishing the accepted digest. The producer's printed digest identifies the review subject.

The expectations file contains one `baseline` and one `candidate` object, each with `status`, `checkpoint`, `action` and `state`. Optional `serialized_references` entries select a committed Unity text asset, a unique top-level field and its target asset. Both captures must include the source, target and target metadata. Each replay verifies the actual serialized GUID and nonzero fileID and requires both identities to agree across the pair. Use `preserved_guids` to require additional GUIDs in both committed inventories. [Maintained prefab expectations](../TestProjects/UnityMCPTests/Verification/prefab-pair-expectations.json)

| Field on each side | Source |
| --- | --- |
| `project_commit`, `package_commit` | Full Git commit IDs recorded by the trusted builder for the Unity project and the MCP package. |
| `proof_sha256`, `closure_sha256` | SHA-256 of the exact portable proof and reviewed dependency closure files. |
| `capture_sha256` | SHA-256 of the builder's capture record. Each replay checks the record against every pinned build identity. |
| `build_id`, `source_revision`, `payload_hash`, `bundle_manifest_sha256` | Registration build ID, expanded source token, v2 payload fingerprint and hash of the exact `scenario-bundle.json`. |
| `status` | Expected native outcome, one of `succeeded`, `failed` or `timed_out`. |
| `checkpoint`, `action` | Unique main-step name and action whose success or failure explains this build's result. |
| `state` | Project-owned observation, e.g. `{"log_marker":"WIND ","expected":{"speed":2}}`. Expected JSON scalar values are compared with the independently emitted Player log marker. |

The maintained [prefab move example](../TestProjects/UnityMCPTests/Verification/README.md) uses the common scenario `verified-shop`, checkpoint `show shop banner` and action `wait_state`. Its moved baseline times out and emits zero visible banners. Its repaired candidate succeeds and emits one visible banner. For a pair built from this example, pin the generated project inputs in the source history used by the build and capture their dependency closures. Other projects supply their own scenario names, actions, expected outcomes and state fields.

## Read the result

The command verifies both proofs, the committed input hashes, the complete local payload inventories and the candidate's live input bytes before running either Player. The candidate's committed `Assets`, `Packages` and `ProjectSettings` trees must match the selected checkout. The native MCP package tree for the candidate must match the verifier checkout. The baseline uses its recorded historical source and package commits. A change to a checked native tree requests a new build. The procedure covers the declared Git sources and observed dependency closure. An external source or environment dependency requires its own provenance at build time.

Each invocation creates a fresh output directory. `reproduction.json` records the verifier and project checkout commits, required build identities on both sides, each new job ID, the native outcome, the independently observed state and SHA-256 hashes of the retained evidence files. Its admission rows retain the verified input counts, dependency closure digest, graph revision and the graph's own commit-binding status. Hash the files relative to the reported artifact directory to verify a transferred result.

| Exit | Meaning |
| --- | --- |
| 0 | Both native process outcomes, checkpoints and independent observations matched. An expected broken baseline is a successful reproduction. |
| 1 | A completed pair disagreed with its reviewed expectations, or the CLI rejected an invalid invocation. |
| 2 | A trusted build, input or complete native result was unavailable. Inspect `reason`, `missing` and `required_builds` for the exact files, commits and digests. |

## Run on GitHub without a license

Dispatch **Windows Player Assurance** on `beta` with `produce_prefab_pair` enabled to produce the maintained control using the existing licensed Windows builder. The fixture driver creates an isolated Unity project, preserves the original prefab GUID through the move, commits the moved and repaired states in that fixture repository, and builds each Player from its own clean commit. It captures Unity's recursive scene dependencies, the exact project inputs, resolved package identity and the two complete payloads. Its transfer includes the fixture's Git source bundle. The trusted build publishes `prebuilt-player-pair` for independent review. [Fixture build driver](../TestProjects/UnityMCPTests/Verification/build_reviewed_prefab_pair.py)

An approved same-repository `beta` build can publish `prebuilt-player-pair` directly from the assembled transfer. To assemble retained captures in a separate clean job, upload `player-pair-captures` with `transfer/baseline/`, `transfer/candidate/` and `expectations.json` at the artifact root. Dispatch **Package Player pair for review** with that artifact's ID and archive SHA-256. It checks the completed producing run and archive, invokes `pack-pair` and uploads the verified transfer. The job summary records the output artifact and manifest identities for the reviewer.

The **Prebuilt Player pair** consumer runs on the project's `beta` branch. An administrator pins the reviewed, completed producer run and its `prebuilt-player-pair` artifact. On two separate clean Windows runners the workflow checks out the verifier at the receiving SHA. If the manifest pins `project_bundle_sha256`, each runner checks the Git bundle digest and refs and materializes the candidate in its own fresh directory. The self-contained bundle must have only `refs/heads/baseline` and `refs/heads/candidate` and contain the Unity project at its root. It can include all historical source bytes reachable from those refs. Use a dedicated, shareable project history for this option. The hosted fallback selects `TestProjects/UnityMCPTests` in the verifier checkout. A team with a separately provisioned project can pass its own clean root to the ordinary CLI, including subdirectories of a larger repository. Supply immutable pins through repository variables.

| Variable | Value |
| --- | --- |
| `VERIFICATION_PLAYER_ARTIFACT_ID`, `VERIFICATION_PLAYER_RUN_ID` | Numeric GitHub Actions artifact ID and producing run ID. |
| `VERIFICATION_PLAYER_PRODUCER_SHA` | Producer run's complete Git commit. |
| `VERIFICATION_PLAYER_ARCHIVE_SHA256` | SHA-256 of the uploaded Actions artifact ZIP as recorded by GitHub. |
| `VERIFICATION_PLAYER_PAIR_SHA256` | Independently reviewed SHA-256 of the exact `pair.json`. |

Admission requires a successful trusted push or manual producer run. GitHub checks the downloaded archive digest and the CLI checks the independently pinned pair, its inputs and both complete native payloads. The workflow retains admission metadata and fresh native reports separately. With all pins unset, a push records `unavailable` and skips the native job. An explicit required dispatch fails when a pin is absent. An expired artifact or inconsistent pin fails admission. [GitHub artifact API](https://docs.github.com/en/rest/actions/artifacts)

The comparison job verifies hashes of the four retained files from every execution, requires four distinct native job IDs and compares the source and package identities, serialized references, ordered step outcomes and project-owned observations. `consistency.json` reports `consistent`, `disagreement` or `unavailable`. The prefab fixture additionally checks the serialized banner reference, old and repaired Resources keys, loaded object and visible UI count before publishing its case receipt. Elapsed times, host process IDs and raw log bytes can differ between valid runs.
