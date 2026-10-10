'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const SOURCE = fs.readFileSync(
  path.resolve(__dirname, '../extension/background-tab-reconnect.js'),
  'utf8',
);
const MANIFEST = JSON.parse(fs.readFileSync(
  path.resolve(__dirname, '../extension/manifest.json'),
  'utf8',
));

function loadModule() {
  const context = vm.createContext({ console, setTimeout, clearTimeout, URL });
  context.globalThis = context;
  vm.runInContext(SOURCE, context, { filename: 'background-tab-reconnect.js' });
  return context.BackgroundTabReconnect;
}

test('one hung ChatGPT tab cannot block reconnecting the remaining tabs', async () => {
  const sent = [];
  let broadcasts = 0;
  const tabs = new Map([[999, { id: 999 }]]);
  const chrome = {
    tabs: {
      async query() { return [{ id: 101 }, { id: 202 }]; },
      sendMessage(tabId, message) {
        sent.push({ tabId, message });
        if (tabId === 101) return new Promise(() => {});
        return Promise.resolve({ ok: true, contentScriptVersion: '0.4.6' });
      },
    },
    scripting: { async executeScript() { throw new Error('must not inject after timeout'); } },
  };
  const reconnect = loadModule().create({
    chrome,
    tabs,
    reconnectingTabs: new Map(),
    tabPatterns: ['https://chatgpt.com/*'],
    contentScriptVersion: '0.4.6',
    ensureOwner() {},
    broadcastState() { broadcasts += 1; },
    timeoutMs: 20,
  });

  const startedAt = Date.now();
  assert.equal(await reconnect.reconnectOpenTabs(), true);
  assert.ok(Date.now() - startedAt < 250);
  assert.equal(broadcasts, 1);
  assert.equal(tabs.has(999), false);
  assert.deepEqual(sent.map((entry) => entry.tabId).sort((a, b) => a - b), [101, 202]);
});

test('a missing receiver is injected once and then reconnected', async () => {
  const sent = [];
  const injected = [];
  let first = true;
  const chrome = {
    tabs: {
      async query() { return [{ id: 303 }]; },
      async sendMessage(tabId, message) {
        sent.push({ tabId, message });
        if (first) {
          first = false;
          throw new Error('Receiving end does not exist.');
        }
        return { ok: true, contentScriptVersion: '0.4.6' };
      },
    },
    scripting: {
      async executeScript(details) { injected.push(details); },
    },
  };
  const reconnect = loadModule().create({
    chrome,
    tabs: new Map(),
    reconnectingTabs: new Map(),
    tabPatterns: ['https://chatgpt.com/*'],
    contentScriptVersion: '0.4.6',
    ensureOwner() {},
    broadcastState() {},
    timeoutMs: 20,
  });

  assert.equal(await reconnect.reconnectOpenTabs(), true);
  assert.equal(injected.length, 1);
  assert.deepEqual(JSON.parse(JSON.stringify(injected[0])), {
    target: { tabId: 303 },
    files: Array.from(loadModule().CONTENT_SCRIPT_FILES),
  });
  assert.equal(sent.length, 2);
});

test('reconnect injection matches the manifest content-script order', () => {
  assert.deepEqual(
    Array.from(loadModule().CONTENT_SCRIPT_FILES),
    MANIFEST.content_scripts[0].js,
  );
});

test('reconnect injection loads the mutation filter before the DOM observer', () => {
  const files = Array.from(loadModule().CONTENT_SCRIPT_FILES);
  const filterIndex = files.indexOf('content-mutation-filter.js');
  const observerIndex = files.indexOf('content-dom-observer.js');

  assert.ok(filterIndex >= 0);
  assert.ok(observerIndex >= 0);
  assert.ok(filterIndex < observerIndex);
});

test('an old content script gets an update watcher instead of a duplicate full injection', async () => {
  const injected = [];
  const sent = [];
  const reconnect = loadModule().create({
    chrome: {
      tabs: {
        async query() { return [{ id: 404, url: 'https://chatgpt.com/c/404', active: false }]; },
        async sendMessage(tabId, message) {
          sent.push({ tabId, message });
          if (message.type === 'bridge-reconnect') {
            return { ok: true, contentScriptVersion: '0.4.5' };
          }
          if (message.type === 'bridge-watch-for-update') return { ok: true, watching: true };
          return { ok: true };
        },
      },
      scripting: {
        async executeScript(details) { injected.push(details); },
      },
    },
    tabs: new Map(),
    reconnectingTabs: new Map(),
    pendingContentUpdateTabs: new Set(),
    tabPatterns: ['https://chatgpt.com/*'],
    contentScriptVersion: '0.4.6',
    ensureOwner() {},
    broadcastState() {},
  });

  assert.equal(await reconnect.reconnectOpenTabs(), true);
  assert.equal(reconnect.pendingContentUpdateCount(), 1);
  assert.deepEqual(JSON.parse(JSON.stringify(injected)), [{
    target: { tabId: 404 },
    files: ['background-tab-update-watcher.js'],
  }]);
  assert.ok(sent.some((entry) => entry.message.type === 'bridge-watch-for-update'));
});

test('safe background page refresh requires an idle tab and current watcher version', async () => {
  const reloads = [];
  const managed = new Set();
  const reconnect = loadModule().create({
    chrome: {
      tabs: {
        async get(tabId) { return { id: tabId, url: 'https://chatgpt.com/c/505', active: false }; },
        async reload(tabId) { reloads.push(tabId); },
      },
      scripting: {
        async executeScript() { return [{ frameId: 0, result: true }]; },
      },
    },
    tabs: new Map(),
    reconnectingTabs: new Map(),
    managedRefreshTabs: managed,
    tabPatterns: ['https://chatgpt.com/*'],
    contentScriptVersion: '0.4.6',
    ensureOwner() {},
    broadcastState() {},
  });

  const result = await reconnect.handlePageReadyForUpdate(
    { id: 505, url: 'https://chatgpt.com/c/505' },
    '0.4.6',
  );
  assert.deepEqual(JSON.parse(JSON.stringify(result)), { ok: true, reloading: true, retry: false });
  assert.deepEqual(reloads, [505]);
  assert.equal(managed.has(505), true);
});

test('active, playing, or stale-version tabs are never refreshed', async () => {
  const reloads = [];
  const probes = [];
  let active = true;
  let playing = false;
  const reconnect = loadModule().create({
    chrome: {
      tabs: {
        async get(tabId) { return { id: tabId, url: 'https://chatgpt.com/c/606', active }; },
        async reload(tabId) { reloads.push(tabId); },
      },
      scripting: {
        async executeScript() { probes.push(true); return [{ frameId: 0, result: true }]; },
      },
    },
    tabs: new Map(),
    reconnectingTabs: new Map(),
    tabPatterns: ['https://chatgpt.com/*'],
    contentScriptVersion: '0.4.6',
    isTabPlaying: () => playing,
    ensureOwner() {},
    broadcastState() {},
  });

  assert.deepEqual(JSON.parse(JSON.stringify(await reconnect.handlePageReadyForUpdate(
    { id: 606, url: 'https://chatgpt.com/c/606' }, '0.4.6',
  ))), { ok: false, reloading: false, retry: false });
  active = false;
  playing = true;
  assert.deepEqual(JSON.parse(JSON.stringify(await reconnect.handlePageReadyForUpdate(
    { id: 606, url: 'https://chatgpt.com/c/606' }, '0.4.6',
  ))), { ok: false, reloading: false, retry: false });
  playing = false;
  assert.deepEqual(JSON.parse(JSON.stringify(await reconnect.handlePageReadyForUpdate(
    { id: 606, url: 'https://chatgpt.com/c/606' }, '0.4.5',
  ))), { ok: false, reloading: false, retry: false });
  assert.equal(probes.length, 0);
  assert.deepEqual(reloads, []);
});

test('a tab that becomes active while refresh state is persisted is not reloaded', async () => {
  const reloads = [];
  const managed = new Set();
  let active = false;
  const reconnect = loadModule().create({
    chrome: {
      tabs: {
        async get(tabId) { return { id: tabId, url: 'https://chatgpt.com/c/707', active }; },
        async reload(tabId) { reloads.push(tabId); },
      },
      scripting: {
        async executeScript() { return [{ frameId: 0, result: true }]; },
      },
    },
    tabs: new Map(),
    reconnectingTabs: new Map(),
    managedRefreshTabs: managed,
    contentScriptVersion: '0.4.6',
    async persistManagedRefreshTabs() { active = true; },
    ensureOwner() {},
    broadcastState() {},
  });

  assert.deepEqual(JSON.parse(JSON.stringify(await reconnect.handlePageReadyForUpdate(
    { id: 707, url: 'https://chatgpt.com/c/707' }, '0.4.6',
  ))), { ok: false, reloading: false, retry: false });
  assert.deepEqual(reloads, []);
  assert.equal(managed.has(707), false);
});

test('a tab queued for playback while refresh state is persisted is not reloaded', async () => {
  const reloads = [];
  const managed = new Set();
  let pendingPlayback = false;
  const reconnect = loadModule().create({
    chrome: {
      tabs: {
        async get(tabId) { return { id: tabId, url: 'https://chatgpt.com/c/708', active: false }; },
        async reload(tabId) { reloads.push(tabId); },
      },
      scripting: {
        async executeScript() { return [{ frameId: 0, result: true }]; },
      },
    },
    tabs: new Map(),
    reconnectingTabs: new Map(),
    managedRefreshTabs: managed,
    contentScriptVersion: '0.4.6',
    async persistManagedRefreshTabs() { pendingPlayback = true; },
    hasPendingPlayback: () => pendingPlayback,
    ensureOwner() {},
    broadcastState() {},
  });

  assert.deepEqual(JSON.parse(JSON.stringify(await reconnect.handlePageReadyForUpdate(
    { id: 708, url: 'https://chatgpt.com/c/708' }, '0.4.6',
  ))), { ok: false, reloading: false, retry: false });
  assert.deepEqual(reloads, []);
  assert.equal(managed.has(708), false);
});
