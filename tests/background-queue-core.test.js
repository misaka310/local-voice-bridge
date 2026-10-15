'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const queueCore = require('../extension/background-queue-core.js');

test('Auto is blocked only during microphone and Live ownership phases', () => {
  for (const phase of ['recording', 'preparing_model', 'transcribing', 'pending_send', 'arming', 'armed', 'sending', 'waiting_response', 'committed', 'responding', 'speaking']) {
    assert.equal(queueCore.shouldQueueAuto(phase), false, phase);
  }
  for (const phase of ['', 'off', 'idle', 'error']) {
    assert.equal(queueCore.shouldQueueAuto(phase), true, phase);
  }
});

test('streaming updates preserve the already-read Auto boundary', () => {
  const previous = { messageKey: 'm1', chunks: ['冒頭プレビュー', '古い続き'] };
  assert.deepEqual(
    queueCore.preserveReadChunkBoundary(previous, ['冒頭プレビュー 新しい続き', '最終部分'], 0),
    ['冒頭プレビュー', '新しい続き', '最終部分'],
  );
  assert.deepEqual(
    queueCore.preserveReadChunkBoundary(previous, ['書き直された冒頭', '最終部分'], 0),
    ['書き直された冒頭', '最終部分'],
  );
});

test('queue item creation freezes the reference selected at enqueue time', () => {
  const item = queueCore.createQueueItem({
    mode: 'auto',
    tabId: 7,
    text: '本文',
  }, {
    createId: () => 'q-fixed',
    defaultVoiceProfile: 'irodori-v3',
    referenceSettingsLoaded: true,
    lastKnownReferenceVoice: 'suguha',
    normalizeReferenceVoice: (value) => String(value || '').trim(),
  });
  assert.deepEqual(item, {
    id: 'q-fixed',
    mode: 'auto',
    reason: 'manual',
    tabId: 7,
    tabTitle: 'ChatGPT',
    messageKey: '',
    chunkIndex: 0,
    chunkCount: 0,
    text: '本文',
    voiceProfile: 'irodori-v3',
    referenceVoice: 'suguha',
    voicePrompt: '',
    audioUrl: null,
  });
});

test('Next and Regen planning is pure and keeps the documented read boundary', () => {
  const tabs = new Map([[4, {
    title: 'Tab 4',
    lastReadIndex: 0,
    lastAssistantMessage: {
      messageKey: 'm4',
      chunks: ['first', 'second', 'third'],
    },
  }]]);
  const next = queueCore.planManualCommand({ command: 'next', senderTabId: 4, tabs, selectedTabId: 4 });
  assert.equal(next.ok, true);
  assert.equal(next.lastReadIndex, 1);
  assert.equal(next.enqueueBase.text, 'second');
  assert.equal(tabs.get(4).lastReadIndex, 0);

  const regen = queueCore.planManualCommand({ command: 'regen', senderTabId: 4, tabs, selectedTabId: 4 });
  assert.equal(regen.ok, true);
  assert.equal(regen.lastReadIndex, 0);
  assert.equal(regen.enqueueBase.text, 'first');
});

test('assistant reports update latest chunks, completion evidence, dedupe Auto, and suppress during Live', () => {
  const initial = {
    title: 'Tab',
    lastReadIndex: -1,
    lastAutoQueueSignature: '',
    lastAssistantMessage: null,
  };
  const first = queueCore.applyAssistantReport(initial, {
    messageKey: 'm1',
    chunks: ['preview', 'rest'],
    autoPreview: 'preview',
    completionReason: 'action-control',
    completionObservedAt: 7,
    isAuto: true,
  }, { tabId: 9, allowAuto: true, capturedAt: 10 });
  assert.equal(first.changed, true);
  assert.equal(first.enqueueBase.text, 'preview');
  assert.equal(first.info.lastReadIndex, 0);
  assert.equal(first.info.lastAssistantMessage.capturedAt, 10);
  assert.equal(first.info.lastAssistantMessage.completionReason, 'action-control');
  assert.equal(first.info.lastAssistantMessage.completionObservedAt, 7);

  const duplicate = queueCore.applyAssistantReport(first.info, {
    messageKey: 'm1',
    chunks: ['preview', 'rest'],
    autoPreview: 'preview',
    completionReason: 'action-control',
    completionObservedAt: 7,
    isAuto: true,
  }, { tabId: 9, allowAuto: true, capturedAt: 11 });
  assert.equal(duplicate.enqueueBase, null);
  assert.equal(duplicate.suppressedAuto, false);

  const reconnectSnapshot = queueCore.applyAssistantReport(first.info, {
    messageKey: 'm1',
    chunks: ['preview', 'rest'],
    autoPreview: 'preview',
    isAuto: false,
  }, { tabId: 9, allowAuto: true, capturedAt: 12 });
  assert.equal(reconnectSnapshot.info.lastAssistantMessage.completionReason, 'action-control');
  assert.equal(reconnectSnapshot.info.lastAssistantMessage.completionObservedAt, 7);

  const blocked = queueCore.applyAssistantReport(initial, {
    messageKey: 'm2',
    chunks: ['blocked'],
    autoPreview: 'blocked',
    completionReason: 'action-control',
    completionObservedAt: 8,
    isAuto: true,
  }, { tabId: 9, allowAuto: false, capturedAt: 12 });
  assert.equal(blocked.enqueueBase, null);
  assert.equal(blocked.suppressedAuto, true);
});
