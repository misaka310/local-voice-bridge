'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const runtimeCore = require('../extension/background-runtime-core.js');

test('runtime payload serializes tabs sessions and queue without sharing item objects', () => {
  const queueItem = { id: 'q-1', text: 'hello', tabId: 1 };
  const payload = runtimeCore.createPayload({
    tabs: new Map([[1, {
      title: 'Tab A',
      url: 'https://chatgpt.com/c/1',
      lastReadIndex: 0,
      lastAutoQueueSignature: 'sig',
      lastAssistantMessage: {
        messageKey: 'm1',
        chunks: ['a'],
        completionReason: 'action-control',
        completionObservedAt: 1,
        capturedAt: 1,
      },
    }]]),
    selectedTabId: 1,
    uiOwnerTabId: 1,
    lastComposerFocusedTabId: 1,
    activeConversationTargetTabId: 1,
    conversationSessionTargets: new Map([[7, 1]]),
    conversationSessionTargetLocations: new Map([[7, 'https://chatgpt.com/c/1']]),
    queue: [queueItem],
    currentItem: null,
    lastPlayedItem: queueItem,
    seq: 2,
  });

  assert.equal(payload.tabs[0].lastAssistantMessage.messageKey, 'm1');
  assert.equal(payload.tabs[0].lastAssistantMessage.completionReason, 'action-control');
  assert.deepEqual(payload.conversationSessions, [{
    sessionId: 7,
    tabId: 1,
    location: 'https://chatgpt.com/c/1',
  }]);
  assert.notEqual(payload.queue[0], queueItem);
  assert.equal(payload.seq, 2);
});

test('runtime restore keeps live state authoritative and deduplicates persisted queue', () => {
  const duplicate = { id: 'q-1', mode: 'auto', tabId: 1, messageKey: 'm1', chunkIndex: 0, text: 'hello' };
  const merged = runtimeCore.mergeSnapshot({
    tabs: [{ id: 1, title: 'Persisted', url: 'https://chatgpt.com/c/1' }],
    selectedTabId: 1,
    currentItem: { id: 'q-current', mode: 'next', tabId: 1, messageKey: 'm2', chunkIndex: 1, text: 'current' },
    queue: [duplicate],
    lastPlayedItem: { id: 'old', text: 'old' },
    seq: 4,
  }, {
    tabs: new Map([[1, { title: 'Live', url: 'https://chatgpt.com/c/1' }]]),
    queue: [{ ...duplicate }, { id: 'q-live', mode: 'next', tabId: 1, messageKey: 'm3', chunkIndex: 0, text: 'live' }],
    currentItem: null,
    isPlaying: false,
    lastPlayedItem: { id: 'live-last', text: 'live last' },
    seq: 6,
    conversationSessionTargets: new Map(),
    conversationSessionTargetLocations: new Map(),
  });

  assert.equal(merged.tabs.get(1).title, 'Live');
  assert.deepEqual(merged.queue.map((item) => item.id), ['q-current', 'q-1', 'q-live']);
  assert.equal(merged.resetPlayback, true);
  assert.equal(merged.lastPlayedItem.id, 'live-last');
  assert.equal(merged.seq, 6);
});

test('runtime restore normalizes persisted messages and merges conversation sessions', () => {
  const merged = runtimeCore.mergeSnapshot({
    tabs: [
      { id: 0, title: 'invalid' },
      {
        id: 2,
        title: 'Persisted',
        url: 'https://chatgpt.com/c/2',
        lastReadIndex: 3,
        lastAutoQueueSignature: 'sig-2',
        lastAssistantMessage: {
          messageKey: 'm2',
          chunks: [' first ', '', 'second'],
          completionReason: 'generation-ended-with-action-control',
          completionObservedAt: 3,
          capturedAt: 4,
        },
      },
    ],
    conversationSessions: [
      { sessionId: 0, tabId: 2, location: 'invalid' },
      { sessionId: 8, tabId: 2, location: 'https://chatgpt.com/c/2' },
    ],
  }, {
    tabs: new Map(),
    queue: [],
    currentItem: null,
    isPlaying: false,
    lastPlayedItem: null,
    seq: 1,
    conversationSessionTargets: new Map([[9, 3]]),
    conversationSessionTargetLocations: new Map([[9, 'https://chatgpt.com/c/3']]),
  });

  assert.deepEqual(merged.tabs.get(2).lastAssistantMessage.chunks, ['first', 'second']);
  assert.equal(merged.tabs.get(2).lastAssistantMessage.completionReason, 'generation-ended-with-action-control');
  assert.equal(merged.conversationSessionTargets.get(8), 2);
  assert.equal(merged.conversationSessionTargets.get(9), 3);
  assert.equal(merged.conversationSessionTargetLocations.get(9), 'https://chatgpt.com/c/3');
});

test('runtime restore never requeues persisted current item over active playback', () => {
  const active = { id: 'active', text: 'active' };
  const merged = runtimeCore.mergeSnapshot({
    currentItem: { id: 'persisted-current', text: 'stale' },
    queue: [],
  }, {
    tabs: new Map(),
    queue: [],
    currentItem: active,
    isPlaying: true,
    lastPlayedItem: null,
    seq: 1,
    conversationSessionTargets: new Map(),
    conversationSessionTargetLocations: new Map(),
  });

  assert.equal(merged.resetPlayback, false);
  assert.deepEqual(merged.queue, []);
});
