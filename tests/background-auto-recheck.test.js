'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const SOURCE_PATH = path.resolve(__dirname, '../extension/background-auto-recheck.js');

function loadModule() {
  const context = vm.createContext({ console, setTimeout, clearTimeout });
  context.globalThis = context;
  vm.runInContext(fs.readFileSync(SOURCE_PATH, 'utf8'), context, {
    filename: 'background-auto-recheck.js',
  });
  return context.BackgroundAutoRecheck;
}

test('recovery heartbeat scans 30 tabs at most once per minute', async () => {
  let now = 1000;
  const sent = [];
  const tabs = new Map(Array.from({ length: 30 }, (_item, index) => [100 + index, {}]));
  const api = loadModule().create({
    chrome: {
      tabs: {
        sendMessage: async (tabId, message) => {
          sent.push({ tabId, message });
          return { ok: true };
        },
      },
    },
    tabs,
    now: () => now,
  });

  assert.equal(api.heartbeat(true), true);
  now += 59_999;
  assert.equal(api.heartbeat(true), false);
  now += 1;
  assert.equal(api.heartbeat(true), true);
  await Promise.resolve();

  assert.equal(sent.length, 60);
  assert.deepEqual(sent.slice(0, 30).map((entry) => entry.tabId), Array.from(tabs.keys()));
  assert.deepEqual(sent.slice(30).map((entry) => entry.tabId), Array.from(tabs.keys()));
});

test('recovery sweep cannot be configured below twenty seconds', async () => {
  let now = 0;
  const sent = [];
  const api = loadModule().create({
    chrome: {
      tabs: {
        sendMessage: async (tabId) => {
          sent.push(tabId);
          return { ok: true };
        },
      },
    },
    tabs: new Map([[101, {}]]),
    now: () => now,
    recoverySweepIntervalMs: 1,
  });

  assert.equal(api.heartbeat(true), true);
  now = 19_999;
  assert.equal(api.heartbeat(true), false);
  now = 20_000;
  assert.equal(api.heartbeat(true), true);
  await Promise.resolve();
  assert.deepEqual(sent, [101, 101]);
});

test('a precise recheck timer stays scoped to the active tab', async () => {
  const sent = [];
  const api = loadModule().create({
    chrome: {
      tabs: {
        sendMessage: async (tabId, message) => {
          sent.push({ tabId, message });
          return { ok: true };
        },
      },
    },
    tabs: new Map([[101, {}], [202, {}]]),
  });

  assert.equal(api.schedule(101, 50), true);
  assert.equal(api.schedule(101, 50), true);
  await new Promise((resolve) => setTimeout(resolve, 80));
  assert.equal(sent.length, 1);
  assert.equal(sent[0].tabId, 101);
  assert.equal(sent[0].message.type, 'auto-recheck');

  assert.equal(api.schedule(202, 50), true);
  api.clear(202);
  await new Promise((resolve) => setTimeout(resolve, 80));
  assert.equal(sent.length, 1);
});
