import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';

const require = createRequire(import.meta.url);
const braces = require('braces');
const fastGlob = require('fast-glob');
const micromatch = require('micromatch');
const fork = 'https://codeload.github.com/FSDevelop/braces/tar.gz/28d440b5dd449dbf1fe6f3506cf94ecca4d02660';

test('every braces lock entry uses the reviewed immutable fork', () => {
  const manifest = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8'));
  const lock = JSON.parse(readFileSync(new URL('../package-lock.json', import.meta.url), 'utf8'));
  assert.equal(manifest.devDependencies.braces, fork);
  assert.equal(manifest.overrides.braces, '$braces');
  const entries = Object.entries(lock.packages).filter(([location]) =>
    location.split('node_modules/').at(-1) === 'braces');
  assert.ok(entries.length > 0);
  for (const [location, entry] of entries) {
    assert.equal(entry.resolved, fork, location);
    assert.match(entry.integrity, /^sha512-[A-Za-z0-9+/]+=*$/);
  }
  for (const consumer of ['micromatch', 'chokidar']) {
    const consumerRequire = createRequire(require.resolve(consumer));
    assert.equal(consumerRequire.resolve('braces'), require.resolve('braces'), consumer);
  }
});

const deepPatterns = {
  braces: '{'.repeat(4500) + 'a,b' + '}'.repeat(4500),
  parentheses: '('.repeat(4500) + 'x' + ')'.repeat(4500),
  mixed: '{('.repeat(101) + 'a,b' + ')}'.repeat(101),
  unbalanced: '{'.repeat(101) + 'a,b',
};

for (const [kind, pattern] of Object.entries(deepPatterns)) {
  for (const method of ['parse', 'compile', 'expand', 'stringify']) {
    test(`braces.${method} rejects excessive ${kind} nesting before stack exhaustion`, () => {
      assert.throws(() => braces[method](pattern), {
        name: 'SyntaxError',
        message: /exceeds max depth/,
      });
    });
  }
}

for (const method of ['compile', 'expand', 'stringify']) {
  test(`braces.${method} also bounds caller-supplied AST depth`, () => {
    let ast = { type: 'text', value: 'a' };
    for (let i = 0; i < 101; i++) ast = { type: 'brace', nodes: [ast] };
    ast = { type: 'root', nodes: [ast] };
    assert.throws(() => braces[method](ast), {
      name: 'RangeError',
      message: /exceeds max depth/,
    });
  });
}

test('braces cannot disable the hard cap through maxDepth options', () => {
  const nested = '{'.repeat(101) + 'a,b' + '}'.repeat(101);
  for (const maxDepth of [Infinity, NaN, 1000000, '1000000']) {
    assert.throws(() => braces.expand(nested, { maxDepth }), /exceeds max depth/);
  }
  assert.doesNotThrow(() => braces.parse('{a,b}', { maxDepth: 1.5 }));
  assert.throws(() => braces.parse('{{a,b},c}', { maxDepth: 1.5 }), /exceeds max depth/);
  assert.doesNotThrow(() => braces.expand('{'.repeat(100) + 'a,b' + '}'.repeat(100)));
});

for (const length of [1, 2]) {
  test(`braces rejects a ${length}-node cyclic AST parent chain`, () => {
    const ast = { type: 'paren', nodes: [{ type: 'text', value: 'a' }] };
    ast.parent = length === 1 ? ast : { type: 'paren', parent: ast };
    assert.throws(() => runInNewContext('braces.expand(ast)', { braces, ast }, { timeout: 1000 }), {
      name: 'RangeError',
      message: /parent chain contains a cycle/,
    });
  });
}

test('braces preserves ordinary ranges, nested sets, escaping and quoted literals', () => {
  assert.deepEqual(braces.expand('a/{01..03}/b'), ['a/01/b', 'a/02/b', 'a/03/b']);
  assert.deepEqual(braces.expand('a{b,c,/{x,y}}/e'), ['ab/e', 'ac/e', 'a/x/e', 'a/y/e']);
  assert.deepEqual(braces.expand('foo/({a,b})'), ['foo/(a)', 'foo/(b)']);
  assert.deepEqual(braces.expand('\\{'.repeat(101)), ['{'.repeat(101)]);
  assert.deepEqual(braces.expand('"' + '{'.repeat(101) + '"'), ['{'.repeat(101)]);
  assert.equal(braces.stringify('{{a}}', { escapeInvalid: true }), '{{a}}');
  assert.throws(() => braces.expand('{1..1001}'), /rangeLimit/);
});

test('braces preserves the fork base upstream unpaired-quote fix', () => {
  assert.deepEqual(braces.expand("a'b{c,d}"), ["a'bc", "a'bd"]);
});

test('Docusaurus glob consumers keep ordinary glob expansion and reject deep input', () => {
  const pattern = 'docs/**/*.{md,mdx}';
  const expected = ['docs/**/*.md', 'docs/**/*.mdx'];
  assert.deepEqual(micromatch.braces(pattern, { expand: true }), expected);
  assert.deepEqual(micromatch.braceExpand(pattern), expected);
  assert.deepEqual(fastGlob.generateTasks(pattern).flatMap(task => task.positive), expected);
  assert.throws(() => micromatch.braces(deepPatterns.braces, { expand: true }), /exceeds max depth/);
  assert.throws(() => fastGlob.generateTasks(deepPatterns.braces), /exceeds max depth/);
});
