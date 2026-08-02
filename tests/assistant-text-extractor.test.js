'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const textCore = require('../extension/content-text-core.js');
globalThis.ContentTextCore = textCore;
const assistantText = require('../extension/assistant-text-extractor.js');

test('normalizes assistant text without flattening meaningful line boundaries', () => {
  assert.equal(assistantText.normalizeText(' 1\r\n2\n\n\n3 '), '1\n2\n\n3');
});

test('recognizes transient ChatGPT generation labels only when they are the whole message', () => {
  assert.equal(assistantText.isTransientAssistantStatus('画像を分析しています…'), true);
  assert.equal(assistantText.isTransientAssistantStatus('Thinking...'), true);
  assert.equal(assistantText.isTransientAssistantStatus('Thinkingについて説明します。'), false);
});

test('normalizes markdown lines for speech without owning chunk policy', () => {
  assert.equal(assistantText.normalizeMarkdownLine('## **結論**'), '結論');
  assert.equal(assistantText.normalizeMarkdownLine('- [GitHub](https://github.com/)を確認'), 'GitHubを確認');
});

test('recognizes a bare external host label but preserves descriptive repository text', () => {
  const github = { getAttribute: (name) => name === 'href' ? 'https://github.com/example/repo' : '' };
  const googleOne = { getAttribute: (name) => name === 'href' ? 'https://one.google.com/about/plans' : '' };
  assert.equal(assistantText.isBareHostLabel(github, 'GitHub'), true);
  assert.equal(assistantText.isBareHostLabel(github, 'example/repo'), false);
  assert.equal(assistantText.isBareHostLabel(googleOne, 'Google One'), true);
});

test('stable key prefers message id then turn id and finally a generated node id', () => {
  const byMessage = {
    dataset: { messageId: 'message-1' },
    getAttribute: () => '',
    closest: () => null,
  };
  assert.equal(assistantText.getStableKey(byMessage), 'message-1');

  const byTurn = {
    dataset: {},
    getAttribute: () => '',
    closest: () => ({ getAttribute: () => 'conversation-turn-7' }),
  };
  assert.equal(assistantText.getStableKey(byTurn), 'conversation-turn-7');

  const generated = { dataset: {}, getAttribute: () => '', closest: () => null };
  assert.equal(assistantText.getStableKey(generated, () => 'node-fixed'), 'node-fixed');
  assert.equal(assistantText.getStableKey(generated, () => 'node-other'), 'node-fixed');
});
