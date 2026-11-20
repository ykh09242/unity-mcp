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
baseline/proof.json
baseline/input-chunks/<sha256>.json      (for chunked proofs)
baseline/closure.json
baseline/build/scenario-bundle.json
baseline/build/<complete native payload>
candidate/proof.json
candidate/input-chunks/<sha256>.json    (for chunked proofs)
candidate/closure.json
candidate/build/scenario-bundle.json
candidate/build/<complete native payload>
```

## Prepare the review record

1. Capture the Git source commit of each build, the exact MCP package commit included by the builder, and the native platform. Keep any resolved local package inputs reproducible in the selected checkout. For each side, prepare a committed source draft, then capture the project's outgoing dependency closure. Review unresolved items, include the proposed source and metadata bytes and build the scenario with the resulting `expanded_source_revision`. Retain the closure as `closure.json`. The verifier checks its commit, its original and expanded revisions, every added dependency hash and all selected committed bytes. [Git input drafts](verification-candidate-draft.md)
2. Export each build's portable candidate proof to its side of the transfer, along with its validated input chunks. The operator reviews both complete bundle payloads before offering them for execution. [Portable Player proofs](verification-candidate-portability.md)
3. Create a UTF-8 JSON object with `schema_version: 1`, `kind: "verification_player_pair"`, the common `scenario` name and its exact `definition_hash`. Include a `baseline` and `candidate` object using the fields below. Pin the SHA-256 of the exact resulting JSON in an independent review record. The command uses those bytes directly.

| Field on each side | Source |
| --- | --- |
| `project_commit`, `package_commit` | Full Git commit IDs recorded by the trusted builder for the Unity project and the MCP package. |
| `proof_sha256`, `closure_sha256` | SHA-256 of the exact portable proof and reviewed dependency closure files. |
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

The **Prebuilt Player pair** workflow runs on the project's `beta` branch. A repository administrator can pin an approved, completed same-repository `beta` producer run and its Actions artifact named `prebuilt-player-pair`. Place the transfer contents above at the artifact root. The Windows runner checks out the receiving SHA with history and uses the exact command above with `TestProjects/UnityMCPTests`. Supply immutable pins through repository variables.

| Variable | Value |
| --- | --- |
| `VERIFICATION_PLAYER_ARTIFACT_ID`, `VERIFICATION_PLAYER_RUN_ID` | Numeric GitHub Actions artifact ID and producing run ID. |
| `VERIFICATION_PLAYER_PRODUCER_SHA` | Producer run's complete Git commit. |
| `VERIFICATION_PLAYER_ARCHIVE_SHA256` | SHA-256 of the uploaded Actions artifact ZIP as recorded by GitHub. |
| `VERIFICATION_PLAYER_PAIR_SHA256` | Independently reviewed SHA-256 of the exact `pair.json`. |

Admission requires a successful trusted push or manual producer run. GitHub checks the downloaded archive digest and the CLI checks the independently pinned pair, its inputs and both complete native payloads. The workflow retains admission metadata and fresh native reports separately. With all pins unset, a push records `unavailable` and skips the native job. An explicit required dispatch fails when a pin is absent. An expired artifact or inconsistent pin fails admission. [GitHub artifact API](https://docs.github.com/en/rest/actions/artifacts)
