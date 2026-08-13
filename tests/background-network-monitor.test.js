'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const SOURCE = fs.readFileSync(
  path.resolve(__dirname, '../extension/background-network-monitor.js'),
  'utf8',
);

function loadModule() {
  const context = vm.createContext({ URL, Date, Promise, console });
  context.globalThis = context;
  vm.runInContext(SOURCE, context, { filename: 'background-network-monitor.js' });
  return context.BackgroundNetworkMonitor;
}

test('sanitizes ChatGPT network details without query strings or fragments', () => {
  const monitor = loadModule();
  const event = monitor.sanitizeDetails({
    url: 'https://chatgpt.com/backend-api/conversations?offset=20#private',
    method: 'get',
    statusCode: 429,
    type: 'xmlhttprequest',
    tabId: 7,
    timeStamp: Date.parse('2026-08-13T06:00:00Z'),
  });

  assert.equal(event.host, 'chatgpt.com');
  assert.equal(event.path, '/backend-api/conversations');
  assert.equal(event.statusCode, 429);
  assert.equal(event.method, 'GET');
  assert.equal(event.tabId, 7);
  assert.equal(event.synthetic, false);
});

test('persists errors and conversation paths but ignores unrelated successful requests', () => {
  const monitor = loadModule();
  assert.equal(monitor.shouldPersist({ statusCode: 429, path: '/backend-api/foo' }), true);
  assert.equal(monitor.shouldPersist({ statusCode: 200, path: '/backend-api/conversations' }), true);
  assert.equal(monitor.shouldPersist({ statusCode: 200, path: '/backend-api/foo' }), false);
});

test('listener posts only matching sanitized events', async () => {
  const monitor = loadModule();
  let listener = null;
  let filter = null;
  const posted = [];
  const instance = monitor.create({
    webRequest: {
      onCompleted: {
        addListener(callback, receivedFilter) {
          listener = callback;
          filter = receivedFilter;
        },
      },
    },
    postEvent: async (event) => posted.push(event),
  });

  assert.equal(instance.start(), true);
  assert.deepEqual(Array.from(filter.urls), ['https://chatgpt.com/*', 'https://chat.openai.com/*']);

  listener({
    url: 'https://chatgpt.com/backend-api/conversation/abc?sample=redacted',
    method: 'POST',
    statusCode: 200,
    type: 'xmlhttprequest',
    tabId: 3,
    timeStamp: Date.now(),
  });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(posted.length, 1);
  assert.equal(posted[0].path, '/backend-api/conversation/abc');
  assert.equal(JSON.stringify(posted[0]).includes('redacted'), false);
});
