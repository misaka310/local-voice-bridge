'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const SOURCE = fs.readFileSync(
  path.resolve(__dirname, '../extension/background-message-router.js'),
  'utf8',
);

function createRouter(overrides = {}) {
  const context = vm.createContext({ console, Date });
  context.globalThis = context;
  vm.runInContext(SOURCE, context, { filename: 'background-message-router.js' });
  return context.BackgroundMessageRouter.create({
    chrome: { tabs: {} },
    tabs: new Map([[101, { title: 'Tab A' }]]),
    scheduleAutoRecheck: () => false,
    ...overrides,
  });
}

test('content tab can schedule one background-owned Auto recheck', () => {
  const calls = [];
  const router = createRouter({
    scheduleAutoRecheck: (tabId, delayMs) => {
      calls.push({ tabId, delayMs });
      return true;
    },
  });
  let response = null;

  const asyncResponse = router(
    { type: 'schedule-auto-recheck', payload: { delayMs: 375 } },
    { tab: { id: 101 } },
    (value) => { response = value; },
  );

  assert.equal(asyncResponse, false);
  assert.deepEqual(calls, [{ tabId: 101, delayMs: 375 }]);
  assert.deepEqual(JSON.parse(JSON.stringify(response)), { ok: true, payload: { scheduled: true } });
});
