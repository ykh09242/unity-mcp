# CI candidate identity

Python, license-free compilation, native Editor tests and Windows Player tests select
`${{ inputs.ref || github.sha }}`. A pull-request run therefore checks GitHub's merge
candidate, including the base branch, rather than silently switching native tests to
the PR head. Push and manual runs retain the triggering SHA. Reusable callers can
supply an explicit reviewed ref; otherwise the caller's triggering SHA is retained.
For cross-workflow comparisons callers should pass an immutable full commit SHA.

Every checkout disables persisted credentials and records the actual checked commit
and requested selector in its job summary. Full SHA selectors are checked against
`git rev-parse HEAD` before workload execution; named refs are resolved by checkout
and their actual SHA is recorded. The existing fork beta push guard remains in place.
Compilation's optional-module diff continues to compare the merge candidate with its
first parent. The full locked Python and Unity matrices are unchanged.

These selectors do not change GitHub's secret withholding for fork pull requests.
Native execution still requires the existing licensed-execution policy and scoped
credentials. No `pull_request_target` event or write permission is introduced.

## Installed archive behavior

The `installed_archive` job freezes the checked SHA before making a project-root
ZIP from `<sha>:Server`, installs that archive into a fresh environment using
`uv pip install`, and runs both probes with Python isolated mode (`-I`). This
resolves the declared runtime dependency ranges independently of `Server/uv.lock`.
The archive probe requires direct archive installation metadata and verifies that
server and dependency imports live in that environment, outside the checkout. Its
JSON evidence includes the frozen candidate SHA, resolved dependency versions,
import origins and completed HTTP/CLI behaviors.

The public checks cover healthy status without token disclosure, authentication,
browser and duplicate-token rejection, authenticated empty-instance responses,
command input validation, disconnected custom tools, CLI help/catalog loading and
raw JSON errors. Requests use the real ASGI middleware and routes with an empty,
owned hub; no application lifespan or Unity discovery is entered. Socket bind and
connect attempts are rejected. Configuration homes, logs and persistent stores
use owned temporary directories; inherited Unity MCP settings are removed. The
existing registration probe separately enters only optional-kernel lifespans.

Project-root ZIPs avoid uv 0.13.5's observed lack of subdirectory-fragment support
for local file archives. This candidate check complements `server_bootstrap`,
which still tests cold/offline installation from the pinned full GitHub archive
with its `#subdirectory=Server` selector. The latter validates the published
launcher source and may precede the current candidate until its pin is refreshed.
The new job covers Windows, Linux and Intel macOS on Python 3.11 in CI; the locked
Python 3.11-3.15 test matrix remains independent. These focused checks do not
establish Unity execution, network listener behavior, or all unlocked combinations.

## Source metadata recovery

The package source, bundle manifest and generated `release-source-inputs.json`
describe the same server and skill revisions. For an inconsistent checkout,
compare committed metadata and restore only the mismatched pin fields to a
verified existing server/skill pair, preserving unrelated edits. After committing the selected
server's skill bundle, use `tools/update_versions.py --server-commit SERVER_SHA
--skill-commit SKILL_SHA` to publish their metadata and source examples together.
Validate the receiving commit with `tools/check_source_pin_drift.py --baseline
PREVIOUS_SHA --require-current` and check the pinned skill and bundled contents.

When the previous commit contains inconsistent source metadata, the drift report
marks `baseline_state` as `invalid_metadata` and `pin_changed` as unknown. Recovery
requires the current commit's server inputs to match its valid immutable pin
exactly. Missing baseline commits and invalid current metadata still fail.
