'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const SOURCE = fs.readFileSync(
  path.resolve(__dirname, '../extension/background-control-heartbeat.js'),
  'utf8',
);

function loadModule() {
  const context = vm.createContext({ console });
  context.globalThis = context;
  vm.runInContext(SOURCE, context, { filename: 'background-control-heartbeat.js' });
  return context.BackgroundControlHeartbeat;
}

test('one global one-minute alarm wakes control sync without per-tab timers', async () => {
  const created = [];
  let listener = null;
  let wakeCount = 0;
  const chrome = {
    alarms: {
      create(name, options) { created.push({ name, options }); },
      get() { return Promise.resolve(null); },
      onAlarm: { addListener(callback) { listener = callback; } },
    },
  };

  const heartbeat = loadModule();
  assert.equal(heartbeat.install(chrome, () => { wakeCount += 1; }), true);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(created.length, 1);
  assert.equal(created[0].name, 'local-voice-control-heartbeat');
  assert.equal(created[0].options.periodInMinutes, 1);

  listener({ name: 'other-alarm' });
  assert.equal(wakeCount, 0);
  listener({ name: 'local-voice-control-heartbeat' });
  assert.equal(wakeCount, 1);
});

test('service-worker wake does not postpone an existing heartbeat alarm', async () => {
  const created = [];
  let listener = null;
  let wakeCount = 0;
  const chrome = {
    alarms: {
      create(name, options) { created.push({ name, options }); },
      get(name) { return Promise.resolve({ name, scheduledTime: 12345 }); },
      onAlarm: { addListener(callback) { listener = callback; } },
    },
  };

  const heartbeat = loadModule();
  assert.equal(heartbeat.install(chrome, () => { wakeCount += 1; }), true);
  listener({ name: 'local-voice-control-heartbeat' });
  await Promise.resolve();
  await Promise.resolve();

  assert.equal(wakeCount, 1);
  assert.equal(created.length, 0);
});

test('heartbeat remains optional in browser test harnesses without alarms', () => {
  const heartbeat = loadModule();
  assert.equal(heartbeat.install({}, () => {}), false);
});
