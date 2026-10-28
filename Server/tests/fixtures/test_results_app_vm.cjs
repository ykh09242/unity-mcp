// Execute the shipped App script against an isolated, deterministic host/DOM seam.
'use strict';
const fs = require('node:fs');
const vm = require('node:vm');
const { webcrypto } = require('node:crypto');
const fixture = JSON.parse(fs.readFileSync(0, 'utf8'));
const html = fs.readFileSync(process.argv[2], 'utf8');
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];
if (scripts.length !== 1) throw new Error('Expected one packaged App script');
const elements = new Map();
function element() {
  return {
    hidden: true, value: '', textContent: '', children: [], listeners: new Map(),
    append(...children) { this.children.push(...children); },
    replaceChildren(...children) { this.children = children; },
    addEventListener(name, handler) { this.listeners.set(name, handler); },
    removeEventListener(name) { this.listeners.delete(name); }
  };
}
for (const name of ['view', 'filter', 'results', 'summary', 'limit', 'matches', 'status', 'main']) {
  elements.set(name, element());
}
const outbound = [];
const listeners = new Map();
const timers = new Map();
let timerId = 0;
let disconnected = false;
const host = { postMessage(message) { outbound.push(message); } };
const window = {
  parent: host,
  addEventListener(name, handler) { listeners.set(name, handler); },
  removeEventListener(name) { listeners.delete(name); }
};
const context = vm.createContext({
  window, crypto: webcrypto,
  document: {
    getElementById(name) { return elements.get(name); },
    createElement: element,
    querySelector(name) { return elements.get(name); },
    documentElement: { scrollHeight: 400, clientWidth: 600 }
  },
  ResizeObserver: class {
    observe() {}
    disconnect() { disconnected = true; }
  },
  setTimeout(callback) { const key = ++timerId; timers.set(key, callback); return key; },
  clearTimeout(key) { timers.delete(key); }
});
vm.runInContext(scripts[0][1], context, { filename: process.argv[2], timeout: 1000 });
const deliver = message => listeners.get('message')?.({ source: host, data: { jsonrpc: '2.0', ...message } });
const initialization = outbound.find(message => message.method === 'ui/initialize');
if (!initialization) throw new Error('App did not initialize');
deliver({ id: initialization.id, result: {
  protocolVersion: '2026-01-26', hostInfo: { name: 'Offline fixture', version: '1' },
  hostCapabilities: {}, hostContext: { displayMode: 'inline' }
} });
function snapshot() {
  return {
    visible: !elements.get('view').hidden,
    rowCount: elements.get('results').children.length,
    summaryCount: elements.get('summary').children.length,
    listening: listeners.has('message'),
    filterListening: elements.get('filter').listeners.has('input'),
    timerCount: timers.size,
    disconnected
  };
}
const states = [];
for (const packet of fixture.packets) {
  deliver(packet);
  states.push(snapshot());
}
process.stdout.write(JSON.stringify({ states, outbound }));
