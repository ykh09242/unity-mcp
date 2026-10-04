---
description: Fork analytics defaults and retained upstream adoption tooling.
---

# External analytics

This Git-only fork does not advertise PyPI download counts, a public adoption badge, a provisioned analytics dashboard, or a hosted documentation site.

## Retained upstream tooling

`website/scripts/fetch-stats.mjs` and its tests are retained upstream tooling. Their CoplayDev repository and `mcpforunityserver` PyPI statistics describe upstream adoption, not this fork. The legacy stats workflow is restricted to upstream; it does not establish fork publication or analytics services.

GitHub stars, forks, and traffic belong to the repository queried. Upstream counts must not be presented as ykh09242 fork usage. GitHub Actions run summaries are visible according to repository and run access; do not assume a public repository's run summary is private.

## Documentation analytics

The website's optional GoatCounter integration is inactive unless configured through the existing environment setting. No fork GoatCounter site or credentials are provided here. Before enabling an operator-owned service, configure the deployment origin, review that service's privacy policy, and document the actual collection behavior.

## In-product telemetry

The fork has no telemetry destination by default and sends no telemetry to Coplay. Explicit endpoint configuration and the existing enabled gate are both required. See [telemetry](./telemetry.md) for details and opt-out controls.
