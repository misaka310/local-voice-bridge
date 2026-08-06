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
  const context = vm.createContext({ console, setTimeout });
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
        return Promise.resolve({ ok: true });
      },
    },
    scripting: { async executeScript() { throw new Error('must not inject after timeout'); } },
  };
  const reconnect = loadModule().create({
    chrome,
    tabs,
    reconnectingTabs: new Map(),
    tabPatterns: ['https://chatgpt.com/*'],
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
        return { ok: true };
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
