import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { Readable } from 'node:stream';
import { fileURLToPath, pathToFileURL } from 'node:url';

const siteDir = fileURLToPath(new URL('../', import.meta.url));
const require = createRequire(import.meta.url);
const coreRequire = createRequire(require.resolve('@docusaurus/core/package.json'));
const notifierUrl = pathToFileURL(coreRequire.resolve('update-notifier')).href;
const removed = ['got', 'cacheable-request', 'http-cache-semantics'];

test('locked search dependencies exclude deprecated whatwg-encoding', () => {
  const lock = JSON.parse(readFileSync(path.join(siteDir, 'package-lock.json'), 'utf8'));
  for (const [location, entry] of Object.entries(lock.packages)) {
    assert.notEqual(location.split('node_modules/').at(-1), 'whatwg-encoding', location);
    assert.equal(entry.dependencies?.['whatwg-encoding'], undefined, location);
  }
});

const searchRequire = createRequire(require.resolve('@easyops-cn/docusaurus-search-local/package.json'));
const cheerioRequire = createRequire(searchRequire.resolve('cheerio'));

test('search Cheerio can consume the encoding API through CommonJS', () => {
  const encoding = cheerioRequire('encoding-sniffer');
  assert.equal(typeof encoding.decodeBuffer, 'function');
  assert.equal(typeof encoding.DecodeStream, 'function');
  assert.equal(typeof encoding.getEncoding, 'function');
  assert.equal(typeof searchRequire('cheerio').loadBuffer, 'function');
});

// Cross the decoder's sniffing window before the text that needs decoding.
const encodingPadding = '<!--' + 'x'.repeat(1024) + '-->';
const encodingFixtures = [
  ['UTF-8', Buffer.from(`<meta charset="utf-8">${encodingPadding}<p>\uac80\uc0c9 café 🎮</p>`), '\uac80\uc0c9 café 🎮'],
  ['UTF-8 BOM', Buffer.concat([
    Buffer.from([0xef, 0xbb, 0xbf]), Buffer.from(`${encodingPadding}<p>\uac80\uc0c9 café 🎮</p>`),
  ]), '\uac80\uc0c9 café 🎮'],
  ['Windows-1252', Buffer.concat([
    Buffer.from(`<meta charset="windows-1252">${encodingPadding}<p>caf`),
    Buffer.from([0xe9, 0x20, 0x80]), Buffer.from('</p>'),
  ]), 'café €'],
];

for (const [name, buffer, expected] of encodingFixtures) {
  test(`search Cheerio decodes ${name} HTML buffers`, () => {
    const { loadBuffer } = searchRequire('cheerio');
    assert.equal(loadBuffer(buffer)('p').text(), expected);
  });

  test(`search Cheerio decodes ${name} HTML across stream chunk boundaries`, async () => {
    const { decodeStream } = searchRequire('cheerio');
    const document = await new Promise((resolve, reject) => {
      const decoder = decodeStream({}, (error, result) => error ? reject(error) : resolve(result));
      decoder.on('error', reject);
      // One-byte chunks split the BOM, metadata and multibyte UTF-8 characters.
      Readable.from(Array.from(buffer, (byte) => Buffer.from([byte]))).pipe(decoder);
    });
    assert.equal(document('p').text(), expected);
  });
}

for (const [name, minimum] of [['tinypool', '2.1.2'], ['postcss-selector-parser', '7.1.6']]) {
  test(`every locked ${name} includes the published security fixes`, () => {
    const lock = JSON.parse(readFileSync(path.join(siteDir, 'package-lock.json'), 'utf8'));
    const entries = Object.entries(lock.packages)
      .filter(([location]) => location.endsWith(`node_modules/${name}`));
    assert.ok(entries.length > 0, `${name} must be checked, not silently absent`);
    for (const [location, entry] of entries) {
      assert.ok(require('semver').gte(entry.version, minimum), `${location}: ${entry.version} < ${minimum}`);
    }
  });
}

test('Docusaurus worker pool preserves SSG data and ignores inherited task options', async () => {
  const { default: Tinypool } = await import(pathToFileURL(coreRequire.resolve('tinypool')).href);
  const directory = mkdtempSync(path.join(tmpdir(), 'docs-worker-test-'));
  let pool;
  try {
    const worker = path.join(directory, 'worker.mjs');
    writeFileSync(worker, `
      import { workerData } from 'node:worker_threads';
      export default (task) => ({
        task,
        params: workerData[1].params,
        hasWorkerId: Boolean(process.__tinypool_state__?.workerId),
      });
    `);
    pool = new Tinypool({
      filename: pathToFileURL(worker).href,
      minThreads: 1,
      maxThreads: 1,
      concurrentTasksPerWorker: 1,
      runtime: 'worker_threads',
      isolateWorkers: false,
      workerData: { params: { baseUrl: '/unity-mcp/' } },
    });
    // A polluted prototype must not redirect work to another module or export.
    const options = Object.create({ filename: path.join(directory, 'missing.mjs'), name: 'missing' });
    const result = await pool.run({ pathnames: ['/unity-mcp/search/'] }, options);
    assert.deepEqual(result, {
      task: { pathnames: ['/unity-mcp/search/'] },
      params: { baseUrl: '/unity-mcp/' },
      hasWorkerId: true,
    });
  } finally {
    try {
      await pool?.destroy();
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  }
});

test('locked dependency graph excludes the obsolete HTTP cache chain', () => {
  const lock = JSON.parse(readFileSync(path.join(siteDir, 'package-lock.json'), 'utf8'));
  for (const [location, entry] of Object.entries(lock.packages)) {
    for (const name of removed) {
      assert.notEqual(location.split('node_modules/').at(-1), name, location);
      assert.notEqual(entry.name, name, location);
      for (const field of ['dependencies', 'optionalDependencies']) {
        assert.equal(entry[field]?.[name], undefined, `${location}: ${field}.${name}`);
      }
    }
  }
});

test('Docusaurus notifier cannot resolve the obsolete HTTP cache chain', () => {
  const notifierRequire = createRequire(notifierUrl);
  for (const name of removed) {
    assert.throws(() => notifierRequire.resolve(name), { code: 'MODULE_NOT_FOUND' });
  }
});

// Import the dependency resolved by Docusaurus in a fresh, isolated process.
// Intercept detached checks before import so the fixtures cannot spawn them.
function checkNotifier(source, extraEnv = {}) {
  const home = mkdtempSync(path.join(tmpdir(), 'docs-notifier-test-'));
  try {
    const result = spawnSync(process.execPath, ['--input-type=module', '--eval', `
      import assert from 'node:assert/strict';
      import childProcess from 'node:child_process';
      import { writeFileSync } from 'node:fs';
      import http from 'node:http';
      import https from 'node:https';
      import { syncBuiltinESMExports } from 'node:module';
      const spawned = [];
      childProcess.spawn = (...args) => {
        spawned.push(args);
        return { unref() {} };
      };
      const denyNetwork = () => { throw new Error('Unexpected network request'); };
      http.request = http.get = https.request = https.get = denyNetwork;
      syncBuiltinESMExports();
      globalThis.fetch = denyNetwork;
      writeFileSync('.npmrc', 'registry=https://inherited-registry.example.invalid/');
      const { default: updateNotifier } = await import(${JSON.stringify(notifierUrl)});
      const pkg = { name: 'docs-notifier-contract-fixture', version: '1.2.3' };
      const options = { pkg, updateCheckInterval: 3600000 };
      ${source}
    `], {
      cwd: home,
      env: {
        ...(process.env.SystemRoot ? { SystemRoot: process.env.SystemRoot } : {}),
        HOME: home,
        USERPROFILE: home,
        XDG_CONFIG_HOME: home,
        APPDATA: home,
        NODE_ENV: 'development',
        npm_config_registry: 'https://registry.example.invalid/',
        npm_config_userconfig: path.join(home, 'user.npmrc'),
        npm_config_globalconfig: path.join(home, 'global.npmrc'),
        ...extraEnv,
      },
      encoding: 'utf8',
      timeout: 15000,
    });
    assert.ifError(result.error);
    assert.equal(result.status, 0, result.stderr || result.stdout);
  } finally {
    rmSync(home, { recursive: true, force: true });
  }
}

for (const env of [{ CI: 'true' }, { NO_UPDATE_NOTIFIER: '' }]) {
  test(`Docusaurus notifier honors ${Object.keys(env)[0]}`, () => {
    checkNotifier(`
      const notifier = updateNotifier(options);
      notifier.check();
      assert.equal(notifier.config, undefined);
      assert.equal(notifier.update, undefined);
      assert.equal(spawned.length, 0);
    `, env);
  });
}

test('Docusaurus notifier preserves cached updates and uses the installed version', () => {
  checkNotifier(`
    const initial = updateNotifier(options);
    assert.equal(initial.config.get('optOut'), false);
    initial.config.set('update', { current: '0.1.0', latest: '1.3.0', name: pkg.name });
    const notifier = updateNotifier(options);
    assert.equal(notifier.update.current, pkg.version);
    assert.equal(notifier.update.latest, '1.3.0');
    assert.equal(notifier.config.get('update'), undefined);
    notifier.config.set('update', notifier.update);
    notifier.check();
    assert.equal(notifier.update.latest, '1.3.0');
    assert.equal(spawned.length, 0);
  `);
});

test('Docusaurus notifier honors opt-out and its background-check interval', () => {
  checkNotifier(`
    const notifier = updateNotifier(options);
    assert.equal(spawned.length, 0);
    notifier.config.set('lastUpdateCheck', 0);
    notifier.config.set('optOut', true);
    notifier.check();
    assert.equal(spawned.length, 0);
    notifier.config.set('optOut', false);
    notifier.check();
    assert.equal(spawned.length, 1);
    const [executable, args, spawnOptions] = spawned[0];
    assert.equal(executable, process.execPath);
    assert.equal(JSON.parse(args[1]).pkg.name, pkg.name);
    assert.equal(spawnOptions.detached, true);
    assert.equal(spawnOptions.stdio, 'ignore');
  `);
});

test('Docusaurus notifier resolves release tags without the obsolete cache client', () => {
  checkNotifier(`
    let requests = 0;
    globalThis.fetch = async (request) => {
      requests += 1;
      assert.equal(new URL(request.url).hostname, 'registry.example.invalid');
      assert.equal(new URL(request.url).pathname, '/' + pkg.name);
      assert.equal(request.headers.has('authorization'), false);
      return new Response(JSON.stringify({
        name: pkg.name,
        'dist-tags': { latest: '1.3.0', next: '2.0.0-beta.1' },
        versions: { '1.3.0': { version: '1.3.0' }, '2.0.0-beta.1': { version: '2.0.0-beta.1' } },
      }), { headers: { 'content-type': 'application/json' } });
    };
    const stable = await updateNotifier(options).fetchInfo();
    assert.deepEqual(stable, { name: pkg.name, current: pkg.version, latest: '1.3.0', type: 'minor' });
    const next = await updateNotifier({ ...options, distTag: 'next' }).fetchInfo();
    assert.equal(next.latest, '2.0.0-beta.1');
    assert.equal(requests, 2);
    assert.equal(spawned.length, 0);
  `);
});
