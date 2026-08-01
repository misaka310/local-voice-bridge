'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const ROOT = path.resolve(__dirname, '..');
const BACKGROUND_PATH = path.join(ROOT, 'extension', 'background.js');
const BACKGROUND_CORE_PATH = path.join(ROOT, 'extension', 'background-core.js');
const BACKGROUND_SETTINGS_CORE_PATH = path.join(ROOT, 'extension', 'background-settings-core.js');
const BACKGROUND_RUNTIME_CORE_PATH = path.join(ROOT, 'extension', 'background-runtime-core.js');
const BACKGROUND_CONTROL_SYNC_PATH = path.join(ROOT, 'extension', 'background-control-sync.js');
const BACKGROUND_LIVE_CLIENT_PATH = path.join(ROOT, 'extension', 'background-live-client.js');

function waitFor(predicate, timeoutMs = 2000) {
  const startedAt = Date.now();
  return new Promise((resolve, reject) => {
    const poll = () => {
      if (predicate()) return resolve();
      if (Date.now() - startedAt >= timeoutMs) return reject(new Error('timed out waiting for background queue'));
      setTimeout(poll, 5);
    };
    poll();
  });
}

function createHarness(harnessOptions = {}) {
  const posts = [];
  const petPosts = [];
  const playbackPosts = [];
  const tabMessages = [];
  const storage = {
    enabled: true,
    apiUrl: 'http://127.0.0.1:8717/v1/speak',
    healthUrl: 'http://127.0.0.1:8717/health',
    voiceProfile: 'irodori-v3',
    voiceId: 'sample',
    referenceVoice: 'sample',
    ...(harnessOptions.initialStorage || {}),
  };
  let runtimeListener = null;
  let tabRemovedListener = null;
  let tabActivatedListener = null;
  let tabUpdatedListener = null;
  let playbackSequence = 0;

  const chrome = {
    storage: {
      local: {
        async get(query) {
          if (query === null || query === undefined) return { ...storage };
          if (Array.isArray(query)) return Object.fromEntries(query.map((key) => [key, storage[key]]));
          if (typeof query === 'object') return { ...query, ...storage };
          return { [query]: storage[query] };
        },
        async set(values) {
          Object.assign(storage, values);
        },
        async remove(keys) {
          for (const key of Array.isArray(keys) ? keys : [keys]) delete storage[key];
        },
      },
    },
    runtime: {
      getManifest() { return { version: '0.2.0' }; },
      onInstalled: { addListener() {} },
      onStartup: { addListener() {} },
      onMessage: {
        addListener(listener) {
          runtimeListener = listener;
        },
      },
    },
    tabs: {
      onRemoved: { addListener(listener) { tabRemovedListener = listener; } },
      onActivated: { addListener(listener) { tabActivatedListener = listener; } },
      onUpdated: { addListener(listener) { tabUpdatedListener = listener; } },
      async sendMessage(tabId, message) {
        tabMessages.push({ tabId, message });
        return { ok: true };
      },
    },
  };

  async function fetch(url, options = {}) {
    if (String(url).endsWith('/v1/speak') && options.method === 'POST') {
      const body = JSON.parse(options.body || '{}');
      posts.push(body);
      if (harnessOptions.speakSucceeds) {
        return {
          ok: true,
          status: 200,
          async json() {
            return {
              ok: true,
              audioUrl: `http://127.0.0.1:8717/audio/test-${posts.length}.wav`,
              referenceVoice: body.referenceVoice,
              usedReferenceAudio: harnessOptions.omitUsedReferenceAudio || !body.referenceVoice ? '' : 'applied.wav',
              ...(harnessOptions.localPlayback ? {
                playedLocally: true,
                playbackCompleted: true,
                stopped: false,
              } : {}),
            };
          },
        };
      }
      throw new Error('captured request');
    }
    if (String(url).endsWith('/v1/playback/replay') && options.method === 'POST') {
      const body = JSON.parse(options.body || '{}');
      playbackPosts.push({ type: 'replay', body });
      return {
        ok: true,
        status: 200,
        async json() {
          return {
            ok: true,
            audioUrl: 'http://127.0.0.1:8717/audio/replay.wav',
            playedLocally: true,
            playbackCompleted: true,
            stopped: false,
          };
        },
      };
    }
    if (String(url).endsWith('/v1/playback/stop') && options.method === 'POST') {
      playbackPosts.push({ type: 'stop', body: JSON.parse(options.body || '{}') });
      return {
        ok: true,
        status: 200,
        async json() { return { ok: true, stopping: true }; },
      };
    }
    if (String(url).endsWith('/v1/desktop-pet') && options.method === 'POST') {
      const body = JSON.parse(options.body || '{}');
      petPosts.push(body);
      return {
        ok: true,
        status: 200,
        async json() {
          return { ok: true, selectedPetId: body.petId };
        },
      };
    }
    throw new Error(`unexpected fetch: ${url}`);
  }

  const context = vm.createContext({
    chrome,
    console,
    crypto: { randomUUID: () => `playback-id-${++playbackSequence}` },
    fetch,
    setTimeout,
    clearTimeout,
    URL,
    Uint8Array,
    btoa: (value) => Buffer.from(value, 'binary').toString('base64'),
  });
  for (const dependencyPath of [
    BACKGROUND_CORE_PATH,
    BACKGROUND_SETTINGS_CORE_PATH,
    BACKGROUND_RUNTIME_CORE_PATH,
    BACKGROUND_CONTROL_SYNC_PATH,
    BACKGROUND_LIVE_CLIENT_PATH,
  ]) {
    vm.runInContext(
      fs.readFileSync(dependencyPath, 'utf8'),
      context,
      { filename: dependencyPath },
    );
  }
  vm.runInContext(fs.readFileSync(BACKGROUND_PATH, 'utf8'), context, { filename: BACKGROUND_PATH });
  assert.equal(typeof runtimeListener, 'function');

  function send(message, tabId, title) {
    let response;
    runtimeListener(message, {
      tab: {
        id: tabId,
        title,
        url: `https://chatgpt.com/c/${tabId}`,
        active: true,
      },
    }, (value) => { response = value; });
    return response;
  }

  function sendAsync(message, tabId, title) {
    return new Promise((resolve) => {
      runtimeListener(message, {
        tab: {
          id: tabId,
          title,
          url: `https://chatgpt.com/c/${tabId}`,
        },
      }, resolve);
    });
  }

  return {
    petPosts,
    playbackPosts,
    posts,
    send,
    sendAsync,
    tabMessages,
    setStorage(values) { Object.assign(storage, values); },
    removeTab(tabId) { tabRemovedListener(tabId); },
    activateTab(tabId) { tabActivatedListener({ tabId }); },
    updateTab(tabId, changeInfo) { tabUpdatedListener(tabId, changeInfo, {}); },
    expirePlayback() { return vm.runInContext('recoverExpiredPlayback(Date.now() + 1_000_000)', context); },
    finishCurrentPlayback() { return vm.runInContext('finishPlayback({ playbackToken: currentToken, ok: true, stopped: false })', context); },
  };
}

test('split-view heartbeats do not move the Local Voice owner between active tabs', () => {
  const harness = createHarness();

  const first = harness.send({ type: 'register-tab', title: 'Tab A' }, 101, 'Tab A');
  const second = harness.send({ type: 'register-tab', title: 'Tab B' }, 202, 'Tab B');
  const firstHeartbeat = harness.send({ type: 'register-tab', title: 'Tab A' }, 101, 'Tab A');
  const secondHeartbeat = harness.send({ type: 'register-tab', title: 'Tab B' }, 202, 'Tab B');
  const focusedSecond = harness.send({ type: 'register-tab', title: 'Tab B', claimOwner: true }, 202, 'Tab B');
  const firstAfterFocus = harness.send({ type: 'register-tab', title: 'Tab A' }, 101, 'Tab A');

  assert.equal(first.payload.isUiOwner, true);
  assert.equal(second.payload.isUiOwner, false);
  assert.equal(firstHeartbeat.payload.isUiOwner, true);
  assert.equal(secondHeartbeat.payload.isUiOwner, false);
  assert.equal(focusedSecond.payload.isUiOwner, true);
  assert.equal(firstAfterFocus.payload.isUiOwner, false);
});

test('continuous queue keeps the selected reference voice when one tab omits legacy voice fields', async () => {
  const harness = createHarness();
  harness.send({ type: 'register-tab', title: 'Tab A' }, 101, 'Tab A');
  harness.send({ type: 'register-tab', title: 'Tab B' }, 202, 'Tab B');

  harness.send({
    type: 'report-chunks',
    messageKey: 'reply-a',
    chunks: ['最初のタブです。'],
    autoPreview: '最初のタブです。',
    isAuto: true,
    voiceId: 'sample',
    referenceVoice: 'sample',
  }, 101, 'Tab A');

  harness.send({
    type: 'report-chunks',
    messageKey: 'reply-b',
    chunks: ['次のタブです。'],
    autoPreview: '次のタブです。',
    isAuto: true,
  }, 202, 'Tab B');

  await waitFor(() => harness.posts.length === 2);
  assert.deepEqual(harness.posts.map((body) => body.referenceVoice), ['sample', 'sample']);
  assert.deepEqual(harness.posts.map((body) => body.voiceId), ['sample', 'sample']);
});

test('stale empty content settings cannot override the selected reference voice', async () => {
  const harness = createHarness();
  harness.send({ type: 'register-tab', title: 'Tab A' }, 101, 'Tab A');
  harness.send({
    type: 'report-chunks',
    messageKey: 'reply-none',
    chunks: ['参照音声なしです。'],
    autoPreview: '参照音声なしです。',
    isAuto: true,
    voiceId: '',
    referenceVoice: '',
  }, 101, 'Tab A');

  await waitFor(() => harness.posts.length === 1);
  assert.equal(harness.posts[0].referenceVoice, 'sample');
  assert.equal(harness.posts[0].voiceId, 'sample');
});

test('a legacy valid referenceVoice survives an empty default voiceId during migration', async () => {
  const harness = createHarness({ initialStorage: { voiceId: '', referenceVoice: 'sample' } });
  harness.send({ type: 'register-tab', title: 'Tab A' }, 101, 'Tab A');
  harness.send({
    type: 'report-chunks',
    messageKey: 'reply-migrated-reference',
    chunks: ['保存済み参照音声を維持します。'],
    autoPreview: '保存済み参照音声を維持します。',
    isAuto: true,
    voiceId: '',
    referenceVoice: '',
  }, 101, 'Tab A');

  await waitFor(() => harness.posts.length === 1);
  assert.equal(harness.posts[0].referenceVoice, 'sample');
  assert.equal(harness.posts[0].voiceId, 'sample');
});

test('Next and Regen use the saved reference voice when legacy fields are omitted', async () => {
  const harness = createHarness();
  harness.send({ type: 'register-tab', title: 'Tab A' }, 101, 'Tab A');
  harness.send({
    type: 'report-chunks',
    messageKey: 'reply-controls',
    chunks: ['最初の部分です。', '次の部分です。'],
    autoPreview: '最初の部分です。',
    isAuto: false,
  }, 101, 'Tab A');

  harness.send({
    type: 'ui-command',
    cmd: 'next',
    params: {},
  }, 101, 'Tab A');
  await waitFor(() => harness.posts.length === 1);

  harness.send({
    type: 'ui-command',
    cmd: 'regen',
    params: {},
  }, 101, 'Tab A');
  await waitFor(() => harness.posts.length === 2);

  assert.deepEqual(harness.posts.map((body) => body.referenceVoice), ['sample', 'sample']);
  assert.deepEqual(harness.posts.map((body) => body.voiceId), ['sample', 'sample']);
});

test('queued Auto items keep the reference voice selected when they entered the queue', async () => {
  const harness = createHarness({ speakSucceeds: true });
  harness.send({ type: 'register-tab', title: 'Tab A' }, 101, 'Tab A');
  harness.send({
    type: 'report-chunks', messageKey: 'reply-a', chunks: ['一件目です。'], autoPreview: '一件目です。', isAuto: true,
  }, 101, 'Tab A');
  await waitFor(() => harness.tabMessages.some((entry) => entry.message.type === 'play-audio'));

  harness.send({
    type: 'report-chunks', messageKey: 'reply-b', chunks: ['二件目です。'], autoPreview: '二件目です。', isAuto: true,
  }, 101, 'Tab A');
  harness.setStorage({ voiceId: 'asuka', referenceVoice: 'asuka' });
  harness.finishCurrentPlayback();

  await waitFor(() => harness.posts.length === 2);
  assert.equal(harness.posts[0].referenceVoice, 'sample');
  assert.equal(harness.posts[1].referenceVoice, 'sample');
  harness.send({ type: 'ui-command', cmd: 'stop' }, 101, 'Tab A');
});

test('a selected reference voice is not played when the API omits proof that it was applied', async () => {
  const harness = createHarness({ speakSucceeds: true, omitUsedReferenceAudio: true });
  harness.send({ type: 'register-tab', title: 'Tab A' }, 101, 'Tab A');
  harness.send({
    type: 'report-chunks', messageKey: 'reply-unverified', chunks: ['参照音声を検証します。'], autoPreview: '参照音声を検証します。', isAuto: true,
  }, 101, 'Tab A');

  await waitFor(() => harness.posts.length === 1);
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(harness.tabMessages.some((entry) => entry.message.type === 'play-audio'), false);
});

test('desktop pet selection is forwarded to the local desktop pet API', async () => {
  const harness = createHarness();

  const response = await harness.sendAsync({
    type: 'desktop-pet-selection',
    petId: 'misaka',
  }, 101, 'Tab A');

  assert.deepEqual(harness.petPosts, [{ petId: 'misaka' }]);
  assert.equal(response.ok, true);
  assert.equal(response.payload.ok, true);
  assert.equal(response.payload.selectedPetId, 'misaka');
});

test('local playback does not use a ChatGPT tab and survives its closure', async () => {
  const harness = createHarness({ speakSucceeds: true, localPlayback: true });
  harness.send({ type: 'register-tab', title: 'Tab A' }, 101, 'Tab A');
  harness.send({
    type: 'report-chunks', messageKey: 'reply-local', chunks: ['ローカル再生です。'], autoPreview: 'ローカル再生です。', isAuto: true,
  }, 101, 'Tab A');

  await waitFor(() => harness.posts.length === 1);
  await waitFor(() => harness.tabMessages.some((entry) => (
    entry.message.type === 'state-update' && entry.message.payload.replayAvailable === true
  )));
  harness.removeTab(101);

  assert.equal(harness.posts[0].playLocal, true);
  assert.equal(harness.posts[0].voiceVolume, 0.6);
  assert.equal(harness.tabMessages.some((entry) => entry.message.type === 'play-audio'), false);
  assert.equal(harness.playbackPosts.some((entry) => entry.type === 'stop'), false);
});

test('closing the playback owner releases the queue and continues on another ChatGPT tab', async () => {
  const harness = createHarness({ speakSucceeds: true });
  harness.send({ type: 'register-tab', title: 'Tab A' }, 101, 'Tab A');
  harness.send({ type: 'register-tab', title: 'Tab B' }, 202, 'Tab B');
  harness.send({
    type: 'report-chunks', messageKey: 'reply-a', chunks: ['最初の読み上げです。'], autoPreview: '最初の読み上げです。', isAuto: true,
  }, 101, 'Tab A');
  await waitFor(() => harness.tabMessages.some((entry) => entry.tabId === 101 && entry.message.type === 'play-audio'));
  harness.send({
    type: 'report-chunks', messageKey: 'reply-b', chunks: ['次の読み上げです。'], autoPreview: '次の読み上げです。', isAuto: true,
  }, 202, 'Tab B');

  harness.removeTab(101);

  await waitFor(() => harness.tabMessages.some((entry) => entry.tabId === 202 && entry.message.type === 'play-audio'));
  assert.equal(harness.posts.length, 2);
  harness.send({ type: 'ui-command', cmd: 'stop' }, 202, 'Tab B');
});

test('a lost playback completion is skipped when its lease expires', async () => {
  const harness = createHarness({ speakSucceeds: true });
  harness.send({ type: 'register-tab', title: 'Tab A' }, 101, 'Tab A');
  harness.send({
    type: 'report-chunks', messageKey: 'reply-a', chunks: ['一件目です。'], autoPreview: '一件目です。', isAuto: true,
  }, 101, 'Tab A');
  await waitFor(() => harness.tabMessages.filter((entry) => entry.message.type === 'play-audio').length === 1);
  harness.send({
    type: 'report-chunks', messageKey: 'reply-b', chunks: ['二件目です。'], autoPreview: '二件目です。', isAuto: true,
  }, 101, 'Tab A');

  assert.equal(harness.expirePlayback(), true);

  await waitFor(() => harness.tabMessages.filter((entry) => entry.message.type === 'play-audio').length === 2);
  assert.equal(harness.posts.length, 2);
  harness.send({ type: 'ui-command', cmd: 'stop' }, 101, 'Tab A');
});

test('Stop targets the tab that owns playback after another tab becomes active', async () => {
  const harness = createHarness({ speakSucceeds: true });
  harness.send({ type: 'register-tab', title: 'Tab A' }, 101, 'Tab A');
  harness.send({ type: 'register-tab', title: 'Tab B' }, 202, 'Tab B');
  harness.send({
    type: 'report-chunks', messageKey: 'reply-a', chunks: ['再生中です。'], autoPreview: '再生中です。', isAuto: true,
  }, 101, 'Tab A');
  await waitFor(() => harness.tabMessages.some((entry) => entry.tabId === 101 && entry.message.type === 'play-audio'));

  harness.activateTab(202);
  harness.send({ type: 'ui-command', cmd: 'stop' }, 202, 'Tab B');

  const stopMessages = harness.tabMessages.filter((entry) => entry.message.type === 'stop-audio');
  assert.equal(stopMessages.at(-1).tabId, 101);
});
