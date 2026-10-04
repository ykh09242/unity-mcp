# Website dependency overrides

Use the Node and npm versions declared in `package.json`; CI installs its declared npm version. npm 12 correctly validates the overridden peer graph.

Docusaurus 3.10.2 still selects older tooling. The explicit overrides keep its production and development commands on supported stable majors:

- `copy-webpack-plugin` 14 and `css-minimizer-webpack-plugin` 8 select patched `serialize-javascript` 7 instead of the vulnerable 6.x series.
- `webpack-dev-server` 6 removes the SockJS dependency that brought in vulnerable `uuid` 8. Docusaurus uses its supported programmatic API and default WebSocket transport.
- `update-notifier` 7.3.1 removes the old `got` -> `cacheable-request` -> `http-cache-semantics` chain through `latest-version` and `package-json`. It preserves the notifier factory, cached update, config and check APIs used by Docusaurus. Upgrading only `http-cache-semantics` to 4.3.0 does not fix the `max-stale` behavior in [GHSA-ch52-4w7c-c8xp](https://github.com/advisories/GHSA-ch52-4w7c-c8xp).

Remove the overrides when Docusaurus's dependency ranges select these supported versions directly. After changing them, run `npm ci --ignore-scripts`, `npm ls --all`, `npm audit`, `npm run build`, and verify `npm start` with browser navigation, local search and hot reload. Do not enable dependency lifecycle scripts just to remove installation warnings.

`npm run test:dependencies` guards against reintroducing the obsolete HTTP cache chain and checks Docusaurus's resolved notifier in isolated processes with temporary config and mocked background checks/registry responses. It also runs before production builds. The tests do not access user config or contact the registry.

## Pinned braces security fork

The `braces` override selects the requested [FSDevelop fork at commit `28d440b5dd449dbf1fe6f3506cf94ecca4d02660`](https://github.com/FSDevelop/braces/commit/28d440b5dd449dbf1fe6f3506cf94ecca4d02660), from `fix/limit-nesting-depth`. The fork's `master` does **not** contain this fix. Keep the full commit SHA in the manifest and lockfile; do not follow a mutable branch.

The fork is an explicit development dependency, reused by all transitive callers through the `$braces` override. Its GitHub archive URL contains the full commit SHA, and the lockfile adds SHA-512 integrity verification. Using the archive avoids npm's Git-dependency integrity-check skip. npm 12 blocks remote archives by default, so the website-local `.npmrc` opts in with `allow-remote=root`. This permits project-declared archives without allowing arbitrary transitive remote fetches or changing global npm settings. Git fetching remains disabled by default.

This addresses [GHSA-vfj7-8cjw-p6xm](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm) by capping brace/parenthesis nesting at 100 and bounding recursive AST processing. Excessive patterns now throw explicit depth errors instead of exhausting the JavaScript stack. Caller-supplied cyclic AST parent chains are also rejected. The fork includes upstream fixes for unpaired quotes and invalid-block parsing relative to npm's 3.0.3; the normal documentation glob patterns remain supported.

`scripts/braces-contracts.test.mjs` checks the exact locked source, shared consumer resolution, deep balanced/unbalanced strings, direct ASTs, cyclic parents, depth-option bypass attempts and ordinary Docusaurus glob behavior. The fork retains the package version `3.0.3`, so `npm audit` still reports this version-based advisory and its affected dependents. Do not suppress it or relabel the package to obtain a zero-warning result: registry advisory matching cannot distinguish this reviewed patched commit. The pinned-source checks and behavioral regressions provide the verification here.

As of 2026-10-04, the official npm release has no fix for this advisory. Replace the fork once a reviewed upstream release passes the same regression and site checks. Review changes to configured globs and plugins as executable build inputs; this depth fix is not a general expansion-size limit. Do not override `chokidar` to 4/5: those versions remove the glob watching Docusaurus uses. No dependency lifecycle scripts are needed for the fork.
