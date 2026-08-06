'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');

const { create } = require('../extension/background-state-publisher.js');

test('state publisher sends changes immediately and unchanged state only on heartbeat', async () => {
  let now = 0;
  const posted = [];
  const publisher = create({ now: () => now, heartbeatMs: 30000 });
  const publish = async (state) => posted.push(JSON.parse(JSON.stringify(state)));

  assert.equal(await publisher.publishIfNeeded({ connected: false, state: { tabsCount: 1 }, publish }), true);
  assert.equal(await publisher.publishIfNeeded({ connected: true, state: { tabsCount: 1 }, publish }), false);
  assert.equal(posted.length, 1);

  assert.equal(await publisher.publishIfNeeded({ connected: true, state: { tabsCount: 2 }, publish }), true);
  assert.equal(posted.length, 2);

  now = 29999;
  assert.equal(await publisher.publishIfNeeded({ connected: true, state: { tabsCount: 2 }, publish }), false);
  now = 30000;
  assert.equal(await publisher.publishIfNeeded({ connected: true, state: { tabsCount: 2 }, publish }), true);
  assert.equal(posted.length, 3);
});
