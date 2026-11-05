# Website dependency overrides

Use the Node and npm versions declared in `package.json`; CI installs its declared npm version. npm 12 correctly validates the overridden peer graph.

Docusaurus 3.10.2 still selects older tooling. The explicit overrides keep its production and development commands on supported stable majors:

- `copy-webpack-plugin` 14 and `css-minimizer-webpack-plugin` 8 select patched `serialize-javascript` 7 instead of the vulnerable 6.x series.
- `webpack-dev-server` 6 removes the SockJS dependency that brought in vulnerable `uuid` 8. Docusaurus uses its supported programmatic API and default WebSocket transport.
- `update-notifier` 7.3.1 removes the old `got` -> `cacheable-request` -> `http-cache-semantics` chain through `latest-version` and `package-json`. It preserves the notifier factory, cached update, config and check APIs used by Docusaurus. Upgrading only `http-cache-semantics` to 4.3.0 does not fix the `max-stale` behavior in [GHSA-ch52-4w7c-c8xp](https://github.com/advisories/GHSA-ch52-4w7c-c8xp).

Remove the overrides when Docusaurus's dependency ranges select these supported versions directly. After changing them, run `npm ci --ignore-scripts`, `npm ls --all`, `npm audit`, `npm run build`, and verify `npm start` with browser navigation, local search and hot reload. Do not enable dependency lifecycle scripts just to remove installation warnings.

`npm run test:dependencies` guards against reintroducing the obsolete HTTP cache chain and checks Docusaurus's resolved notifier in isolated processes with temporary config and mocked background checks/registry responses. It also runs before production builds. The tests do not access user config or contact the registry.

## Released braces dependency

The lockfile uses the official npm `braces` release shared by Docusaurus's glob consumers. There is no direct `braces` dependency, fork override or website-local remote-archive exception. `scripts/braces-contracts.test.mjs` checks registry provenance, shared resolution and ordinary glob behavior.

GitHub withdrew the advisory for `braces` 3.0.3 on 2026-10-23. The November 5 npm audit reflects that withdrawal. [GitHub advisory record](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm)

Treat configured glob patterns and build plugins as trusted build inputs. The library's optional `maxLength` check is covered as an API contract. Docusaurus's glob-based watchers use their ordinary defaults.

## November 2026 refresh

On November 5, npm reported one low-severity advisory in `deepmerge` and counted four dependents in Docusaurus's SVG build chain. The installed version is 4.3.1. The advisory lists no patched release. Recheck the upstream package and SVGR resolution at the next refresh. [deepmerge advisory](https://github.com/advisories/GHSA-gcqx-w48m-w2h4)

The minimum `postcss-selector-parser` override is 7.1.7, which preserves trailing whitespace in lossless parsing. Local search and development hot reload were exercised with the refreshed webpack and Markdown packages. [Selector parser changelog](https://github.com/postcss/postcss-selector-parser/blob/v7.1.7/CHANGELOG.md)

## October 2026 refresh

React and React DOM are paired at 19.3.1. Its [RSC transport changes](https://github.com/react/react/releases/tag/v19.3.1) do not enable Server Components in this Docusaurus site.

[webpack-dev-server 6.1.0](https://github.com/webpack/webpack-dev-server/releases/tag/v6.1.0) improves startup validation, hot-reload recovery and host/origin handling. Its browser client now comes from webpack-dev-middleware; keep Docusaurus's default WebSocket transport and verify actual development navigation, local search and hot reload after updates. No SSE or multi-configuration plugin migration is needed for this site.
