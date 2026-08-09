'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const SOURCE = fs.readFileSync(
  path.resolve(__dirname, '../extension/background-playback-queue.js'),
  'utf8',
);

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function queueItem(overrides = {}) {
  return {
    id: 'q-1',
    tabId: 202,
    text: 'テスト音声です。',
    chunkIndex: 0,
    chunkCount: 1,
    voiceProfile: 'irodori-v3',
    referenceVoice: 'sample',
    voicePrompt: '',
    audioUrl: null,
    ...overrides,
  };
}

function createHarness(options = {}) {
  const state = {
    queue: [...(options.queue || [])],
    isPlaying: false,
    playbackPhase: 'idle',
    currentItem: null,
    currentToken: null,
    currentPlaybackTabId: null,
    currentPlaybackDeadlineAt: 0,
    playbackWatchdogTimer: null,
    lastPlayedItem: options.lastPlayedItem || null,
  };
  const messages = [];
  let speakCalls = 0;
  let replayCalls = 0;
  const context = vm.createContext({
    console,
    crypto: { randomUUID: () => 'token-1' },
    Date,
    setTimeout,
    clearTimeout,
  });
  context.globalThis = context;
  vm.runInContext(SOURCE, context, { filename: 'background-playback-queue.js' });
  const tabs = options.tabs || new Map([[101, { title: 'Tab A' }], [202, { title: 'Tab B' }]]);
  const speakResult = options.speakResult || {
    ok: true,
    playedLocally: true,
    playbackCompleted: true,
    stopped: false,
    audioUrl: 'http://127.0.0.1:8717/audio/mock.wav',
    referenceVoice: 'sample',
    usedReferenceAudio: 'mock.wav',
    voiceProfile: 'irodori-v3',
  };
  const replayResult = options.replayResult || {
    ok: true,
    playedLocally: true,
    playbackCompleted: true,
    stopped: false,
    audioUrl: 'http://127.0.0.1:8717/audio/replay.wav',
  };
  const controller = context.BackgroundPlaybackQueue.create({
    getState: () => state,
    patchState: (patch) => Object.assign(state, patch),
    ensureOwner() {},
    uiOwnerTabId: () => options.uiOwnerTabId || 101,
    tabs,
    setStatus() {},
    broadcastState() {},
    flushBrowserRuntimeState: options.flushBrowserRuntimeState || (async () => {}),
    runtimePersistMicrotaskBudget: 4,
    replayLocalAudio: async () => {
      replayCalls += 1;
      return { ...replayResult };
    },
    speak: async () => {
      speakCalls += 1;
      return { ...speakResult };
    },
    cloneItem: (item) => ({ ...item }),
    statusPayload: () => ({}),
    stopLocalAudio: async () => {},
    chrome: {
      tabs: {
        sendMessage: async (tabId, message) => {
          messages.push({ tabId, message });
          if (options.sendMessage) return options.sendMessage(tabId, message);
          return { ok: true };
        },
      },
    },
    queueCore: { createQueueItem: (base) => ({ ...base }) },
    nextSequence: () => 1,
    defaultVoiceProfile: 'irodori-v3',
    referenceSettingsLoaded: () => true,
    lastKnownReferenceVoice: () => 'sample',
    normalizeReferenceVoice: (value) => value,
    selectedTabId: () => 101,
  });
  return {
    controller,
    messages,
    state,
    speakCalls: () => speakCalls,
    replayCalls: () => replayCalls,
  };
}

function statusTypes(harness, tabId = 202) {
  return harness.messages
    .filter((entry) => entry.tabId === tabId && entry.message.type.startsWith('playback-'))
    .map((entry) => entry.message.type);
}

test('playback waits until the source tab observes the started status', async () => {
  let releaseStarted;
  const started = new Promise((resolve) => { releaseStarted = resolve; });
  const harness = createHarness({
    queue: [queueItem()],
    sendMessage: (_tabId, message) => (message.type === 'playback-started' ? started : { ok: true }),
  });

  const playback = harness.controller.playNext();
  await delay(20);
  assert.equal(harness.speakCalls(), 0);

  releaseStarted({ ok: true });
  await playback;
  assert.equal(harness.speakCalls(), 1);
  assert.deepEqual(statusTypes(harness), ['playback-started', 'playback-completed']);
});

test('terminal playback status retries after transient source-tab delivery failures', async () => {
  let completionAttempts = 0;
  const harness = createHarness({
    queue: [queueItem()],
    sendMessage: (_tabId, message) => {
      if (message.type !== 'playback-completed') return { ok: true };
      completionAttempts += 1;
      if (completionAttempts < 3) throw new Error('receiver temporarily unavailable');
      return { ok: true };
    },
  });

  await harness.controller.playNext();
  await delay(350);

  assert.equal(completionAttempts, 3);
  assert.equal(statusTypes(harness).filter((type) => type === 'playback-completed').length, 3);
});

test('runtime persistence cannot block local playback or source-tab status', async () => {
  const harness = createHarness({
    queue: [queueItem()],
    flushBrowserRuntimeState: () => new Promise(() => {}),
  });

  void harness.controller.playNext();
  await delay(50);

  assert.equal(harness.speakCalls(), 1);
  assert.deepEqual(statusTypes(harness), ['playback-started', 'playback-completed']);
  assert.equal(harness.messages.some((entry) => entry.message.type === 'play-audio'), false);
  assert.equal(harness.state.isPlaying, false);
  assert.equal(harness.state.lastPlayedItem.tabId, 202);
});

test('closing the answer tab does not redirect local playback status to another tab', async () => {
  const harness = createHarness({
    queue: [queueItem({ tabId: 999 })],
    tabs: new Map([[101, { title: 'Tab A' }]]),
  });

  await harness.controller.playNext();

  assert.equal(harness.speakCalls(), 1);
  assert.deepEqual(statusTypes(harness, 101), []);
  assert.equal(harness.messages.some((entry) => entry.message.type === 'play-audio'), false);
  assert.equal(harness.state.lastPlayedItem.tabId, 999);
});

test('Replay stays on the local playback worker and reports status to the answer tab', async () => {
  const harness = createHarness({
    queue: [queueItem({ mode: 'replay', audioUrl: 'http://127.0.0.1:8717/audio/already-generated.wav' })],
  });

  await harness.controller.playNext();

  assert.equal(harness.speakCalls(), 0);
  assert.equal(harness.replayCalls(), 1);
  assert.deepEqual(statusTypes(harness), ['playback-started', 'playback-completed']);
  assert.equal(harness.messages.some((entry) => entry.message.type === 'play-audio'), false);
});

test('browser playback remains only as a fallback while status stays on the answer tab', async () => {
  const harness = createHarness({
    queue: [queueItem()],
    speakResult: {
      ok: true,
      playedLocally: false,
      playbackCompleted: false,
      stopped: false,
      audioUrl: 'http://127.0.0.1:8717/audio/mock.wav',
      referenceVoice: 'sample',
      usedReferenceAudio: 'mock.wav',
      voiceProfile: 'irodori-v3',
    },
  });

  await harness.controller.playNext();

  assert.deepEqual(statusTypes(harness), ['playback-started']);
  const playback = harness.messages.find((entry) => entry.message.type === 'play-audio');
  assert.ok(playback);
  assert.equal(playback.tabId, 101);
  assert.equal(harness.state.currentPlaybackTabId, 101);
});
