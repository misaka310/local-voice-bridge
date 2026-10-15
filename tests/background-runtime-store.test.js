'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const SOURCE = fs.readFileSync(
  path.resolve(__dirname, '../extension/background-runtime-store.js'),
  'utf8',
);

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function createStore(controlPanelRequest, snapshot) {
  const context = vm.createContext({ setTimeout, clearTimeout });
  context.globalThis = context;
  vm.runInContext(SOURCE, context, { filename: 'background-runtime-store.js' });
  return context.BackgroundRuntimeStore.create({
    runtimeCore: {
      cloneItem: (item) => item,
      createPayload: (value) => ({ ...value }),
      mergeSnapshot: (_value, current) => current,
    },
    snapshot,
    applyMerged() {},
    ensureOwner() {},
    getSettings: async () => ({}),
    controlPanelRequest,
    queueLength: () => 0,
    isPlaying: () => false,
    playNext() {},
    stopLocalAudio: async () => {},
  });
}

test('each flush resolves after its own serialized snapshot write', async () => {
  const firstWrite = deferred();
  const secondWrite = deferred();
  const writes = [];
  let revision = 0;
  const store = createStore(async (_settings, pathname, options) => {
    assert.equal(pathname, '/v1/browser-runtime');
    writes.push(options.body.revision);
    if (writes.length === 1) await firstWrite.promise;
    if (writes.length === 2) await secondWrite.promise;
    return { ok: true };
  }, () => ({ revision }));
  store.applyBrowserRuntimeSnapshot({});

  revision = 1;
  const first = store.flushBrowserRuntimeState();
  while (writes.length < 1) await delay(1);

  revision = 2;
  const second = store.flushBrowserRuntimeState();
  firstWrite.resolve();

  try {
    assert.equal(
      await Promise.race([first.then(() => 'resolved'), delay(100).then(() => 'timed-out')]),
      'resolved',
    );
    while (writes.length < 2) await delay(1);
    assert.deepEqual([...writes], [1, 2]);
  } finally {
    secondWrite.resolve();
    await Promise.allSettled([first, second]);
  }
});
