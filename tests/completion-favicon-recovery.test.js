'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { create } = require('../extension/background-auto-recheck.js');

test('completion recovery sweep stays enabled when Auto reading is off', async () => {
  const sent = [];
  const api = create({
    chrome: {
      tabs: {
        sendMessage: async (tabId, message) => {
          sent.push({ tabId, message });
          return { ok: true };
        },
      },
    },
    tabs: new Map([[101, {}], [202, {}]]),
    now: () => 1000,
  });

  assert.equal(api.heartbeat(false), true);
  await Promise.resolve();

  assert.deepEqual(sent.map((entry) => entry.tabId), [101, 202]);
  assert.ok(sent.every((entry) => entry.message.type === 'auto-recheck'));
});
