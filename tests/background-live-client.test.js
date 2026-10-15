'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');

const { create } = require('../extension/background-live-client.js');

function response(status, payload) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async json() { return payload; },
  };
}

test('submission request adds authoritative sender tab id and action', async () => {
  const calls = [];
  const client = create({
    fetch: async (url, options) => {
      calls.push({ url, options });
      return response(200, { ok: true, sendAllowed: true, submission: { phase: 'armed' } });
    },
    getSettings: async () => ({ healthUrl: 'http://127.0.0.1:8717/health' }),
    buildUrl: (_settings, path) => `http://127.0.0.1:8717${path}`,
  });

  const result = await client.handle({
    type: 'live-submission',
    action: 'arm',
    payload: { tabId: 999, submissionId: 'submission-1' },
  }, 7, true);

  assert.equal(result.response.ok, true);
  const body = JSON.parse(calls[0].options.body);
  assert.equal(body.tabId, 7);
  assert.equal(body.action, 'arm');
  assert.equal(calls[0].url, 'http://127.0.0.1:8717/v1/conversation/submission');
});

test('live chunk 429 is returned as bounded retry metadata', async () => {
  const client = create({
    fetch: async () => response(429, { ok: false, error: 'full', retryAfterMs: 175 }),
    getSettings: async () => ({}),
    buildUrl: (_settings, path) => path,
  });

  const result = await client.handle({ type: 'live-chunk', payload: {} }, 7, true);

  assert.equal(result.response.ok, true);
  assert.equal(result.response.payload.retry, true);
  assert.equal(result.response.payload.retryAfterMs, 175);
});

test('unregistered content scripts cannot arm or enqueue chunks', async () => {
  let fetched = false;
  const client = create({
    fetch: async () => { fetched = true; return response(200, { ok: true }); },
    getSettings: async () => ({}),
    buildUrl: (_settings, path) => path,
  });

  const arm = await client.handle({ type: 'live-submission', payload: {} }, 0, false);
  const chunk = await client.handle({ type: 'live-chunk', payload: {} }, 8, false);

  assert.equal(arm.response.error, 'tab-not-registered');
  assert.equal(chunk.response.error, 'tab-not-registered');
  assert.equal(fetched, false);
});

test('state uses GET and conflict remains a hard failure', async () => {
  const calls = [];
  const replies = [
    response(200, { ok: true, submission: { phase: 'idle' } }),
    response(409, { ok: false, error: 'stale' }),
  ];
  const client = create({
    fetch: async (url, options) => {
      calls.push({ url, options });
      return replies.shift();
    },
    getSettings: async () => ({}),
    buildUrl: (_settings, path) => path,
  });

  const state = await client.handle({ type: 'live-state' }, null, false);
  const conflict = await client.handle({ type: 'live-interrupt', payload: {} }, 7, true);

  assert.equal(calls[0].options.method, 'GET');
  assert.equal(state.response.ok, true);
  assert.equal(conflict.response.ok, false);
  assert.equal(conflict.response.status, 409);
});
