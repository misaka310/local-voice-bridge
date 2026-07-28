'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const textCore = require('../extension/content-text-core.js');

test('coalesces a one-character Japanese DOM fragment into the following line', () => {
  assert.deepEqual(
    textCore.coalesceOrphanLines(['完', '了状態 最初から再点検しました。'], { minChars: 40 }),
    ['完了状態 最初から再点検しました。'],
  );
});

test('preserves a complete short reply when it is the only line', () => {
  assert.deepEqual(textCore.coalesceOrphanLines(['はい。'], { minChars: 40 }), ['はい。']);
});

test('keeps a word boundary when a short Japanese heading is merged', () => {
  assert.deepEqual(
    textCore.coalesceOrphanLines(['結論', '修正が必要です。'], { minChars: 40 }),
    ['結論 修正が必要です。'],
  );
});

test('does not merge a punctuated short sentence with the next line', () => {
  assert.deepEqual(
    textCore.coalesceOrphanLines(['はい。', '次の説明です。'], { minChars: 40 }),
    ['はい。', '次の説明です。'],
  );
});

test('gives an unpunctuated one-character partial a long stability delay', () => {
  assert.equal(textCore.stableDelayForPreview('完', { minChars: 40, stableMs: 1000 }), 5000);
});

test('still schedules valid short complete replies and normal previews', () => {
  assert.equal(textCore.stableDelayForPreview('はい。', { minChars: 40, stableMs: 1000 }), 2200);
  assert.equal(
    textCore.stableDelayForPreview('これは十分な長さを持つ通常の返答プレビューとして扱われる文章です。追加の文字列です。', { minChars: 40, stableMs: 1000 }),
    1000,
  );
});
