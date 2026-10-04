# Website dependency overrides

Use the Node and npm versions declared in `package.json`; CI installs its declared npm version. npm 12 correctly validates the overridden peer graph.

Docusaurus 3.10.2 still selects older tooling. The explicit overrides keep its production and development commands on supported stable majors:

- `copy-webpack-plugin` 14 and `css-minimizer-webpack-plugin` 8 select patched `serialize-javascript` 7 instead of the vulnerable 6.x series.
- `webpack-dev-server` 6 removes the SockJS dependency that brought in vulnerable `uuid` 8. Docusaurus uses its supported programmatic API and default WebSocket transport.
- `update-notifier` 7.3.1 removes the old `got` -> `cacheable-request` -> `http-cache-semantics` chain through `latest-version` and `package-json`. It preserves the notifier factory, cached update, config and check APIs used by Docusaurus. Upgrading only `http-cache-semantics` to 4.3.0 does not fix the `max-stale` behavior in [GHSA-ch52-4w7c-c8xp](https://github.com/advisories/GHSA-ch52-4w7c-c8xp).

Remove the overrides when Docusaurus's dependency ranges select these supported versions directly. After changing them, run `npm ci --ignore-scripts`, `npm ls --all`, `npm audit`, `npm run build`, and verify `npm start` with browser navigation, local search and hot reload. Do not enable dependency lifecycle scripts just to remove installation warnings.

`npm run test:dependencies` guards against reintroducing the obsolete HTTP cache chain and checks Docusaurus's resolved notifier in isolated processes with temporary config and mocked background checks/registry responses. It also runs before production builds. The tests do not access user config or contact the registry.

## Remaining advisory

As of 2026-10-04, [GHSA-vfj7-8cjw-p6xm](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm) has no released fix for `braces` 3.0.3. A deeply nested brace pattern can exhaust the stack. Docusaurus's docs discovery and watchers receive patterns from repository/plugin configuration; no public browser-request path to attacker-controlled patterns was identified here. This is a remaining dependency risk, not a zero-warning audit result. Review changes to configured glob patterns and plugins as executable build inputs.

Do not override `chokidar` to 4/5 to conceal this advisory: those versions remove the glob watching Docusaurus uses, and other `braces` consumers remain. Recheck the official advisory and dependency releases before changing this graph; do not substitute an unreviewed fork or suppress audit output.
