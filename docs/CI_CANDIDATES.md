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
