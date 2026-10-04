import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
import postcss from 'postcss';
import { copyText } from '../src/components/CopyButton/copyText.mjs';

const site = fileURLToPath(new URL('../', import.meta.url));
const stylesheet = (path) => postcss.parse(readFileSync(resolve(site, path), 'utf8'));

function declarationsAt(css, selector, width) {
  const declarations = {};
  css.walkRules((rule) => {
    if (!rule.selector.split(',').map((value) => value.trim()).includes(selector)) return;
    for (let parent = rule.parent; parent; parent = parent.parent) {
      if (parent.type !== 'atrule' || parent.name !== 'media') continue;
      const min = parent.params.match(/min-width:\s*(\d+)px/);
      const max = parent.params.match(/max-width:\s*(\d+)px/);
      if ((min && width < Number(min[1])) || (max && width > Number(max[1]))) return;
    }
    rule.walkDecls((declaration) => { declarations[declaration.prop] = declaration.value; });
  });
  return declarations;
}

test('search reserves navigation space at supported mobile and desktop widths', () => {
  const css = stylesheet('src/css/custom.css');
  for (const width of [390, 1000, 1280, 1366]) {
    const container = declarationsAt(css, "[class*='navbarSearchContainer']", width);
    assert.equal(container.position, 'relative', `search must stay in flow at ${width}px`);
    assert.ok(!container.transform, 'search must not be centered over links');
    assert.equal(container['min-width'], '0', 'search may shrink with available space');
  }
  const input = declarationsAt(css, "[class*='navbarSearchContainer'] .navbar__search-input", 1366);
  assert.ok(!input.transition.includes('width'), 'focus must not animate layout');
  const focused = declarationsAt(css, "[class*='navbarSearchContainer'] .navbar__search-input:focus", 1366);
  assert.ok(!focused.width || focused.width === input.width, 'focus must not enlarge the search field');
  const mobile = declarationsAt(css, ".navbar .navbar__items--right [class*='navbarSearchContainer']", 390);
  assert.equal(mobile.position, 'relative', 'mobile rule must override the generated search container class');
  assert.equal(mobile.flex, '0 0 auto', 'search wrapper must reserve the full expanded input width');
  for (const width of [320, 390]) {
    const right = declarationsAt(css, '.navbar .navbar__items--right', width);
    assert.equal(right.flex, '0 0 auto', 'only the product title should shrink when search expands');
    const header = declarationsAt(css, '.navbar__inner', width);
    assert.equal(header['flex-wrap'], 'nowrap', 'expanded search must not wrap below the fixed-height header');
    const dropdown = declarationsAt(css, ".navbar .navbar__items--right [class*='dropdownMenu']", width);
    assert.ok(dropdown['max-width'].includes('100vw'), 'result width must be bounded by the viewport');
    assert.equal(dropdown.right, '0');
    assert.equal(dropdown.left, 'auto');
    const input = declarationsAt(css, '.navbar__items--right .navbar__search-input:focus', width);
    assert.ok(input['padding-right'], 'expanded input must reserve space for the clear control');
  }
  const mobileGithub = declarationsAt(css, '.navbar .navbar__items--right > .header-icon-link', 390);
  assert.equal(mobileGithub.display, 'none', 'repository access belongs in the mobile menu, not beside expanded search');
  const measure = declarationsAt(css, ".navbar .navbar__search pre[aria-hidden='true']", 390);
  assert.equal(measure.right, '0', 'hidden query measurement must not extend past the input');
  assert.equal(measure.left, 'auto');
  assert.ok(!measure.width && !measure['max-width'] && !measure.display, 'measurement width and participation must be preserved');
});

test('release URL can wrap and shrink without clipping on narrow screens', () => {
  const css = stylesheet('src/components/HomeHero/styles.module.css');
  for (const width of [390, 1000]) {
    const url = declarationsAt(css, '.installUrl', width);
    assert.equal(url['min-width'], '0');
    assert.equal(url['overflow-wrap'], 'anywhere');
    assert.notEqual(url['white-space'], 'nowrap');
    assert.notEqual(url.overflow, 'hidden');
  }
});

test('full search results do not inherit document heading gaps or clip long titles', () => {
  const css = stylesheet('src/css/custom.css');
  for (const width of [320, 390, 1366]) {
    const row = declarationsAt(css, ".container article[class*='searchResultItem']", width);
    const heading = declarationsAt(css, ".container article[class*='searchResultItem'] > h2", width);
    assert.equal(heading.margin, '0', 'search titles must not inherit document section spacing');
    assert.equal(row['overflow-wrap'], 'anywhere', 'long query-derived titles must remain readable');
    assert.ok(!row.height && !heading.height, 'result rows must grow with title and snippet content');
    assert.notEqual(row.overflow, 'hidden');
  }
});

test('site typography remains readable without viewport-scaled type or tracking', () => {
  function visit(dir) {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const path = resolve(dir, entry.name);
      if (entry.isDirectory()) visit(path);
      else if (entry.name.endsWith('.css')) {
        postcss.parse(readFileSync(path, 'utf8')).walkDecls((declaration) => {
          if (declaration.prop === 'font-size') assert.ok(!/\b[\d.]+vw\b/.test(declaration.value), path);
          if (declaration.prop === 'letter-spacing') assert.equal(declaration.value, '0', path);
        });
      }
    }
  }
  visit(resolve(site, 'src'));
});

test('native clipboard receives the exact immutable release URL', async () => {
  const url = 'https://github.com/ykh09242/unity-mcp.git?path=/MCPForUnity#ykh09242-v1.2.3';
  const writes = [];
  await copyText(url, { navigator: { clipboard: { writeText: async (text) => writes.push(text) } } });
  assert.deepEqual(writes, [url]);
});

test('clipboard denial is reported, not silently treated as success', async () => {
  const denied = new Error('Permission denied');
  await assert.rejects(copyText('text', {
    navigator: { clipboard: { writeText: async () => { throw denied; } } },
  }), (error) => error === denied);
});

function legacyDocument(result, error) {
  const events = [];
  const textarea = {
    style: {},
    setAttribute: (name) => events.push(name),
    select: () => events.push('select'),
    remove: () => events.push('remove'),
  };
  return {
    events,
    textarea,
    document: {
      activeElement: { focus: (options) => events.push(['focus', options]) },
      createElement: () => textarea,
      body: { appendChild: () => events.push('append') },
      execCommand: (command) => {
        events.push(command);
        if (error) throw error;
        return result;
      },
    },
  };
}

test('legacy copy cleans up and restores keyboard focus without scrolling', async () => {
  const fixture = legacyDocument(true);
  await copyText('release URL', { navigator: {}, document: fixture.document });
  assert.equal(fixture.textarea.value, 'release URL');
  assert.deepEqual(fixture.events, ['readonly', 'append', 'select', 'copy', 'remove', ['focus', { preventScroll: true }]]);
});

test('legacy clipboard failure and exceptions still restore focus and remove temporary text', async () => {
  for (const fixture of [legacyDocument(false), legacyDocument(false, new Error('Unavailable'))]) {
    await assert.rejects(copyText('release URL', { navigator: {}, document: fixture.document }));
    assert.deepEqual(fixture.events.slice(-2), ['remove', ['focus', { preventScroll: true }]]);
  }
});
