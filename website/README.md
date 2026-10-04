# Unity MCP (ykh09242) - Documentation Site

Docusaurus 3.x documentation for the Git-only ykh09242 fork. The GitHub Pages deployment target is [https://ykh09242.github.io/unity-mcp/](https://ykh09242.github.io/unity-mcp/): the default production origin is `https://ykh09242.github.io`, with `/unity-mcp/` as its base path. `WEBSITE_URL` can override the origin for another configured deployment. Local development still uses `http://localhost:3000/unity-mcp/`.

Fork versions use their own SemVer series. Published fork versions belong on [Fork Releases](https://github.com/ykh09242/unity-mcp/releases); the retained `release-metadata.json` and `/releases` page describe upstream CoplayDev history and baseline versions.

## Local development

```bash
cd website
npm install
npm run start    # serves at http://localhost:3000/unity-mcp/
```

Edits to Markdown under `docs/` hot-reload.

## Build

```bash
npm run build    # outputs to website/build/
npm run serve    # serves the build for local verification
```

## GitHub Pages deployment

The `Docs — Build & Deploy` workflow builds PRs and publishes eligible `beta` pushes or manual dispatches. Deployment is restricted to `ykh09242/unity-mcp` and the upstream repository; arbitrary forks do not deploy automatically. Set the fork's Pages source to **GitHub Actions**, then run the workflow on `beta` or push a qualifying docs change. Confirm the successful Pages deployment and the published URL after the run; a local build alone does not publish the site.

See [Docs Workflow](docs/contributing/docs.md#deploy) for setup and verification, and [Releasing](docs/contributing/releases.md) for the separate GitHub release process. No Asset Store or PyPI publication is part of either workflow.

## Layout

```
website/
  docs/                    Markdown content
    getting-started/       Overview, install, setup wizard, first prompt
    guides/                How-to content (migrated in M2)
    reference/             Tool & resource reference (auto-generated in M3)
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

## Tool reference (M3+)

Files under `docs/reference/tools/` and `docs/reference/resources/` are **generated** from the Python `@mcp_for_unity_tool` and `@mcp_for_unity_resource` registries by `tools/generate_docs_reference.py`. Do not hand-edit those files outside the `<!-- examples:start --><!-- examples:end -->` blocks — the generator will overwrite them.
