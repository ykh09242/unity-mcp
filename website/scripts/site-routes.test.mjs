import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { load } from 'cheerio';

const siteDir = fileURLToPath(new URL('../', import.meta.url));
// Match --out-dir with WEBSITE_BUILD_DIR for custom production previews.
const buildDir = resolve(siteDir, process.env.WEBSITE_BUILD_DIR || 'build');
const notFound = load(readFileSync(resolve(buildDir, '404.html'), 'utf8'));
const homepage = load(readFileSync(resolve(buildDir, 'index.html'), 'utf8'));
const siteUrl = homepage('link[rel="canonical"]').attr('href');
assert.ok(siteUrl, 'the generated homepage must declare the site URL');

for (const route of ['search', 'getting-started/install', 'reference/tools']) {
  test(`published ${route}/ resolves directly to its server-rendered directory index`, () => {
    const path = resolve(buildDir, route, 'index.html');
    assert.ok(existsSync(path), `${route}/ needs an index.html on GitHub Pages`);
    const document = load(readFileSync(path, 'utf8'));
    assert.ok(document('h1').first().text().trim(), 'the route must contain its server-rendered heading');
    assert.notEqual(document('h1').first().text(), notFound('h1').first().text(), 'the route must not serve the 404 document');
    const canonical = document('link[rel="canonical"]').attr('href');
    assert.equal(canonical, new URL(`${route}/`, siteUrl).href);
    if (route === 'search') {
      assert.equal(document('input[type="search"][name="q"]').length, 1, 'direct search load must render the actual search page before hydration');
    }
  });
}
