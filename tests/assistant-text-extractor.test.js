'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const textCore = require('../extension/content-text-core.js');
globalThis.ContentTextCore = textCore;
globalThis.LocalVoiceAssistantSourceFilter = require('../extension/assistant-source-filter.js');
const assistantText = require('../extension/assistant-text-extractor.js');

test('normalizes assistant text without flattening meaningful line boundaries', () => {
  assert.equal(assistantText.normalizeText(' 1\r\n2\n\n\n3 '), '1\n2\n\n3');
});

test('recognizes transient ChatGPT generation labels only when they are the whole message', () => {
  assert.equal(assistantText.isTransientAssistantStatus('画像を分析しています…'), true);
  assert.equal(assistantText.isTransientAssistantStatus('Thinking...'), true);
  assert.equal(assistantText.isTransientAssistantStatus('ChatGPT の発言:'), true);
  assert.equal(assistantText.isTransientAssistantStatus('<|SpawnThinking|>'), true);
  assert.equal(assistantText.isTransientAssistantStatus('Thinkingについて説明します。'), false);
  assert.equal(assistantText.isTransientAssistantStatus('ChatGPT の発言: この回答は参考になります。'), false);
});

test('removes internal ChatGPT control tokens before assistant speech extraction', () => {
  const node = (innerText) => ({
    cloneNode: () => ({
      innerText,
      querySelectorAll: () => [],
    }),
  });

  assert.equal(assistantText.extractAssistantText(node('<|SpawnThinking|>')), '');
  assert.equal(
    assistantText.extractAssistantText(node('<|SpawnThinking|>\n最終回答です。')),
    '最終回答です。',
  );
});

test('normalizes markdown lines for speech without owning chunk policy', () => {
  assert.equal(assistantText.normalizeMarkdownLine('## **結論**'), '結論');
  assert.equal(assistantText.normalizeMarkdownLine('- [GitHub](https://github.com/)を確認'), 'GitHubを確認');
});

test('recognizes a bare external host label but preserves descriptive link text', () => {
  const github = { getAttribute: (name) => name === 'href' ? 'https://github.com/example/repo' : '' };
  const googleOne = { getAttribute: (name) => name === 'href' ? 'https://one.google.com/about/plans' : '' };
  assert.equal(assistantText.isBareHostLabel(github, 'GitHub'), true);
  assert.equal(assistantText.isBareHostLabel(github, 'example/repo'), false);
  assert.equal(assistantText.isBareHostLabel(googleOne, 'Google One'), true);
  assert.equal(assistantText.isBareHostLabel(googleOne, 'Google Oneの料金'), false);
  assert.equal(assistantText.isBareHostLabel(googleOne, 'Google Oneを確認'), false);
});

test('assistant node discovery supports current data-conversation-role markup', () => {
  const assistant = { id: 'current-assistant' };
  const seenSelectors = [];
  const documentObject = {
    querySelectorAll(selector) {
      seenSelectors.push(selector);
      if (selector === '[data-conversation-role="assistant"]') return [assistant];
      return [];
    },
  };

  assert.deepEqual(assistantText.getAssistantNodes(documentObject), [assistant]);
  assert.ok(seenSelectors.includes('[data-conversation-role="assistant"]'));
});

test('assistant role heading resolves to its response body sibling instead of the label', () => {
  const responseContainer = { id: 'assistant-response-container' };
  const answerBody = { id: 'assistant-response-body' };
  const label = {
    tagName: 'H4',
    innerText: 'ChatGPT の発言:',
    textContent: 'ChatGPT の発言:',
    parentElement: responseContainer,
    nextElementSibling: answerBody,
  };
  const documentObject = {
    querySelectorAll(selector) {
      if (selector === '[data-conversation-role="assistant"]') return [label];
      return [];
    },
  };

  assert.deepEqual(assistantText.getAssistantNodes(documentObject), [answerBody]);
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
