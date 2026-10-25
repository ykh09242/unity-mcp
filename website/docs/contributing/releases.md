# Releasing (Maintainers)

This fork distributes the Unity package from Git and the Python server from a commit-addressed GitHub archive. Publish fork releases through [GitHub Releases](https://github.com/ykh09242/unity-mcp/releases). The `beta` branch is the moving source branch; the version suffix and GitHub prerelease flag determine a release's channel independently of the branch name. A GitHub release is separate from documentation deployment and does not imply PyPI, Asset Store, OpenUPM, pre-built Docker image, or MCPB publication.

## Fork versioning

The fork owns its SemVer series independently of the original project's `10.3.x` versions. Unity-package and Python-server versions describe their respective fork components and can evolve independently; the Unity package's immutable `mcpServerSource` selects the matching server commit.

The first stable fork release is tagged `ykh09242-v1.0.0`, with both Unity and Python component versions initially `1.0.0`. Fork tags use the `ykh09242-v` prefix to avoid collisions with inherited upstream tags such as `v1.0.0`; never replace those historical tags. Publish the fork tag as a stable GitHub release rather than a prerelease after the release checks pass. Later component versions can be maintained independently.

Record the original project's version and commit as the upstream baseline, not as the fork's release version. A `-beta.N` suffix identifies a preview version, which should also be marked as a prerelease on GitHub. Do not imply a stable release until the maintainer selects and publishes one.

## Update component versions

Run the version helper from the repository root, replacing the version placeholders with the desired versions independently:

```bash
python tools/update_versions.py --component unity --version X.Y.Z
python tools/update_versions.py --component server --version A.B.C
```

`unity` is the default component and updates `MCPForUnity/package.json` and the root `manifest.json`. `server` requires an explicit `--version` and updates only `Server/pyproject.toml` and its own distribution entry in `Server/uv.lock`. Neither mode changes the immutable server or skill pin or couples the component versions.

After a server version change, test and commit the server files, then manually repin the Unity package and root manifest to that verified server commit using the procedure below. `--component all` is the legacy synchronized upstream mode, explicitly used by the upstream release workflow; use the independent modes for this fork.

## Prepare a pinned source version

1. Verify the server changes and commit them in the fork.
2. Record the full immutable server commit in `MCPForUnity/package.json` as `mcpServerSource`, using `https://github.com/ykh09242/unity-mcp/archive/<40-hex-commit>.zip#subdirectory=Server`. Keep the root `manifest.json` server invocation's `--from` argument identical; the metadata contract test enforces this alignment. The archive bypasses Git for Windows checkout limits without global Git or OS configuration changes.
3. Verify the Unity package uses that source by default and that `Server/pyproject.toml` names the distribution `ykh09242-unity-mcp-server`.
4. Run the relevant Python, Unity compile, metadata, and documentation checks.
5. Commit the Unity package and documentation changes. Users can replace the preview URL's `#beta` with that Unity-package commit SHA for reproducibility.

A server commit and a Unity-package commit need not be the same: the latter records the former's immutable source.

## Prepare the independent skill pin

Verify both skill copies against the intended Unity contracts, then commit that content as C. In a later package commit P, declare the full C as `mcpSkillRevision` in `MCPForUnity/package.json`. Keep `mcpServerSource` independent: a verified server commit does not establish a verified skill revision. Do not try to make a commit contain its own SHA.

Run `python tools/check_skill_pin.py --repo . --verify-git` after preparing the package. It checks locally available committed content and both current mirrors without fetching or changing the pin. Missing history is an explicit failure. The package commit P, selected server commit S and selected skill commit C can all differ.

## Connect capabilities to actual evidence

Keep the normal release summary and PR links. Add the two compact tables below to the manually prepared **fork GitHub release body**. Generated [Upstream Release History](/releases), `website/release-metadata.json` and the inherited release synchronizer remain upstream history.

This is a template, not a release or verification result. Replace every P/S/C/version/URL placeholder with actual committed inputs and retained links. The checker rejects unresolved placeholders. The example native row is valid only when that scenario was genuinely not run; an executed scenario must not be relabelled to fit the template.

```markdown
<!-- release-evidence:start -->
| Component | Version | Immutable input |
| --- | --- | --- |
| Unity package | X.Y.Z | [P](https://github.com/ykh09242/unity-mcp/commit/P) |
| Python server | A.B.C | [S](https://github.com/ykh09242/unity-mcp/archive/S.zip#subdirectory=Server) |
| Client skill | not separately versioned | [C](https://github.com/ykh09242/unity-mcp/commit/C) |

| Capability / scope | Component | Verification kind | Actual result | Tested input | Evidence |
| --- | --- | --- | --- | --- | --- |
| Native scenario not executed | unity | native-editmode | not-run | none | [native-policy](https://github.com/ykh09242/unity-mcp/blob/P/docs/development/ci-validation.md) |
<!-- release-evidence:end -->
```

Inside this block, use exactly these headers and values:

- **Component:** one of `unity`, `server`, `skill`, with one component per verification row. The component table's versions and immutable links must match metadata read at P, the server version read at S, and the independent skill pin C. The server archive must also match the manifest's `--from`.
- **Verification kind:** `python`, `compile`, `package-preparation`, `native-editmode`, or `native-playmode`. Compilation, package preparation and Editor execution establish different facts.
- **Actual result:** exactly `passed`, `failed`, `skipped`, `not-run`, or `historical`. Keep counts and environments in the linked actual records, not suffixes in this cell. A green workflow containing a skipped native job does not establish native execution.
- **Tested input:** `commit:` followed by the full lowercase 40-hex actual tested commit. Passed/current rows must match the selected component input P, S or C. Skipped/not-run rows use `none`. Historical local-native rows use `partial:` followed by the full lowercase SHA256 of their original nonempty source map.
- **Evidence:** exactly one `[ID](HTTPS_URL)` link, with a unique lowercase ID matching `[a-z][a-z0-9-]{0,63}`. Retain the actual job/bundle, results and input records at that link. Local paths alone are not public retention.

A historical row still requires actual supported executed records with their original result, counts and environment. Local partial source maps cannot prove completeness, even when every remaining entry matches P. They are historical-only; deleting a source/test/metadata/config entry cannot turn them into current-release evidence. This first format has no complete-local-inventory adapter.

Manual/live E2E and other unsupported executed records stay in truthful prose **outside** the checked block with their original links. Their kinds are rejected inside the block for every result. Do not disguise an executed unsupported record as not-run or evidence-free historical. Failed jobs that never produced the required execution records also stay outside the block as infrastructure failures; do not invent results for them.

### Validate the prepared body offline

```sh
python tools/check_release_evidence.py --repo . --package-commit FULL_P --notes PATH_TO_PREPARED_BODY --evidence-index PATH_TO_LOCAL_INDEX
```

The helper reads explicit local Git objects, including metadata at P and server metadata at S. It disables lazy fetching and Git replacement objects. It never checks out files, fetches history, executes note content, calls a provider, runs tests or publishes anything. An unavailable object or unsupported Git read option fails closed. Exit 0 means the supplied block agrees with its committed inputs and saved records; human review still owns feature coverage, retained public evidence and any later publication decision.

The local, untracked index has exactly `schema_version: 1` and an `evidence` object keyed by the IDs in the table. Each entry has `origin`, `kind`, `public_url` and `files`. Its URL/kind must equal the row. Each file role maps to `{ "path": "relative-file", "sha256": "full-lowercase-digest" }`; files must be ordinary local files under the index directory. Duplicate keys, linked/escaping paths, missing/extra roles, changed digests and verdict/count overrides fail.

| Origin and row | Required roles |
| --- | --- |
| `github-actions`, compile | `workflow`, `job`, `checkout`, `log` |
| `github-actions`, package-preparation | `workflow`, `job`, `checkout`, `log`, `preparation` |
| `github-actions`, python or either native kind | `workflow`, `job`, `checkout`, `log`, `results` |
| `github-actions`, skipped | `workflow`, `job`, plus a nonempty `reason`; tested input `none` |
| `local-native`, historical native only | `invocation`, `inputs`, `log`, `results` |
| `policy`, not-run only | Empty `files` object and a nonempty `reason`; tested input `none` |

CI executed rows support passed, failed or historical records. The workflow/job files are saved raw GitHub Actions REST objects with consistent repository/run/job URLs, IDs and completed status. For executed CI rows, `checkout` and `log` must contain identical bytes of the same complete saved job log; independent checkout snippets or another execution log fail. The checkout reader accepts the timestamped raw job log (or gh tab-prefixed equivalent, including its initial BOM wrapper) containing one repository checkout and its full `git log -1 --format=%H` output, before removal of checkout auth. Untimestamped relative-path continuations are recognized only for setup-uv's `cache-dependency-glob` and upload-artifact's `path` inputs inside their action preambles; those payloads cannot supply checkout evidence. Original log bytes and digest checks remain unchanged. API `head_sha` alone does not establish the actual checkout, especially for PR merge tests. Retain the trigger head separately; a legitimate actual PR merge checkout can differ and remains authoritative when its execution is bound to that same job log.

The execution log must contain actual compiler-stage output, Python/pytest invocation and version, or the Editor version/platform/runner ending for its kind. Python `results` are UTF-8 pytest JUnit; native results are UTF-8 Unity NUnit XML. Other encodings and DTD/entity declarations are unsupported. Counts and actual cases must agree. Native passed rows require positive actual passes with zero failures, inconclusive cases or skips and a successful runner. Failed combined batches cannot be promoted by extracting a passing family. `preparation` is the actual `resolved-packages.json` emitted by the existing Unity package preparer. Historical local-native supports the existing `invocation.json`, nonempty `source-identities.json`, actual Editor log and NUnit XML only. No generic passed boolean replaces these records.

The index points to existing results; it is not a second result database. No dedicated CI, application or publication workflow is introduced. See the repository's [CI validation policy](https://github.com/ykh09242/unity-mcp/blob/beta/docs/development/ci-validation.md) for execution and skip boundaries.

## Publish a fork GitHub release

1. Select the fork version and whether it is a prerelease or stable release. Complete the relevant checks for the prepared Git version and validate its prepared release evidence block.
2. Create a fork release tag on the verified commit, then publish its GitHub release with the selected prerelease state and any explicitly prepared assets. Publication is a manual maintainer operation; it does not use the upstream release workflow.
3. Explain the fork changes, identify the Unity and Python component versions, immutable server and skill commits, and actual bounded verification evidence, and credit the original MIT authorship. Link the upstream version and commit as the baseline.
4. Verify the published tag, target commit, release notes, prerelease state, and attached assets. Users can pin the Unity Git installation URL to the verified fork tag or commit.

Only tags and releases actually created for this fork belong on [Fork Releases](https://github.com/ykh09242/unity-mcp/releases). Do not relabel inherited upstream tags or release bodies as fork releases. The [Upstream Release History](/releases) page and `release-metadata.json` intentionally retain CoplayDev history.

## Legacy upstream automation

The inherited release and release-note synchronization workflows are restricted to the upstream repository. They are not a fork publication procedure. Their historical PyPI, Docker, and MCPB steps must not be used as evidence that those fork channels exist.
