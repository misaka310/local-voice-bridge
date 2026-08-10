'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const SOURCE = fs.readFileSync(
  path.resolve(__dirname, '../extension/content-mic-keepalive.js'),
  'utf8',
);

function createHarness({ micConversationEnabled = true, visibilityState = 'visible' } = {}) {
  const sent = [];
  const intervals = [];
  const cleared = [];
  const settings = { micConversationEnabled };
  const document = { visibilityState };
  let nextTimerId = 1;
  const context = vm.createContext({ console, Promise });
  context.globalThis = context;
  vm.runInContext(SOURCE, context, { filename: 'content-mic-keepalive.js' });
  const keepalive = context.LocalVoiceMicKeepalive.create({
    chrome: {
      runtime: {
        sendMessage(message) {
          sent.push(message);
          return Promise.resolve({ ok: true });
        },
      },
    },
    document,
    getSettings: () => settings,
    setInterval(callback, delay) {
      const id = nextTimerId++;
      intervals.push({ id, callback, delay });
      return id;
    },
    clearInterval(id) {
      cleared.push(id);
    },
  });
  return { keepalive, settings, document, sent, intervals, cleared };
}

test('visible mic conversation keeps the service worker warm with one 20 second heartbeat', () => {
  const harness = createHarness();

  assert.equal(harness.keepalive.sync(), true);
  assert.equal(harness.sent.length, 1);
  assert.equal(harness.sent[0].type, 'mic-control-keepalive');
  assert.equal(harness.intervals.length, 1);
  assert.equal(harness.intervals[0].delay, 20_000);

  harness.intervals[0].callback();
  assert.equal(harness.sent.length, 2);
  assert.equal(harness.sent[1].type, 'mic-control-keepalive');

  assert.equal(harness.keepalive.sync(), true);
  assert.equal(harness.intervals.length, 1);
});

test('keepalive is disabled for hidden tabs and stops immediately when mic mode turns off', () => {
  const harness = createHarness({ visibilityState: 'hidden' });

  assert.equal(harness.keepalive.sync(), false);
  assert.equal(harness.sent.length, 0);
  assert.equal(harness.intervals.length, 0);

  harness.document.visibilityState = 'visible';
  assert.equal(harness.keepalive.sync(), true);
  assert.equal(harness.sent.length, 1);
  assert.equal(harness.intervals.length, 1);

  harness.settings.micConversationEnabled = false;
  assert.equal(harness.keepalive.sync(), false);
  assert.deepEqual(harness.cleared, [harness.intervals[0].id]);

  harness.intervals[0].callback();
  assert.equal(harness.sent.length, 1);
});
