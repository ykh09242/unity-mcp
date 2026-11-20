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

## November 21, 2026

The npm 12.2.3 lock refresh advances `minimizer-webpack-plugin` from 5.14.0 to 5.14.1. The published archive checks options before loading `schema-utils` and the full schema. Its new `schema-utils` minimum is 4.8.5, which the site already resolves. All 13 direct dependencies and existing overrides retain their selected versions. The tarball matches the registry SHA-512 integrity, the offline lock-only repeat produced the same graph and npm audit reported zero findings. [Published package metadata](https://registry.npmjs.org/minimizer-webpack-plugin/5.14.1) · [Published archive](https://registry.npmjs.org/minimizer-webpack-plugin/-/minimizer-webpack-plugin-5.14.1.tgz)

## November 20, 2026

### PR reproduction follow-up

npm 12.2.3 resolved eight further transitive updates across the 1,319 lock entries including the root. All 13 direct dependencies and the explicit overrides retained their versions. An offline repeat produced a byte-identical lock. These checks used lock-only resolution with lifecycle scripts and registry audit disabled. The site build and route contracts run in the consuming commit's CI.

| Updated packages | Upstream change |
| --- | --- |
| `mdast-util-directive` 3.2.1, `mdast-util-find-and-replace` 3.0.5 | Handle empty text directives before a colon and terminate replacement on empty matches. [Directive change](https://github.com/syntax-tree/mdast-util-directive/compare/3.2.0...3.2.1) · [Replacement change](https://github.com/syntax-tree/mdast-util-find-and-replace/compare/3.0.4...3.0.5) |
| `mdast-util-gfm-autolink-literal` 2.1.2, `mdast-util-to-markdown` 2.2.4 | Correct escaping for URL prefixes and Markdown destinations beginning with a less-than sign. [Autolinks](https://github.com/syntax-tree/mdast-util-gfm-autolink-literal/compare/2.1.0...2.1.2) · [Destinations](https://github.com/syntax-tree/mdast-util-to-markdown/compare/2.2.3...2.2.4) |
| `mdast-util-mdx-jsx` 3.2.2, `stringify-entities` 4.0.5 | Preserve ampersands in MDX attribute values and entity terminators before a literal semicolon. [MDX attributes](https://github.com/syntax-tree/mdast-util-mdx-jsx/compare/3.2.1...3.2.2) · [Entities](https://github.com/wooorm/stringify-entities/compare/4.0.4...4.0.5) |
| `terser` 5.51.3, `webpack-dev-middleware` 8.6.2 | Terser corrects source content disclosure when processing input source maps and handles two minification edge cases. The development middleware now precompiles its options validator. [Terser changelog](https://github.com/terser/terser/blob/v5.51.3/CHANGELOG.md) · [Middleware changelog](https://github.com/webpack/webpack-dev-middleware/blob/v8.6.2/CHANGELOG.md) |

### Earlier qualification

The npm registry audit reports zero vulnerabilities. GitHub withdrew the `picomatch` advisory on November 18, and the lockfile still resolves the official 2.3.2 and 4.0.7 releases required by the existing glob consumers. The advisory withdrawal accounts for the disappearance of the November 18 findings. [GitHub advisory record](https://github.com/advisories/GHSA-fc5q-39xc-vjpf) · [picomatch releases](https://github.com/micromatch/picomatch/releases)

The compatible lock refresh updates webpack to 5.113.0, webpack-sources to 3.7.1, Autoprefixer to 10.7.0 and hast-util-raw to 9.2.1. Webpack improves hot updates and tracks ESM build dependencies without `es-module-lexer`. Webpack-sources preserves source buffers restored from webpack's filesystem cache. Autoprefixer fixes CSS value matching and compound feature queries in the PostCSS pipeline. The Markdown raw HTML converter now requires parse5 8.1, which the previous lockfile already resolved. [Webpack release](https://github.com/webpack/webpack/releases/tag/v5.113.0) · [Webpack-sources release](https://github.com/webpack/webpack-sources/releases/tag/v3.7.1) · [Autoprefixer release](https://github.com/postcss/autoprefixer/releases/tag/10.7.0) · [hast-util-raw release](https://github.com/syntax-tree/hast-util-raw/releases/tag/9.2.1)

## November 18, 2026

The minimum `postcss-selector-parser` override is 7.2.1. CSS Modules now keeps an escaped identifier consistent between the emitted selector and the JavaScript class export. The dependency test exercises the same PostCSS scoping plugin used in the production build. [Selector parser changelog](https://github.com/postcss/postcss-selector-parser/blob/main/CHANGELOG.md#721---2026-11-10)

The npm 12.2.3 audit reports 36 high-severity package entries from one `picomatch` advisory in the documentation build and watcher dependencies. The advisory covers releases through 4.0.7, and npm lists no available fix. The November 11 `deepmerge` finding is absent from this audit. [GitHub advisory](https://github.com/advisories/GHSA-fc5q-39xc-vjpf) · [Refresh and verification](../docs/development/dependency-refresh-2026-11-18.md)

## November 11, 2026

On November 11, npm 12.2.2 still reports one low-severity advisory in `deepmerge` and counts four dependents in Docusaurus's SVG build chain (five low entries total). The installed version is 4.3.1 and the advisory lists no patched release. `npm audit` exits 1 for these findings; it does not report a failed installation or an invalid dependency graph. Its force-fix suggestion downgrades `@docusaurus/preset-classic` to 3.6.3, so do not apply it. Recheck the upstream package and SVGR resolution at the next refresh. [deepmerge advisory](https://github.com/advisories/GHSA-gcqx-w48m-w2h4)

The minimum `postcss-selector-parser` override is 7.2.0. It retains the 7.1.7 trailing-whitespace correction and includes the subsequent CPU-resource and escaping fixes. Dependency contracts exercise escaped identifiers, functional pseudo-classes, attribute flags, lossless parsing and the optional `maxInputLength` limit. That limit is opt-in; this update does not add a site-wide selector cap. [Selector parser changelog](https://github.com/postcss/postcss-selector-parser/blob/7591d8d67f42fde79490d5e9c8fac0b8deb43fa2/CHANGELOG.md)

The compatible transitive refresh keeps Docusaurus 3.10.2, React/DOM 19.3.1 and the existing override/lifecycle policies. It includes Undici 7.30.2 and updates Markdown, CSS, watcher and search tooling. Production navigation, local autocomplete/full search and development CSS hot reload were exercised on owned loopback servers. [Full refresh and verification notes](../docs/development/dependency-refresh-2026-11-11.md#website-dependency-graph)

## October 2026 refresh

React and React DOM are paired at 19.3.1. Its [RSC transport changes](https://github.com/react/react/releases/tag/v19.3.1) do not enable Server Components in this Docusaurus site.

[webpack-dev-server 6.1.0](https://github.com/webpack/webpack-dev-server/releases/tag/v6.1.0) improves startup validation, hot-reload recovery and host/origin handling. Its browser client now comes from webpack-dev-middleware; keep Docusaurus's default WebSocket transport and verify actual development navigation, local search and hot reload after updates. No SSE or multi-configuration plugin migration is needed for this site.
