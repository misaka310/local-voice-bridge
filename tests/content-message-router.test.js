'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const SOURCE = fs.readFileSync(
  path.resolve(__dirname, '../extension/content-message-router.js'),
  'utf8',
);

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

function createHarness(playItem = async () => ({ ok: true })) {
  const marks = [];
  let stopped = 0;
  const context = vm.createContext({ console, Promise });
  context.globalThis = context;
  vm.runInContext(SOURCE, context, { filename: 'content-message-router.js' });
  const router = context.LocalVoiceContentMessageRouter.create({
    conversationTargetStatus: () => ({ ok: true }),
    clearCompletionMarker: () => marks.push('clear'),
    registerCurrentTab: async () => ({}),
    applyOwnerState() {},
    applySettingsSnapshot() {},
    inspectLatestAssistant() {},
    playItem,
    markPlaybackStarted: () => marks.push('playing'),
    markPlaybackCompleted: () => marks.push('complete'),
    markPlaybackError: () => marks.push('error'),
    markPlaybackStopped: () => marks.push('stopped'),
    handleVoiceTranscript: () => false,
    cancelPendingVoiceSend: () => ({ ok: true }),
    audioPlayer: { matches: () => true },
    stopCurrentPlayback: () => { stopped += 1; },
  });
  return { router, marks, stopped: () => stopped };
}

function send(router, message) {
  return router(message, {}, () => {});
}

test('tab activation acknowledges the terminal favicon state', () => {
  const harness = createHarness();

  send(harness.router, { type: 'tab-activated' });

  assert.deepEqual(harness.marks, ['clear']);
});

test('local playback status ignores stale completion tokens', () => {
  const harness = createHarness();

  send(harness.router, { type: 'playback-started', payload: { playbackToken: 'first' } });
  send(harness.router, { type: 'playback-started', payload: { playbackToken: 'second' } });
  send(harness.router, { type: 'playback-completed', payload: { playbackToken: 'first' } });
  send(harness.router, { type: 'playback-completed', payload: { playbackToken: 'second' } });

  assert.deepEqual(harness.marks, ['playing', 'playing', 'complete']);
});

test('play-audio marks only the active token as completed', async () => {
  const first = deferred();
  const second = deferred();
  const calls = [];
  const harness = createHarness((_url, _text, _item, token) => {
    calls.push(token);
    return token === 'first' ? first.promise : second.promise;
  });

  send(harness.router, { type: 'play-audio', payload: { playbackToken: 'first', url: 'a' } });
  send(harness.router, { type: 'play-audio', payload: { playbackToken: 'second', url: 'b' } });
  first.resolve({ ok: false, stopped: true });
  await Promise.resolve();
  assert.deepEqual(harness.marks, ['playing', 'playing']);

  second.resolve({ ok: true, stopped: false });
  await Promise.resolve();
  assert.deepEqual(calls, ['first', 'second']);
  assert.deepEqual(harness.marks, ['playing', 'playing', 'complete']);
});

test('playback failure and explicit stop terminate the active playback indicator', async () => {
  const pending = deferred();
  const harness = createHarness(() => pending.promise);

  send(harness.router, { type: 'play-audio', payload: { playbackToken: 'active', url: 'a' } });
  send(harness.router, { type: 'stop-audio', payload: { playbackToken: 'active' } });
  assert.equal(harness.stopped(), 1);
  assert.deepEqual(harness.marks, ['playing', 'stopped']);

  send(harness.router, { type: 'playback-error', payload: { error: 'generation failed' } });
  assert.deepEqual(harness.marks, ['playing', 'stopped', 'error']);
  pending.resolve({ ok: false, stopped: true });
  await Promise.resolve();
  assert.deepEqual(harness.marks, ['playing', 'stopped', 'error']);
});
