import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const fastGlob = require('fast-glob');
const micromatch = require('micromatch');
const consumerRequire = createRequire(require.resolve('micromatch'));
const braces = consumerRequire('braces');

test('braces is an upstream registry dependency without a fork override', () => {
  const manifest = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8'));
  const lock = JSON.parse(readFileSync(new URL('../package-lock.json', import.meta.url), 'utf8'));
  assert.equal(manifest.dependencies.braces, undefined);
  assert.equal(manifest.devDependencies.braces, undefined);
  assert.equal(manifest.overrides.braces, undefined);
  const entries = Object.entries(lock.packages).filter(([location]) =>
    location.split('node_modules/').at(-1) === 'braces');
  assert.ok(entries.length > 0);
  for (const [location, entry] of entries) {
    assert.ok(require('semver').gte(entry.version, '3.0.3'), location);
    assert.equal(entry.resolved, `https://registry.npmjs.org/braces/-/braces-${entry.version}.tgz`, location);
    assert.match(entry.integrity, /^sha512-[A-Za-z0-9+/]+=*$/);
  }
  for (const consumer of ['micromatch', 'chokidar']) {
    const resolver = createRequire(require.resolve(consumer));
    assert.equal(resolver.resolve('braces'), consumerRequire.resolve('braces'), consumer);
  }
});

test('released braces preserves normal ranges, nested sets and escaping', () => {
  assert.deepEqual(braces.expand('a/{01..03}/b'), ['a/01/b', 'a/02/b', 'a/03/b']);
  assert.deepEqual(braces.expand('a{b,c,/{x,y}}/e'), ['ab/e', 'ac/e', 'a/x/e', 'a/y/e']);
  assert.deepEqual(braces.expand('foo/({a,b})'), ['foo/(a)', 'foo/(b)']);
  assert.deepEqual(braces.expand('\\{'.repeat(101)), ['{'.repeat(101)]);
  assert.equal(braces.stringify('{{a}}', { escapeInvalid: true }), '{{a}}');
});

test('released braces exposes catchable input-limit errors, not process safety guarantees', () => {
  // This tests the library API, not an input limit installed in Docusaurus.
  assert.throws(() => braces.compile('{'.repeat(257), { maxLength: 256 }), {
    name: 'SyntaxError',
    message: /exceeds max characters/,
  });
});

test('Docusaurus glob consumers preserve normal brace expansion', () => {
  const pattern = 'docs/**/*.{md,mdx}';
  const expected = ['docs/**/*.md', 'docs/**/*.mdx'];
  assert.deepEqual(micromatch.braces(pattern, { expand: true }), expected);
  assert.deepEqual(micromatch.braceExpand(pattern), expected);
  assert.deepEqual(fastGlob.generateTasks(pattern).flatMap(task => task.positive), expected);
});
