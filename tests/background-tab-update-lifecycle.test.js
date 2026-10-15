'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const SOURCE = fs.readFileSync(
  path.resolve(__dirname, '../extension/background-tab-update-lifecycle.js'),
  'utf8',
);

function loadModule() {
  const context = vm.createContext({ console, URL });
  context.globalThis = context;
  vm.runInContext(SOURCE, context, { filename: 'background-tab-update-lifecycle.js' });
  return context.BackgroundTabUpdateLifecycle;
}

test('managed tab refresh markers survive service-worker recreation within the browser session', async () => {
  const values = new Map();
  const chrome = {
    storage: {
      session: {
        async set(entries) { for (const [key, value] of Object.entries(entries)) values.set(key, value); },
        async get(key) { return { [key]: values.get(key) }; },
      },
    },
  };
  const module = loadModule();
  const first = module.createState(chrome, new Set([707]));
  await first.persist();

  const restored = module.createState(chrome, new Set());
  assert.equal(await restored.has(707), true);
  assert.equal(restored.tabs.has(707), true);
  assert.equal(await restored.has(708), false);
});

test('managed page reload preserves tab state, then reconnects the updated ChatGPT page', async () => {
  const events = [];
  const state = { async has() { return true; } };
  const tabReconnect = {
    forgetTab(tabId) { events.push(['forget', tabId]); },
    async completeManagedRefresh(tabId) { events.push(['complete', tabId]); },
    async reconnectTab(tab) { events.push(['reconnect', tab.id]); },
  };
  const lifecycle = loadModule().createEvents({
    state,
    tabReconnect,
    clearAutoRecheck(tabId) { events.push(['clear', tabId]); },
    removeTab(tabId, reason) { events.push(['remove', tabId, reason]); },
  });

  lifecycle.onUpdated(808, { status: 'loading' }, { id: 808, url: 'https://chatgpt.com/c/808' });
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(events, [['clear', 808]]);

  lifecycle.onUpdated(808, { status: 'complete' }, { id: 808, url: 'https://chatgpt.com/c/808' });
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(events, [['clear', 808], ['complete', 808], ['reconnect', 808]]);
});

test('a page that navigates away during a managed reload is removed from ChatGPT state', async () => {
  const events = [];
  const lifecycle = loadModule().createEvents({
    state: { async has() { return true; } },
    tabReconnect: {
      async completeManagedRefresh(tabId) { events.push(['complete', tabId]); },
    },
    clearAutoRecheck() {},
    removeTab(tabId, reason) { events.push(['remove', tabId, reason]); },
  });

  lifecycle.onUpdated(909, { status: 'complete' }, { id: 909, url: 'https://example.com/' });
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(events, [
    ['complete', 909],
    ['remove', 909, 'Playback tab navigated away; skipped'],
  ]);
});
