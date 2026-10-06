# Unity MCP (ykh09242) - Documentation Site

Docusaurus 3.x documentation for the Git-only ykh09242 fork. The GitHub Pages deployment target is [https://ykh09242.github.io/unity-mcp/](https://ykh09242.github.io/unity-mcp/): the default production origin is `https://ykh09242.github.io`, with `/unity-mcp/` as its base path. `WEBSITE_URL` can override the origin for another configured deployment. Local development still uses `http://localhost:3000/unity-mcp/`.

Fork versions use their own SemVer series. Published fork versions belong on [Fork Releases](https://github.com/ykh09242/unity-mcp/releases); the retained `release-metadata.json` and `/releases` page describe upstream CoplayDev history and baseline versions.

## Local development

Use a Node.js version matching `engines.node` in `package.json` and the npm
version in `packageManager`. CI uses the same npm pin. Use `npm ci` to reproduce
the committed dependency tree; `npm update` is reserved for reviewed updates.

```bash
cd website
npm ci
npm run start    # serves at http://localhost:3000/unity-mcp/
```

Edits to Markdown under `docs/` hot-reload.

## Build

```bash
npm run build    # outputs to website/build/
npm run serve    # serves the build for local verification
```

## Dependency security checks

`npm run build` runs the dependency and UI contracts before bundling and checks
published routes afterward. `npm run test:dependencies` can run the dependency
contracts separately. Keep all `@docusaurus/*` dependencies on the same release.
The npm `allowScripts` policy explicitly denies `core-js`'s optional donation
banner script; its polyfills do not need an install hook. Review new install
scripts individually instead of approving all dependencies.

Two transitive overrides bridge Docusaurus 3's older dependency ranges:

- `tinypool` 2.2.x includes both worker-option security fixes (minimum 2.1.2).
  The contract test exercises real worker threads, Docusaurus's SSG data format,
  and rejection of inherited task filename/export options. See
  [constructor options](https://github.com/advisories/GHSA-5gmw-xhrv-c9v3) and
  [task options](https://github.com/advisories/GHSA-85c8-ppgw-ccpr).
- `postcss-selector-parser` 7.1.6 removes the quadratic flat-selector parsing
  path. The override covers older CSS minifier consumers as well as newer
  PostCSS plugins. See the
  [selector parsing advisory](https://github.com/advisories/GHSA-rj75-hqrm-r3gf).

`braces` uses the official npm release selected by its parent packages and
recorded in `package-lock.json`, without a direct dependency or fork override.
The contracts check registry provenance and ordinary glob behavior, not the
removed fork's custom depth limits. `npm audit` still reports the upstream
[braces advisory](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm), including
affected parent packages. Keep that output visible; this is a scoped risk
assessment, not a claim that npm has cleared the advisory. Do not apply the
audit's suggested Docusaurus downgrade blindly.

### Braces decision (2026-10-06)

Return to the official `braces` 3.0.3 release for this build-only dependency.
The previous FSDevelop pin came from upstream
[PR #72](https://github.com/micromatch/braces/pull/72), which was closed without
merging. The
[maintainer's objection](https://github.com/micromatch/braces/issues/70#issuecomment-5995348316)
and [PR #75's withdrawal](https://github.com/micromatch/braces/pull/75#issuecomment-5998382636)
correctly distinguish limiting recursion from preventing process termination:
the guard still throws, and callers must handle errors. It does not bound total
expansion output, all CPU/memory use, or regex execution complexity. The default
character limit alone is also not a proof that recursive walkers cannot exhaust
the stack on every Node version.

In this project, braces is part of the Node-based documentation build/watch
toolchain, including developer-configured globs and local filesystem paths.
The published Pages site serves static files, and browser search uses
Lunr rather than passing visitor queries to braces. No remote visitor-to-braces
path has been identified; build/watch inputs are a different
trust boundary from an online service accepting arbitrary patterns. This npm
dependency is not part of the Python MCP server or Unity package.

The [advisory correction proposal](https://github.com/github/advisory-database/pull/10132)
was still open and unmerged on the review date; the published advisory still
listed no patched npm release. Neither the maintainer's disagreement nor the
fork's tests establishes an official withdrawal. Reassess when the advisory
receives an authoritative resolution, a supported upstream release lands, or a
parent package removes the dependency. Any future service accepting external
glob patterns needs its own input limits, exception handling and resource
isolation review; this build-tool decision must not be reused as that approval.

## GitHub Pages deployment

The `Docs — Build & Deploy` workflow builds PRs and publishes eligible `beta` pushes or manual dispatches. Deployment is restricted to `ykh09242/unity-mcp` and the upstream repository; arbitrary forks do not deploy automatically. Set the fork's Pages source to **GitHub Actions**, then run the workflow on `beta` or push a qualifying docs change. Confirm the successful Pages deployment and the published URL after the run; a local build alone does not publish the site.

See [Docs Workflow](docs/contributing/docs.md#deploy) for setup and verification, and [Releasing](docs/contributing/releases.md) for the separate GitHub release process. No Asset Store or PyPI publication is part of either workflow.

## Layout

```
website/
  docs/                    Markdown content
    getting-started/       Overview, install, migration, clients, first prompt
    guides/                Task-focused routing, security, CLI and setup guides
    reference/             Generated tool/resource schemas and authored examples
    architecture/          System design notes
    contributing/          Dev setup, testing, releases
    migrations/            Version-upgrade guides
  src/
    css/custom.css         Theme overrides
  static/
    img/                   Logo, favicon, social card
  docusaurus.config.js     Site config — brand title/URL/social links live here
  sidebars.js              Navigation tree
```

## Brand-neutral URL policy

URL slugs must NOT contain `mcp-for-unity` or `unity-mcp`. The brand name lives in `docusaurus.config.js` (`title`, `tagline`, navbar/footer copy). A future product rename should touch this file and content text — not URL paths. See the rename-proofing section of the plan.

## Adding a redirect when renaming a slug

Never rename a published slug without adding an entry to `plugin-client-redirects` in `docusaurus.config.js`. External backlinks must keep working.

## Content ownership and validation

Files under `docs/reference/tools/` and `docs/reference/resources/` are **generated** from the Python `@mcp_for_unity_tool` and `@mcp_for_unity_resource` registries by `tools/generate_docs_reference.py`. Do not hand-edit those files outside the `<!-- examples:start --><!-- examples:end -->` blocks — the generator will overwrite them.

Use [Install](docs/getting-started/install.md), [Migration](docs/getting-started/migrate.md), [Security And Consent](docs/guides/security.md), and [Troubleshooting](docs/guides/troubleshooting.md) as canonical user guides. Component READMEs point to these instead of maintaining parallel tool catalogs. Preserve published slugs and add redirects deliberately when changing routes.

For Markdown changes, check links, JSON/command examples and manifest pins without launching Unity or the MCP server. For site presentation changes, verify the rendered experience at desktop/mobile sizes as well as the build. Reference freshness, site build, local browser inspection and a successful Pages deployment are separate checks; report only the ones actually performed.

Stable release documentation must distinguish the fixed `ykh09242-v1.0.0` snapshot from moving `beta` content. `/releases` and historical migration pages retain upstream versions/services, not current fork installation claims. See [Docs Workflow](docs/contributing/docs.md) for generation/deployment boundaries.
