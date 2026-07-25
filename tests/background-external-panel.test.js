'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const ROOT = path.resolve(__dirname, '..');
const BACKGROUND_PATH = path.join(ROOT, 'extension', 'background.js');

function waitFor(predicate, timeoutMs = 2000) {
  const startedAt = Date.now();
  return new Promise((resolve, reject) => {
    const poll = () => {
      if (predicate()) return resolve();
      if (Date.now() - startedAt >= timeoutMs) return reject(new Error('timed out'));
      setTimeout(poll, 5);
    };
    poll();
  });
}

function response(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async json() { return body; },
  };
}

function createHarness({ initialized = true, openTabs = [], missingContentScriptTabs = [] } = {}) {
  const storage = {
    enabled: false,
    apiUrl: 'http://127.0.0.1:8717/v1/speak',
    healthUrl: 'http://127.0.0.1:8717/health',
    voiceProfile: 'irodori-v3',
    voiceId: 'sample',
    referenceVoice: 'sample',
    voiceVolume: 0.6,
    micConversationEnabled: false,
    sttModel: 'small',
    cancelGraceMs: 700,
  };
  const speakPosts = [];
  const petPosts = [];
  const statePosts = [];
  const conversationStatePosts = [];
  const settingsPosts = [];
  const sentMessages = [];
  const injectedScripts = [];
  const injectedTabs = new Set();
  const missingContentScripts = new Set(missingContentScriptTabs.map(Number));
  const tabMessageResponders = new Map();
  let pollError = null;
  let runtimeListener = null;
  let tabsRemovedListener = null;
  let tabsUpdatedListener = null;
  let control = {
    ok: true,
    initialized,
    settingsRevision: 3,
    settings: {
      enabled: true,
      voiceVolume: 0.25,
      referenceVoice: 'asuka',
      micConversationEnabled: true,
      sttModel: 'medium',
      cancelGraceMs: 900,
    },
    commands: [{ id: 1, command: 'next' }],
    conversationEvents: [],
  };

  const chrome = {
    storage: {
      local: {
        async get(query) {
          if (query === null || query === undefined) return { ...storage };
          if (Array.isArray(query)) return Object.fromEntries(query.map((key) => [key, storage[key]]));
          if (typeof query === 'object') return { ...query, ...storage };
          return { [query]: storage[query] };
        },
        async set(values) { Object.assign(storage, values); },
        async remove(keys) {
          for (const key of Array.isArray(keys) ? keys : [keys]) delete storage[key];
        },
      },
    },
    runtime: {
      getManifest() { return { version: '0.2.0' }; },
      onInstalled: { addListener() {} },
      onStartup: { addListener() {} },
      onMessage: { addListener(listener) { runtimeListener = listener; } },
    },
    tabs: {
      onRemoved: { addListener(listener) { tabsRemovedListener = listener; } },
      onActivated: { addListener() {} },
      onUpdated: { addListener(listener) { tabsUpdatedListener = listener; } },
      async query() {
        return openTabs.map((tab) => ({ ...tab }));
      },
      async sendMessage(tabId, message) {
        sentMessages.push({ tabId, message });
        if (message.type === 'bridge-reconnect'
          && missingContentScripts.has(Number(tabId))
          && !injectedTabs.has(Number(tabId))) {
          throw new Error('Could not establish connection. Receiving end does not exist.');
        }
        const responder = tabMessageResponders.get(tabId);
        if (responder) return responder(message);
        if (message.type === 'conversation-target-status') {
          return { ok: true, composerAvailable: true, composerFocused: false, documentFocused: false, visible: false };
        }
        return { ok: true };
      },
    },
    scripting: {
      async executeScript(details) {
        injectedScripts.push(details);
        injectedTabs.add(Number(details?.target?.tabId));
        return [];
      },
    },
  };

  async function fetch(url, options = {}) {
    const target = new URL(String(url));
    if (target.pathname === '/v1/control-panel/poll') {
      if (pollError) throw new Error(pollError);
      const after = Number(target.searchParams.get('after') || 0);
      return response({
        ...control,
        commands: control.commands.filter((item) => item.id > after),
      });
    }
    if (target.pathname === '/v1/control-panel/settings' && options.method === 'POST') {
      const body = JSON.parse(options.body || '{}');
      settingsPosts.push(body);
      control = {
        ...control,
        initialized: true,
        settingsRevision: control.settingsRevision + 1,
        settings: {
          ...control.settings,
          ...(Object.prototype.hasOwnProperty.call(body, 'enabled') ? { enabled: Boolean(body.enabled) } : {}),
          ...(Object.prototype.hasOwnProperty.call(body, 'voiceVolume') ? { voiceVolume: Number(body.voiceVolume) } : {}),
          ...(Object.prototype.hasOwnProperty.call(body, 'referenceVoice') ? { referenceVoice: String(body.referenceVoice || '') } : {}),
          ...(Object.prototype.hasOwnProperty.call(body, 'micConversationEnabled') ? { micConversationEnabled: Boolean(body.micConversationEnabled) } : {}),
          ...(Object.prototype.hasOwnProperty.call(body, 'sttModel') ? { sttModel: String(body.sttModel || 'small') } : {}),
          ...(Object.prototype.hasOwnProperty.call(body, 'cancelGraceMs') ? { cancelGraceMs: Number(body.cancelGraceMs ?? 700) } : {}),
        },
      };
      return response(control);
    }
    if (target.pathname === '/v1/control-panel/state' && options.method === 'POST') {
      const body = JSON.parse(options.body || '{}');
      statePosts.push(body);
      return response({ ok: true, extension: body });
    }
    if (target.pathname === '/v1/conversation/state' && options.method === 'POST') {
      const body = JSON.parse(options.body || '{}');
      conversationStatePosts.push(body);
      return response({ ok: true, conversation: body });
    }
    if (target.pathname === '/v1/desktop-pet' && options.method === 'POST') {
      const body = JSON.parse(options.body || '{}');
      petPosts.push(body);
      return response({ ok: true, selectedPetId: body.petId });
    }
    if (target.pathname === '/v1/speak' && options.method === 'POST') {
      const body = JSON.parse(options.body || '{}');
      speakPosts.push(body);
      return response({
        ok: true,
        audioUrl: 'http://127.0.0.1:8717/audio/test.wav',
        voiceProfile: 'irodori-v3',
        referenceVoice: body.referenceVoice,
      });
    }
    throw new Error(`unexpected fetch: ${url}`);
  }

  const context = vm.createContext({
    chrome,
    console,
    crypto: { randomUUID: () => 'playback-id' },
    fetch,
    setTimeout,
    clearTimeout,
    URL,
    Uint8Array,
    btoa: (value) => Buffer.from(value, 'binary').toString('base64'),
  });
  vm.runInContext(fs.readFileSync(BACKGROUND_PATH, 'utf8'), context, { filename: BACKGROUND_PATH });
  assert.equal(typeof runtimeListener, 'function');

  function send(message, tabId = 101, title = 'Tab A') {
    let value;
    runtimeListener(message, {
      tab: { id: tabId, title, url: `https://chatgpt.com/c/${tabId}`, active: true },
    }, (responseValue) => { value = responseValue; });
    return value;
  }

  function sendAsync(message, tabId = 101, title = 'Tab A') {
    return new Promise((resolve) => {
      runtimeListener(message, {
        tab: { id: tabId, title, url: `https://chatgpt.com/c/${tabId}`, active: true },
      }, resolve);
    });
  }

  return {
    control: () => control,
    setControl(next) { control = { ...control, ...next }; },
    setPollError(error) { pollError = error ? String(error) : null; },
    setTabResponder(tabId, responder) { tabMessageResponders.set(tabId, responder); },
    removeTab(tabId) { tabsRemovedListener(tabId); },
    reloadTab(tabId) { tabsUpdatedListener(tabId, { status: 'loading' }); },
    conversationStatePosts,
    injectedScripts,
    petPosts,
    sentMessages,
    settingsPosts,
    speakPosts,
    statePosts,
    storage,
    send,
    sendAsync,
  };
}

test('recovering the local API asks every already-open ChatGPT tab to reconnect', async () => {
  const harness = createHarness({
    openTabs: [
      { id: 101, title: 'Tab A', url: 'https://chatgpt.com/c/101' },
      { id: 202, title: 'Tab B', url: 'https://chatgpt.com/c/202' },
    ],
  });
  harness.send({ type: 'register-tab', title: 'Tab A' }, 101, 'Tab A');

  const initial = await harness.sendAsync({ type: 'external-control-poll' }, 101, 'Tab A');
  assert.equal(initial.ok, true);
  await waitFor(() => harness.sentMessages.filter((entry) => entry.message.type === 'bridge-reconnect').length >= 2);

  harness.sentMessages.length = 0;
  harness.setPollError('local API unavailable');
  const failed = await harness.sendAsync({ type: 'external-control-poll' }, 101, 'Tab A');
  assert.equal(failed.ok, false);

  harness.setPollError(null);
  const recovered = await harness.sendAsync({ type: 'external-control-poll' }, 101, 'Tab A');
  assert.equal(recovered.ok, true);
  await waitFor(() => harness.sentMessages.filter((entry) => entry.message.type === 'bridge-reconnect').length === 2);
  assert.deepEqual(
    harness.sentMessages
      .filter((entry) => entry.message.type === 'bridge-reconnect')
      .map((entry) => entry.tabId)
      .sort((a, b) => a - b),
    [101, 202],
  );
});

test('missing receivers are injected into already-open ChatGPT tabs before reconnecting', async () => {
  const harness = createHarness({
    openTabs: [
      { id: 303, title: 'Existing Tab', url: 'https://chatgpt.com/c/303' },
    ],
    missingContentScriptTabs: [303],
  });

  const result = await harness.sendAsync({ type: 'external-control-poll' }, 303, 'Existing Tab');
  assert.equal(result.ok, true);
  await waitFor(() => harness.injectedScripts.length === 1);
  assert.deepEqual(JSON.parse(JSON.stringify(harness.injectedScripts[0])), {
    target: { tabId: 303 },
    files: ['prompt-input-core.js', 'content.js'],
  });
  await waitFor(() => harness.sentMessages.filter((entry) => entry.tabId === 303 && entry.message.type === 'bridge-reconnect').length >= 2);
});

test('an existing reconnect receiver failure does not inject a duplicate content script', async () => {
  const harness = createHarness({
    openTabs: [
      { id: 404, title: 'Existing Receiver', url: 'https://chatgpt.com/c/404' },
    ],
  });
  harness.setTabResponder(404, (message) => (
    message.type === 'bridge-reconnect'
      ? { ok: false, error: 'temporary registration failure' }
      : { ok: true }
  ));

  const result = await harness.sendAsync({ type: 'external-control-poll' }, 404, 'Existing Receiver');
  assert.equal(result.ok, true);
  assert.ok(
    harness.sentMessages.filter((entry) => entry.tabId === 404 && entry.message.type === 'bridge-reconnect').length >= 1,
  );
  assert.equal(harness.injectedScripts.length, 0);
});

test('external panel poll applies settings, executes each command once, and posts global state', async () => {
  const harness = createHarness();
  harness.send({ type: 'register-tab', title: 'Tab A' }, 101, 'Tab A');
  harness.send({
    type: 'report-chunks',
    messageKey: 'manual-reply',
    chunks: ['最初のチャンクです。', '次のチャンクです。'],
    autoPreview: '最初のチャンクです。',
    isAuto: false,
  }, 101, 'Tab A');

  const first = await harness.sendAsync({ type: 'external-control-poll' }, 101, 'Tab A');
  await waitFor(() => harness.speakPosts.length === 1 && harness.statePosts.length >= 1);

  assert.equal(first.ok, true);
  assert.equal(harness.storage.enabled, true);
  assert.equal(harness.storage.voiceVolume, 0.25);
  assert.equal(harness.storage.voiceId, 'asuka');
  assert.equal(harness.storage.referenceVoice, 'asuka');
  assert.equal(harness.storage.micConversationEnabled, true);
  assert.equal(harness.storage.sttModel, 'small');
  assert.equal(harness.storage.cancelGraceMs, 700);
  assert.deepEqual(harness.petPosts.at(-1), { petId: 'asuka' });
  assert.equal(harness.speakPosts[0].text, '最初のチャンクです。');
  assert.equal(harness.speakPosts[0].referenceVoice, 'asuka');
  assert.equal(harness.statePosts.at(-1).tabsCount, 1);
  assert.equal(harness.statePosts.at(-1).currentText, '最初のチャンクです。');

  await harness.sendAsync({ type: 'external-control-poll' }, 101, 'Tab A');
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(harness.speakPosts.length, 1);
});

test('streaming updates preserve the already-read Auto text as the Next boundary', async () => {
  const harness = createHarness();
  harness.send({ type: 'register-tab', title: 'Tab A' }, 101, 'Tab A');
  harness.send({
    type: 'report-chunks',
    messageKey: 'streaming-reply',
    chunks: ['概ね妥当です。'],
    autoPreview: '概ね妥当です。',
    isAuto: true,
  }, 101, 'Tab A');
  await waitFor(() => harness.speakPosts.length === 1);
  harness.send({ type: 'playback-done', playbackToken: 'playback-id', ok: true, stopped: false }, 101, 'Tab A');

  harness.send({
    type: 'report-chunks',
    messageKey: 'streaming-reply',
    chunks: [
      '概ね妥当です。 ただし、公開時の誤認防止とブランド統一のために変更すべき項目があります。',
      'Chrome拡張名、EXE名、スタートメニュー名、READMEタイトルを独自名称へ統一します。',
    ],
    autoPreview: '概ね妥当です。 ただし、公開時の誤認防止とブランド統一のために変更すべき項目があります。',
    isAuto: false,
  }, 101, 'Tab A');
  harness.send({ type: 'ui-command', cmd: 'next' }, 101, 'Tab A');
  await waitFor(() => harness.speakPosts.length === 2);

  assert.equal(
    harness.speakPosts[1].text,
    'ただし、公開時の誤認防止とブランド統一のために変更すべき項目があります。',
  );
});

test('first extension poll seeds an uninitialized external panel from existing Chrome settings', async () => {
  const harness = createHarness({ initialized: false });
  harness.send({ type: 'register-tab', title: 'Tab A' }, 101, 'Tab A');

  const result = await harness.sendAsync({ type: 'external-control-poll' }, 101, 'Tab A');

  assert.equal(result.ok, true);
  assert.equal(harness.settingsPosts.length, 1);
  assert.deepEqual(harness.settingsPosts[0], {
    enabled: false,
    voiceVolume: 0.6,
    referenceVoice: 'sample',
    micConversationEnabled: false,
    sttModel: 'small',
    cancelGraceMs: 700,
    initialized: true,
  });
});

test('conversation events are delivered to the selected ChatGPT tab once', async () => {
  const harness = createHarness();
  harness.send({ type: 'register-tab', title: 'Tab A' }, 101, 'Tab A');
  harness.setControl({
    commands: [],
    conversationEvents: [
      { id: 1, type: 'cancel_pending', payload: { sessionId: 4 } },
      { id: 2, type: 'transcript', payload: { sessionId: 4, text: '音声入力です', cancelGraceMs: 700 } },
    ],
  });

  await harness.sendAsync({ type: 'external-control-poll' }, 101, 'Tab A');
  await harness.sendAsync({ type: 'external-control-poll' }, 101, 'Tab A');

  const conversationMessages = harness.sentMessages.filter(({ message }) => (
    message.type === 'cancel-voice-send' || message.type === 'voice-transcript'
  ));
  assert.deepEqual(conversationMessages.map(({ tabId, message }) => ({ tabId, type: message.type })), [
    { tabId: 101, type: 'cancel-voice-send' },
    { tabId: 101, type: 'voice-transcript' },
  ]);
  assert.equal(conversationMessages[1].message.payload.text, '音声入力です');
});

test('conversation transcript stays on the tab whose composer was focused when recording started', async () => {
  const harness = createHarness();
  harness.send({ type: 'register-tab', title: 'Tab A' }, 101, 'Tab A');
  harness.send({ type: 'register-tab', title: 'Tab B', claimOwner: true }, 202, 'Tab B');
  harness.send({ type: 'composer-focused' }, 101, 'Tab A');
  harness.setControl({
    commands: [],
    conversation: { phase: 'transcribing' },
    conversationEvents: [
      { id: 1, type: 'cancel_pending', payload: { sessionId: 9 } },
      { id: 2, type: 'transcript', payload: { sessionId: 9, text: 'フォーカスしたタブへ送る', cancelGraceMs: 700 } },
    ],
  });

  await harness.sendAsync({ type: 'external-control-poll' }, 202, 'Tab B');

  const transcripts = harness.sentMessages.filter(({ message }) => message.type === 'voice-transcript');
  assert.equal(transcripts.length, 1);
  assert.equal(transcripts[0].tabId, 101);
  assert.equal(transcripts[0].message.payload.text, 'フォーカスしたタブへ送る');
  const cancels = harness.sentMessages.filter(({ message }) => message.type === 'cancel-voice-send');
  assert.deepEqual(new Set(cancels.map(({ tabId }) => tabId)), new Set([101, 202]));
});

test('recording start prefers the composer that is actually focused over stale tab history', async () => {
  const harness = createHarness();
  harness.send({ type: 'register-tab', title: 'Tab A' }, 101, 'Tab A');
  harness.send({ type: 'register-tab', title: 'Tab B', claimOwner: true }, 202, 'Tab B');
  harness.send({ type: 'composer-focused' }, 101, 'Tab A');
  harness.setTabResponder(101, (message) => {
    if (message.type === 'conversation-target-status') {
      return { ok: true, composerAvailable: false, composerFocused: false, documentFocused: false, visible: false };
    }
    return { ok: true };
  });
  harness.setTabResponder(202, (message) => {
    if (message.type === 'conversation-target-status') {
      return { ok: true, composerAvailable: true, composerFocused: true, documentFocused: true, visible: true };
    }
    return { ok: true };
  });
  harness.setControl({
    commands: [],
    conversationEvents: [
      { id: 1, type: 'cancel_pending', payload: { sessionId: 10 } },
      { id: 2, type: 'transcript', payload: { sessionId: 10, text: '現在の入力欄へ送る', cancelGraceMs: 700 } },
    ],
  });

  await harness.sendAsync({ type: 'external-control-poll' }, 202, 'Tab B');

  const transcripts = harness.sentMessages.filter(({ message }) => message.type === 'voice-transcript');
  assert.deepEqual(transcripts.map(({ tabId }) => tabId), [202]);
});

test('transcript insertion retries only on the captured tab after a transient composer failure', async () => {
  const harness = createHarness();
  harness.send({ type: 'register-tab', title: 'Tab A' }, 101, 'Tab A');
  let transcriptAttempts = 0;
  harness.setTabResponder(101, (message) => {
    if (message.type === 'conversation-target-status') {
      return { ok: true, composerAvailable: true, composerFocused: true, documentFocused: true, visible: true };
    }
    if (message.type === 'voice-transcript') {
      transcriptAttempts += 1;
      return transcriptAttempts === 1
        ? { ok: false, reason: 'composer-not-found' }
        : { ok: true };
    }
    return { ok: true };
  });
  harness.setControl({
    commands: [],
    conversationEvents: [
      { id: 1, type: 'cancel_pending', payload: { sessionId: 11 } },
      { id: 2, type: 'transcript', payload: { sessionId: 11, text: '再試行する音声入力', cancelGraceMs: 700 } },
    ],
  });

  await harness.sendAsync({ type: 'external-control-poll' }, 101, 'Tab A');

  const transcripts = harness.sentMessages.filter(({ message }) => message.type === 'voice-transcript');
  assert.equal(transcriptAttempts, 2);
  assert.deepEqual(transcripts.map(({ tabId }) => tabId), [101, 101]);
  assert.equal(harness.conversationStatePosts.some((post) => post.phase === 'error'), false);
});

test('failed transcript insertion never falls through to another ChatGPT tab', async () => {
  const harness = createHarness();
  harness.send({ type: 'register-tab', title: 'Tab A' }, 101, 'Tab A');
  harness.send({ type: 'register-tab', title: 'Tab B', claimOwner: true }, 202, 'Tab B');
  harness.setTabResponder(101, (message) => {
    if (message.type === 'conversation-target-status') {
      return { ok: true, composerAvailable: true, composerFocused: true, documentFocused: true, visible: true };
    }
    if (message.type === 'voice-transcript') return { ok: false, reason: 'composer-not-found' };
    return { ok: true };
  });
  harness.setTabResponder(202, (message) => {
    if (message.type === 'conversation-target-status') {
      return { ok: true, composerAvailable: true, composerFocused: false, documentFocused: false, visible: false };
    }
    return { ok: true };
  });
  harness.setControl({
    commands: [],
    conversationEvents: [
      { id: 1, type: 'cancel_pending', payload: { sessionId: 12 } },
      { id: 2, type: 'transcript', payload: { sessionId: 12, text: '別タブへ送らない', cancelGraceMs: 700 } },
    ],
  });

  await harness.sendAsync({ type: 'external-control-poll' }, 202, 'Tab B');

  const transcripts = harness.sentMessages.filter(({ message }) => message.type === 'voice-transcript');
  assert.deepEqual(transcripts.map(({ tabId }) => tabId), [101, 101, 101]);
  assert.equal(harness.conversationStatePosts.at(-1).phase, 'error');
  assert.equal(harness.conversationStatePosts.at(-1).error, 'composer-not-found');
});

test('captured microphone target is not reused after same-tab conversation navigation', async () => {
  const harness = createHarness();
  harness.send({ type: 'register-tab', title: 'Tab A' }, 101, 'Tab A');
  harness.send({ type: 'register-tab', title: 'Tab B', claimOwner: true }, 202, 'Tab B');
  harness.send({ type: 'composer-focused' }, 101, 'Tab A');
  let targetUrl = 'https://chatgpt.com/c/original';
  harness.setTabResponder(101, (message) => {
    if (message.type === 'conversation-target-status') {
      return {
        ok: true,
        composerAvailable: true,
        composerFocused: true,
        documentFocused: true,
        visible: true,
        url: targetUrl,
      };
    }
    return { ok: true };
  });
  harness.setTabResponder(202, (message) => {
    if (message.type === 'conversation-target-status') {
      return {
        ok: true,
        composerAvailable: true,
        composerFocused: false,
        documentFocused: false,
        visible: false,
        url: 'https://chatgpt.com/c/other',
      };
    }
    return { ok: true };
  });
  harness.setControl({
    commands: [],
    conversationEvents: [
      { id: 1, type: 'cancel_pending', payload: { sessionId: 14 } },
    ],
  });
  await harness.sendAsync({ type: 'external-control-poll' }, 202, 'Tab B');

  targetUrl = 'https://chatgpt.com/c/navigated';
  harness.setControl({
    commands: [],
    conversationEvents: [
      { id: 2, type: 'transcript', payload: { sessionId: 14, text: '移動後の会話へ送らない', cancelGraceMs: 700 } },
    ],
  });
  await harness.sendAsync({ type: 'external-control-poll' }, 202, 'Tab B');

  const transcripts = harness.sentMessages.filter(({ message }) => message.type === 'voice-transcript');
  assert.equal(transcripts.length, 0);
  assert.equal(harness.conversationStatePosts.at(-1).phase, 'error');
  assert.equal(harness.conversationStatePosts.at(-1).error, 'conversation-target-page-changed');
});

for (const [label, invalidateTarget] of [
  ['closed', (harness) => harness.removeTab(101)],
  ['reloaded', (harness) => harness.reloadTab(101)],
]) {
  test(`captured microphone target is not replaced after the tab is ${label}`, async () => {
    const harness = createHarness();
    harness.send({ type: 'register-tab', title: 'Tab A' }, 101, 'Tab A');
    harness.send({ type: 'register-tab', title: 'Tab B', claimOwner: true }, 202, 'Tab B');
    harness.send({ type: 'composer-focused' }, 101, 'Tab A');
    harness.setControl({
      commands: [],
      conversationEvents: [
        { id: 1, type: 'cancel_pending', payload: { sessionId: 13 } },
      ],
    });
    await harness.sendAsync({ type: 'external-control-poll' }, 202, 'Tab B');

    invalidateTarget(harness);
    harness.setControl({
      commands: [],
      conversationEvents: [
        { id: 2, type: 'transcript', payload: { sessionId: 13, text: '代替タブへ送らない', cancelGraceMs: 700 } },
      ],
    });
    await harness.sendAsync({ type: 'external-control-poll' }, 202, 'Tab B');

    const transcripts = harness.sentMessages.filter(({ message }) => message.type === 'voice-transcript');
    assert.equal(transcripts.length, 0);
    assert.equal(harness.conversationStatePosts.at(-1).phase, 'error');
    assert.equal(harness.conversationStatePosts.at(-1).error, 'conversation-target-not-found');
  });
}

test('assistant replies are not auto-queued while microphone transcription is active', async () => {
  const harness = createHarness();
  harness.send({ type: 'register-tab', title: 'Tab A' }, 101, 'Tab A');
  harness.setControl({
    commands: [],
    conversation: { phase: 'transcribing' },
    conversationEvents: [],
  });
  await harness.sendAsync({ type: 'external-control-poll' }, 101, 'Tab A');

  harness.send({
    type: 'report-chunks',
    messageKey: 'reply-during-stt',
    chunks: ['文字起こし中に来た別の返答です。'],
    autoPreview: '文字起こし中に来た別の返答です。',
    isAuto: true,
  }, 101, 'Tab A');
  await new Promise((resolve) => setTimeout(resolve, 20));

  assert.equal(harness.speakPosts.length, 0);
});

test('content conversation state is posted to the loopback service without transcript text', async () => {
  const harness = createHarness();
  harness.send({ type: 'register-tab', title: 'Tab A' }, 101, 'Tab A');

  const result = await harness.sendAsync({
    type: 'conversation-state',
    payload: {
      phase: 'waiting_response',
      statusText: 'ChatGPT応答待ち',
      sttModel: 'small',
      error: '',
      text: '送信本文は転送しない',
    },
  }, 101, 'Tab A');

  assert.equal(result.ok, true);
  assert.equal(harness.conversationStatePosts.length, 1);
  assert.deepEqual(harness.conversationStatePosts[0], {
    phase: 'waiting_response',
    statusText: 'ChatGPT応答待ち',
    sttDevice: '',
    sttModel: 'small',
    error: '',
  });
});

test('options page pushes STT model and send grace to the local runtime', async () => {
  const harness = createHarness();
  harness.storage.sttModel = 'large-v3-turbo';
  harness.storage.cancelGraceMs = 1500;

  const result = await harness.sendAsync({ type: 'options-settings-updated' });

  assert.equal(result.ok, true);
  assert.deepEqual(harness.settingsPosts.at(-1), {
    sttModel: 'large-v3-turbo',
    cancelGraceMs: 1500,
  });
  assert.equal(harness.control().settings.sttModel, 'large-v3-turbo');
  assert.equal(harness.control().settings.cancelGraceMs, 1500);
});
