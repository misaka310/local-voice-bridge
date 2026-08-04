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

test('runtime persistence cannot block the next queued audio generation', async () => {
  const state = {
    queue: [{
      id: 'q-1',
      text: 'テスト音声です。',
      chunkIndex: 0,
      chunkCount: 1,
      voiceProfile: 'irodori-v3',
      referenceVoice: 'sample',
      voicePrompt: '',
      audioUrl: null,
    }],
    isPlaying: false,
    playbackPhase: 'idle',
    currentItem: null,
    currentToken: null,
    currentPlaybackTabId: null,
    currentPlaybackDeadlineAt: 0,
    playbackWatchdogTimer: null,
    lastPlayedItem: null,
  };
  let speakCalls = 0;
  const context = vm.createContext({
    console,
    crypto: { randomUUID: () => 'token-1' },
    Date,
    setTimeout,
    clearTimeout,
  });
  context.globalThis = context;
  vm.runInContext(SOURCE, context, { filename: 'background-playback-queue.js' });
  const controller = context.BackgroundPlaybackQueue.create({
    getState: () => state,
    patchState: (patch) => Object.assign(state, patch),
    ensureOwner() {},
    uiOwnerTabId: () => 101,
    tabs: new Map([[101, { title: 'Tab A' }]]),
    setStatus() {},
    broadcastState() {},
    flushBrowserRuntimeState: () => new Promise(() => {}),
    runtimePersistMicrotaskBudget: 4,
    replayLocalAudio: async () => ({ ok: true, playedLocally: true, playbackCompleted: true }),
    speak: async () => {
      speakCalls += 1;
      return {
        ok: true,
        playedLocally: true,
        playbackCompleted: true,
        audioUrl: 'http://127.0.0.1/audio/mock.wav',
        referenceVoice: 'sample',
        usedReferenceAudio: 'mock.wav',
        voiceProfile: 'irodori-v3',
      };
    },
    cloneItem: (item) => ({ ...item }),
    statusPayload: () => ({}),
    stopLocalAudio: async () => {},
    chrome: { tabs: { sendMessage: async () => ({ ok: true }) } },
    queueCore: {},
    nextSequence: () => 1,
    defaultVoiceProfile: 'irodori-v3',
    referenceSettingsLoaded: () => true,
    lastKnownReferenceVoice: () => 'sample',
    normalizeReferenceVoice: (value) => value,
    selectedTabId: () => 101,
  });

  void controller.playNext();
  await delay(50);

  assert.equal(speakCalls, 1);
});
