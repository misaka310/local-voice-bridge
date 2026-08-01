'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');

const live = require('../extension/live-browser-core.js');

test('conversationKey removes query fragment and trailing slash', () => {
  assert.equal(
    live.conversationKey('https://chatgpt.com/c/abc/?model=x#bottom'),
    'https://chatgpt.com/c/abc',
  );
});

test('assistant baseline and binding ignore preexisting nodes', () => {
  const oldNodes = [{ key: 'a1' }, { key: 'a2' }];
  const baseline = live.assistantBaseline(oldNodes, (node) => node.key);
  const pending = live.resolveAssistantBinding({
    baselineKeys: baseline.keys,
    candidates: oldNodes,
    getKey: (node) => node.key,
  });
  const bound = live.resolveAssistantBinding({
    baselineKeys: baseline.keys,
    candidates: [...oldNodes, { key: 'a3' }],
    getKey: (node) => node.key,
  });
  assert.equal(pending.ok, false);
  assert.equal(pending.reason, 'assistant-binding-pending');
  assert.equal(bound.ok, true);
  assert.equal(bound.key, 'a3');
});

test('assistant binding fails closed when more than one new candidate exists', () => {
  const result = live.resolveAssistantBinding({
    baselineKeys: ['a1'],
    candidates: [{ key: 'a1' }, { key: 'a2' }, { key: 'a3' }],
    getKey: (node) => node.key,
  });
  assert.equal(result.ok, false);
  assert.equal(result.reason, 'assistant-binding-ambiguous');
  assert.equal(result.candidateCount, 2);
});

test('stable sentence chunks exclude an unfinished tail and fenced code', () => {
  const text = [
    '最初の文です。次の文です！まだ途中',
    '```js',
    'console.log("読まない");',
    '```',
  ].join('\n');
  assert.deepEqual(live.splitStableSentences(text, { maxChars: 80 }), [
    '最初の文です。 次の文です！',
  ]);
});

test('final streaming chunk includes a non-punctuated tail', () => {
  assert.deepEqual(
    live.splitStableSentences('完了した文です。最後の短い尾', { isFinal: true, maxChars: 80 }),
    ['完了した文です。', '最後の短い尾'],
  );
});

test('newChunks rejects a rewritten prefix', () => {
  assert.deepEqual(
    live.newChunks(['一文目です。'], ['書き換わりました。']),
    { ok: false, reason: 'stream-prefix-changed', chunks: [] },
  );
  assert.deepEqual(
    live.newChunks(['一文目です。'], ['一文目です。', '二文目です。']),
    { ok: true, chunks: ['二文目です。'], allChunks: ['一文目です。', '二文目です。'] },
  );
});

test('boundedRetry retries 429-like results and stops when turn changes', async () => {
  const sleeps = [];
  let calls = 0;
  const result = await live.boundedRetry(async () => {
    calls += 1;
    return calls < 3 ? { ok: false, retry: true, retryAfterMs: 125 } : { ok: true };
  }, {
    maxAttempts: 4,
    sleep: async (milliseconds) => sleeps.push(milliseconds),
    isCurrent: () => true,
  });
  assert.equal(result.ok, true);
  assert.equal(result.attempts, 3);
  assert.deepEqual(sleeps, [125, 125]);

  let current = true;
  const cancelled = await live.boundedRetry(async () => {
    current = false;
    return { ok: false, retry: true, retryAfterMs: 25 };
  }, {
    sleep: async () => {},
    isCurrent: () => current,
  });
  assert.equal(cancelled.cancelled, true);
});
