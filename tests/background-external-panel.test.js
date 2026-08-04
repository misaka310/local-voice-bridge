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
const BACKGROUND_QUEUE_CORE_PATH = path.join(ROOT, 'extension', 'background-queue-core.js');
const BACKGROUND_AUTO_RECHECK_PATH = path.join(ROOT, 'extension', 'background-auto-recheck.js');
const BACKGROUND_CONTROL_SYNC_PATH = path.join(ROOT, 'extension', 'background-control-sync.js');
const BACKGROUND_TAB_REGISTRY_PATH = path.join(ROOT, 'extension', 'background-tab-registry.js');
const BACKGROUND_CONVERSATION_TARGET_PATH = path.join(ROOT, 'extension', 'background-conversation-target.js');
const BACKGROUND_LOCAL_API_CLIENT_PATH = path.join(ROOT, 'extension', 'background-local-api-client.js');
const BACKGROUND_RUNTIME_STORE_PATH = path.join(ROOT, 'extension', 'background-runtime-store.js');
const BACKGROUND_PLAYBACK_QUEUE_PATH = path.join(ROOT, 'extension', 'background-playback-queue.js');
const BACKGROUND_LIVE_CLIENT_PATH = path.join(ROOT, 'extension', 'background-live-client.js');
const BACKGROUND_MESSAGE_ROUTER_PATH = path.join(ROOT, 'extension', 'background-message-router.js');

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

function createHarness({
  initialized = true,
  openTabs = [],
  missingContentScriptTabs = [],
  storage: sharedStorage = null,
  browserRuntime: initialBrowserRuntime = null,
  browserRuntimeError: initialBrowserRuntimeError = null,
  browserRuntimePostError: initialBrowserRuntimePostError = null,
} = {}) {
  const storage = sharedStorage || {
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
  const stopPosts = [];
  const petPosts = [];
  const statePosts = [];
  const conversationStatePosts = [];
  const settingsPosts = [];
  const sentMessages = [];
  const injectedScripts = [];
  const lifecycleEvents = [];
  let runtimeReloads = 0;
  const injectedTabs = new Set();
  const missingContentScripts = new Set(missingContentScriptTabs.map(Number));
  const tabMessageResponders = new Map();
  const registeredTabs = new Map();
  const acknowledged = new Map();
  const ackPosts = [];
  const browserRuntimePosts = [];
  let browserRuntime = initialBrowserRuntime || {
    tabs: [],
    selectedTabId: 0,
    uiOwnerTabId: 0,
    queue: [],
    currentItem: null,
    lastPlayedItem: null,
    seq: 1,
  };
  let pollError = null;
  let ackError = null;
  let browserRuntimeError = initialBrowserRuntimeError ? String(initialBrowserRuntimeError) : null;
  let browserRuntimePostError = initialBrowserRuntimePostError ? String(initialBrowserRuntimePostError) : null;
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
      referenceVoiceExplicit: true,
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
      reload() {
        lifecycleEvents.push('reload');
        runtimeReloads += 1;
      },
      onInstalled: { addListener() {} },
      onStartup: { addListener() {} },
      onMessage: { addListener(listener) { runtimeListener = listener; } },
    },
    tabs: {
      onRemoved: { addListener(listener) { tabsRemovedListener = listener; } },
      onActivated: { addListener() {} },
      onUpdated: { addListener(listener) { tabsUpdatedListener = listener; } },
      async query() {
        const source = openTabs.length ? openTabs : Array.from(registeredTabs.values());
        return source.map((tab) => ({ ...tab }));
      },
      async get(tabId) {
        const source = openTabs.length ? openTabs : Array.from(registeredTabs.values());
        const tab = source.find((item) => Number(item.id) === Number(tabId));
        if (!tab) throw new Error(`No tab with id: ${tabId}`);
        return { ...tab };
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
    if (target.pathname === '/v1/browser-runtime' && (!options.method || options.method === 'GET')) {
      if (browserRuntimeError) throw new Error(browserRuntimeError);
      return response({ ok: true, browserRuntime });
    }
    if (target.pathname === '/v1/browser-runtime' && options.method === 'POST') {
      if (browserRuntimePostError) throw new Error(browserRuntimePostError);
      browserRuntime = JSON.parse(options.body || '{}');
      browserRuntimePosts.push(browserRuntime);
      return response({ ok: true, browserRuntime });
    }
    if (target.pathname === '/v1/control-panel/poll') {
      if (pollError) throw new Error(pollError);
      const after = Number(target.searchParams.get('after') || 0);
      const afterEvent = Number(target.searchParams.get('afterEvent') || 0);
      const consumer = target.searchParams.get('consumer') || 'legacy';
      const cursor = acknowledged.get(consumer) || { command: 0, event: 0 };
      return response({
        ...control,
        commands: control.commands.filter((item) => item.id > Math.max(after, cursor.command)),
        conversationEvents: control.conversationEvents.filter((item) => item.id > Math.max(afterEvent, cursor.event)),
      });
    }
    if (target.pathname === '/v1/control-panel/ack' && options.method === 'POST') {
      if (ackError) throw new Error(ackError);
      const body = JSON.parse(options.body || '{}');
      ackPosts.push(body);
      lifecycleEvents.push('ack');
      const consumer = String(body.consumerId || 'legacy');
      const cursor = acknowledged.get(consumer) || { command: 0, event: 0 };
      if (Object.prototype.hasOwnProperty.call(body, 'commandId')) cursor.command = Math.max(cursor.command, Number(body.commandId) || 0);
      if (Object.prototype.hasOwnProperty.call(body, 'conversationEventId')) cursor.event = Math.max(cursor.event, Number(body.conversationEventId) || 0);
      acknowledged.set(consumer, cursor);
      return response({ ok: true, consumerId: consumer, commandId: cursor.command, conversationEventId: cursor.event });
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
          ...(Object.prototype.hasOwnProperty.call(body, 'referenceVoiceExplicit') ? { referenceVoiceExplicit: Boolean(body.referenceVoiceExplicit) } : {}),
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
    if (target.pathname === '/v1/playback/stop' && options.method === 'POST') {
      const body = JSON.parse(options.body || '{}');
      stopPosts.push(body);
      return response({ ok: true, stopping: true });
    }
    if (target.pathname === '/v1/speak' && options.method === 'POST') {
      const body = JSON.parse(options.body || '{}');
      speakPosts.push(body);
      return response({
        ok: true,
        audioUrl: 'http://127.0.0.1:8717/audio/test.wav',
        voiceProfile: 'irodori-v3',
        referenceVoice: body.referenceVoice,
        usedReferenceAudio: body.referenceVoice ? 'applied.wav' : '',
      });
    }
    throw new Error(`unexpected fetch: ${url}`);
  }

  let backgroundContext = null;

  function bootBackground() {
    runtimeListener = null;
    backgroundContext = vm.createContext({
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
    for (const dependencyPath of [
      BACKGROUND_CORE_PATH,
      BACKGROUND_SETTINGS_CORE_PATH,
      BACKGROUND_RUNTIME_CORE_PATH,
      BACKGROUND_QUEUE_CORE_PATH,
      BACKGROUND_AUTO_RECHECK_PATH,
      BACKGROUND_CONTROL_SYNC_PATH,
      BACKGROUND_TAB_REGISTRY_PATH,
      BACKGROUND_CONVERSATION_TARGET_PATH,
      BACKGROUND_LOCAL_API_CLIENT_PATH,
      BACKGROUND_RUNTIME_STORE_PATH,
      BACKGROUND_PLAYBACK_QUEUE_PATH,
      BACKGROUND_LIVE_CLIENT_PATH,
      BACKGROUND_MESSAGE_ROUTER_PATH,
    ]) {
      vm.runInContext(
        fs.readFileSync(dependencyPath, 'utf8'),
        backgroundContext,
        { filename: dependencyPath },
      );
    }
    vm.runInContext(fs.readFileSync(BACKGROUND_PATH, 'utf8'), backgroundContext, { filename: BACKGROUND_PATH });
  }
  bootBackground();
  assert.equal(typeof runtimeListener, 'function');

  function send(message, tabId = 101, title = 'Tab A') {
    if (message && message.type === 'register-tab') {
      registeredTabs.set(tabId, { id: tabId, title, url: `https://chatgpt.com/c/${tabId}` });
    }
    let value;
    runtimeListener(message, {
      tab: { id: tabId, title, url: `https://chatgpt.com/c/${tabId}`, active: true },
    }, (responseValue) => { value = responseValue; });
    return value;
  }

  function sendAsync(message, tabId = 101, title = 'Tab A') {
    if (message && message.type === 'register-tab') {
      registeredTabs.set(tabId, { id: tabId, title, url: `https://chatgpt.com/c/${tabId}` });
    }
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
    setAckError(error) { ackError = error ? String(error) : null; },
    setBrowserRuntimeError(error) { browserRuntimeError = error ? String(error) : null; },
    setBrowserRuntimePostError(error) { browserRuntimePostError = error ? String(error) : null; },
    backgroundState() {
      return vm.runInContext('({ isPlaying, playbackPhase, statusText: lastStatusText, queueSize: queue.length, currentItem })', backgroundContext);
    },
    setTabResponder(tabId, responder) { tabMessageResponders.set(tabId, responder); },
    reloadBackground() { bootBackground(); },
    runtimeReloads: () => runtimeReloads,
    lifecycleEvents,
    ready() { return vm.runInContext('initializeBackgroundRuntime()', backgroundContext); },
    ackPosts,
    browserRuntime: () => browserRuntime,
    browserRuntimePosts,
    removeTab(tabId) { registeredTabs.delete(tabId); tabsRemovedListener(tabId); },
    reloadTab(tabId) { tabsUpdatedListener(tabId, { status: 'loading' }); },
    conversationStatePosts,
    injectedScripts,
    petPosts,
    sentMessages,
    settingsPosts,
    speakPosts,
    stopPosts,
    statePosts,
    storage,
    send,
    sendAsync,
  };
}

test('tab attention uses the live Chrome tab state instead of a stale sender snapshot', async () => {
  const harness = createHarness({
    openTabs: [{
      id: 101,
      title: 'Tab A',
      url: 'https://chatgpt.com/c/101',
      active: false,
    }],
  });

  const responseValue = await harness.sendAsync({ type: 'tab-attention-state' }, 101);
  assert.equal(responseValue.ok, true);
  assert.equal(responseValue.payload.active, false);
});

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
    files: ['live-browser-core.js', 'live-content-controller.js', 'prompt-input-core.js', 'delivery-id-core.js', 'content-text-core.js', 'assistant-source-filter.js', 'assistant-text-extractor.js', 'auto-speech-controller.js', 'content-settings.js', 'content-dom-observer.js', 'content-completion-marker.js', 'content-conversation-bridge.js', 'content-audio-player.js', 'content-message-router.js', 'content.js'],
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

test('service-worker startup restores the latest response without auto-reading it', async () => {
  const harness = createHarness({
    openTabs: [{ id: 101, title: 'Tab A', url: 'https://chatgpt.com/c/101' }],
    browserRuntime: {
      tabs: [
        {
          id: 101,
          title: 'Tab A',
          url: 'https://chatgpt.com/c/101',
          lastReadIndex: 0,
          lastAutoQueueSignature: 'reply-restored\u0000最初です。',
          lastAssistantMessage: {
            messageKey: 'reply-restored',
            chunks: ['最初です。', '復元した続きです。'],
            capturedAt: 10,
          },
        },
      ],
      selectedTabId: 101,
      uiOwnerTabId: 101,
      queue: [],
      currentItem: null,
      lastPlayedItem: null,
      seq: 5,
    },
  });

  await waitFor(() => harness.sentMessages.some((entry) => entry.message.type === 'bridge-reconnect'));
  await new Promise((resolve) => setTimeout(resolve, 30));
  assert.equal(harness.speakPosts.length, 0);

  harness.send({ type: 'ui-command', cmd: 'next', params: {} }, 101, 'Tab A');
  await waitFor(() => harness.speakPosts.length === 1);
  assert.equal(harness.speakPosts[0].text, '復元した続きです。');
});

test('browser-runtime persistence failure releases playback instead of wedging the queue', async () => {
  const harness = createHarness({ browserRuntimePostError: 'state file unavailable' });
  await harness.ready();
  harness.send({ type: 'register-tab', title: 'Tab A' }, 101, 'Tab A');
  harness.send({
    type: 'report-chunks',
    messageKey: 'reply-persist-failure',
    chunks: ['保存できない場合です。'],
    autoPreview: '保存できない場合です。',
    isAuto: false,
  }, 101, 'Tab A');

  harness.send({ type: 'ui-command', cmd: 'next', params: {} }, 101, 'Tab A');

  await waitFor(() => harness.backgroundState().statusText.startsWith('Playback failed:'));
  assert.equal(harness.backgroundState().isPlaying, false);
  assert.equal(harness.backgroundState().playbackPhase, 'idle');
  assert.equal(harness.speakPosts.length, 0);
});

test('service-worker restart stops orphan playback and resumes the persisted current item first', async () => {
  const harness = createHarness({
    openTabs: [{ id: 101, title: 'Restored Tab', url: 'https://chatgpt.com/c/101' }],
    browserRuntime: {
      tabs: [
        {
          id: 101,
          title: 'Restored Tab',
          url: 'https://chatgpt.com/c/101',
          lastReadIndex: 0,
          lastAutoQueueSignature: 'reply-current\u0000生成中でした。',
          lastAssistantMessage: {
            messageKey: 'reply-current',
            chunks: ['生成中でした。', '待機中です。'],
            capturedAt: 10,
          },
        },
      ],
      selectedTabId: 101,
      uiOwnerTabId: 101,
      queue: [
        {
          id: 'q-pending-2',
          mode: 'next',
          reason: 'next',
          tabId: 101,
          tabTitle: 'Restored Tab',
          messageKey: 'reply-current',
          chunkIndex: 1,
          chunkCount: 2,
          text: '待機中です。',
          voiceProfile: 'irodori-v3',
          referenceVoice: 'sample',
          voicePrompt: '',
          audioUrl: null,
        },
      ],
      currentItem: {
        id: 'q-current-1',
        mode: 'auto',
        reason: 'auto',
        tabId: 101,
        tabTitle: 'Restored Tab',
        messageKey: 'reply-current',
        chunkIndex: 0,
        chunkCount: 2,
        text: '生成中でした。',
        voiceProfile: 'irodori-v3',
        referenceVoice: 'sample',
        voicePrompt: '',
        audioUrl: null,
      },
      lastPlayedItem: null,
      seq: 3,
    },
  });
  harness.setControl({ commands: [], conversationEvents: [] });

  await harness.ready();

  await waitFor(() => harness.stopPosts.length === 1 && harness.speakPosts.length === 1);
  assert.equal(harness.speakPosts[0].text, '生成中でした。');
});

test('API recovery retries browser-runtime hydration and resumes the persisted queue', async () => {
  const harness = createHarness({
    browserRuntimeError: 'local API unavailable',
    browserRuntime: {
      tabs: [
        {
          id: 101,
          title: 'Restored Tab',
          url: 'https://chatgpt.com/c/101',
          lastReadIndex: 0,
          lastAutoQueueSignature: 'reply-restored\u0000復元キューです。',
          lastAssistantMessage: {
            messageKey: 'reply-restored',
            chunks: ['復元キューです。'],
            capturedAt: 10,
          },
        },
      ],
      selectedTabId: 101,
      uiOwnerTabId: 101,
      queue: [
        {
          id: 'q-restored-1',
          mode: 'auto',
          reason: 'auto',
          tabId: 101,
          tabTitle: 'Restored Tab',
          messageKey: 'reply-restored',
          chunkIndex: 0,
          chunkCount: 1,
          text: '復元キューです。',
          voiceProfile: 'irodori-v3',
          referenceVoice: 'sample',
          voicePrompt: '',
          audioUrl: null,
        },
      ],
      currentItem: null,
      lastPlayedItem: null,
      seq: 2,
    },
  });
  harness.setControl({ commands: [], conversationEvents: [] });

  await harness.ready();
  assert.equal(harness.speakPosts.length, 0);

  harness.setBrowserRuntimeError(null);
  const recovered = await harness.sendAsync({ type: 'external-control-poll' }, 101, 'Restored Tab');

  assert.equal(recovered.ok, true);
  await waitFor(() => harness.speakPosts.length === 1);
  assert.equal(harness.speakPosts[0].text, '復元キューです。');
});

test('control polling uses one recovery sweep instead of repeated all-tab rechecks', async () => {
  const harness = createHarness();
  await harness.ready();
  harness.send({ type: 'register-tab', title: 'Tab A' }, 101, 'Tab A');
  harness.send({ type: 'register-tab', title: 'Tab B' }, 202, 'Tab B');
  harness.sentMessages.length = 0;

  const first = await harness.sendAsync({ type: 'external-control-poll' }, 101, 'Tab A');
  const second = await harness.sendAsync({ type: 'external-control-poll' }, 101, 'Tab A');

  assert.equal(first.ok, true);
  assert.equal(second.ok, true);
  assert.deepEqual(
    harness.sentMessages
      .filter((entry) => entry.message.type === 'auto-recheck')
      .map((entry) => entry.tabId)
      .sort((a, b) => a - b),
    [101, 202],
  );
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
  assert.deepEqual(
    JSON.parse(JSON.stringify(harness.sentMessages.filter((entry) => entry.message.type === 'settings-update'))),
    [{
      tabId: 101,
      message: {
        type: 'settings-update',
        payload: {
          enabled: true,
          voiceVolume: 0.25,
          voiceId: 'asuka',
          referenceVoice: 'asuka',
          micConversationEnabled: true,
        },
      },
    }],
  );
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
  assert.equal(harness.statePosts.at(-1).supportsExtensionReload, true);

  await harness.sendAsync({ type: 'external-control-poll' }, 101, 'Tab A');
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(harness.speakPosts.length, 1);
});

test('extension reload command is acknowledged before reloading and is not replayed', async () => {
  const harness = createHarness();
  harness.setControl({ commands: [{ id: 21, command: 'reload_extension' }], conversationEvents: [] });

  const first = await harness.sendAsync({ type: 'external-control-poll' }, 101, 'Tab A');

  assert.equal(first.ok, true);
  assert.deepEqual(harness.lifecycleEvents, ['ack', 'reload']);
  assert.equal(harness.runtimeReloads(), 1);
  assert.deepEqual(harness.ackPosts.at(-1), { consumerId: 'playback-id', commandId: 21 });

  await harness.sendAsync({ type: 'external-control-poll' }, 101, 'Tab A');
  assert.equal(harness.runtimeReloads(), 1);
});

test('extension reload does not run until its command can be acknowledged', async () => {
  const harness = createHarness();
  harness.setControl({ commands: [{ id: 22, command: 'reload_extension' }], conversationEvents: [] });
  harness.setAckError('ack temporarily unavailable');

  const failed = await harness.sendAsync({ type: 'external-control-poll' }, 101, 'Tab A');

  assert.equal(failed.ok, false);
  assert.equal(harness.runtimeReloads(), 0);

  harness.setAckError(null);
  const recovered = await harness.sendAsync({ type: 'external-control-poll' }, 101, 'Tab A');
  assert.equal(recovered.ok, true);
  assert.equal(harness.runtimeReloads(), 1);
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
    referenceVoiceExplicit: true,
    micConversationEnabled: false,
    sttModel: 'small',
    cancelGraceMs: 700,
    initialized: true,
  });
});

test('a legacy empty external reference cannot erase a selected Chrome reference voice', async () => {
  const harness = createHarness();
  harness.setControl({
    commands: [],
    settingsRevision: 4,
    settings: {
      ...harness.control().settings,
      referenceVoice: '',
      referenceVoiceExplicit: false,
    },
  });

  const result = await harness.sendAsync({ type: 'external-control-poll' }, 101, 'Tab A');

  assert.equal(result.ok, true);
  assert.equal(harness.storage.referenceVoice, 'sample');
  assert.equal(harness.storage.voiceId, 'sample');
  assert.ok(harness.settingsPosts.some((body) => (
    body.referenceVoice === 'sample' && body.referenceVoiceExplicit === true
  )));
});

test('durable poll uses a stored consumer, independent event cursor, and ACKs only after handling', async () => {
  const harness = createHarness();
  harness.send({ type: 'register-tab', title: 'Tab A' }, 101, 'Tab A');
  harness.setTabResponder(101, (message) => (
    message.type === 'voice-transcript'
      ? { ok: true, alreadyApplied: true }
      : { ok: true, composerAvailable: true, composerFocused: true, documentFocused: true, visible: true, url: 'https://chatgpt.com/c/101' }
  ));
  harness.setControl({
    commands: [{ id: 7, command: 'next' }],
    conversationEvents: [{
      id: 11,
      type: 'transcript',
      payload: { sessionId: 5, text: '再送されても一度だけ', deliveryId: 'delivery-11', cancelGraceMs: 0 },
    }],
  });

  const result = await harness.sendAsync({ type: 'external-control-poll' }, 101, 'Tab A');

  assert.equal(result.ok, true);
  assert.equal(harness.storage.bridgeConsumerId, 'playback-id');
  assert.deepEqual(harness.ackPosts, [
    { consumerId: 'playback-id', commandId: 7 },
    { consumerId: 'playback-id', conversationEventId: 11 },
  ]);
  const transcript = harness.sentMessages.find(({ message }) => message.type === 'voice-transcript');
  assert.equal(transcript.message.payload.deliveryId, 'delivery-11');

  await harness.sendAsync({ type: 'external-control-poll' }, 101, 'Tab A');
  assert.equal(harness.sentMessages.filter(({ message }) => message.type === 'voice-transcript').length, 1);
});

test('failed ACK leaves a command retryable and an acknowledged item survives a service-worker restart', async () => {
  const harness = createHarness();
  harness.send({ type: 'register-tab', title: 'Tab A' }, 101, 'Tab A');
  harness.setControl({ commands: [{ id: 9, command: 'stop' }], conversationEvents: [] });
  harness.setAckError('ack temporarily unavailable');

  const failed = await harness.sendAsync({ type: 'external-control-poll' }, 101, 'Tab A');
  assert.equal(failed.ok, false);
  assert.equal(harness.ackPosts.length, 0);

  harness.setAckError(null);
  const recovered = await harness.sendAsync({ type: 'external-control-poll' }, 101, 'Tab A');
  assert.equal(recovered.ok, true);
  assert.deepEqual(harness.ackPosts.at(-1), { consumerId: 'playback-id', commandId: 9 });

  harness.reloadBackground();
  const afterRestart = await harness.sendAsync({ type: 'external-control-poll' }, 101, 'Tab A');
  assert.equal(afterRestart.ok, true);
  assert.equal(harness.ackPosts.filter((item) => item.commandId === 9).length, 1);
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

test('captured conversation target survives a service-worker restart before transcript delivery', async () => {
  const sharedStorage = {};
  const harness = createHarness({
    storage: sharedStorage,
    openTabs: [
      { id: 101, title: 'Tab A', url: 'https://chatgpt.com/c/101' },
      { id: 202, title: 'Tab B', url: 'https://chatgpt.com/c/202' },
    ],
  });
  harness.send({ type: 'register-tab', title: 'Tab A' }, 101, 'Tab A');
  harness.send({ type: 'register-tab', title: 'Tab B' }, 202, 'Tab B');
  harness.setTabResponder(101, (message) => {
    if (message.type === 'conversation-target-status') {
      return { ok: true, composerAvailable: true, composerFocused: false, documentFocused: true, visible: true, url: 'https://chatgpt.com/c/101' };
    }
    return { ok: true };
  });
  harness.setTabResponder(202, (message) => {
    if (message.type === 'conversation-target-status') {
      return { ok: true, composerAvailable: true, composerFocused: true, documentFocused: true, visible: true, url: 'https://chatgpt.com/c/202' };
    }
    return { ok: true };
  });
  harness.setControl({
    commands: [],
    conversationEvents: [{ id: 1, type: 'cancel_pending', payload: { sessionId: 77 } }],
  });

  const captured = await harness.sendAsync({ type: 'external-control-poll' }, 101, 'Tab A');
  assert.equal(captured.ok, true);
  await waitFor(() => (
    Array.isArray(harness.browserRuntime().conversationSessions)
    && harness.browserRuntime().conversationSessions.some((item) => item.sessionId === 77 && item.tabId === 202)
  ));

  harness.reloadBackground();
  await harness.ready();
  harness.setTabResponder(101, (message) => {
    if (message.type === 'conversation-target-status') {
      return { ok: true, composerAvailable: true, composerFocused: true, documentFocused: true, visible: true, url: 'https://chatgpt.com/c/101' };
    }
    return { ok: true };
  });
  harness.setTabResponder(202, (message) => {
    if (message.type === 'conversation-target-status') {
      return { ok: true, composerAvailable: true, composerFocused: false, documentFocused: true, visible: true, url: 'https://chatgpt.com/c/202' };
    }
    return { ok: true };
  });
  harness.setControl({
    commands: [],
    conversationEvents: [
      {
        id: 2,
        type: 'transcript',
        payload: { sessionId: 77, text: '再起動後も固定先です', deliveryId: 'delivery-session-77', cancelGraceMs: 700 },
      },
    ],
  });

  const delivered = await harness.sendAsync({ type: 'external-control-poll' }, 101, 'Tab A');
  assert.equal(delivered.ok, true);
  const transcriptMessages = harness.sentMessages.filter((entry) => entry.message.type === 'voice-transcript');
  assert.equal(transcriptMessages.at(-1).tabId, 202);
  assert.equal(transcriptMessages.at(-1).message.payload.deliveryId, 'delivery-session-77');
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

test('a permanent transcript rejection is ACKed and does not poison later control polling', async () => {
  const harness = createHarness();
  harness.send({ type: 'register-tab', title: 'Tab A' }, 101, 'Tab A');
  harness.setTabResponder(101, (message) => {
    if (message.type === 'conversation-target-status') {
      return { ok: true, composerAvailable: true, composerFocused: true, documentFocused: true, visible: true };
    }
    if (message.type === 'voice-transcript') return { ok: false, reason: 'composer-not-empty' };
    return { ok: true };
  });
  harness.setControl({
    commands: [],
    conversationEvents: [
      { id: 1, type: 'cancel_pending', payload: { sessionId: 21 } },
      { id: 2, type: 'transcript', payload: { sessionId: 21, text: '既存入力を上書きしない', cancelGraceMs: 700 } },
      { id: 3, type: 'cancel_pending', payload: { sessionId: 22 } },
    ],
  });

  const result = await harness.sendAsync({ type: 'external-control-poll' }, 101, 'Tab A');

  assert.equal(result.ok, true);
  assert.deepEqual(harness.ackPosts.filter((item) => item.conversationEventId).map((item) => item.conversationEventId), [1, 2, 3]);
  assert.equal(harness.conversationStatePosts.at(-1).error, 'composer-not-empty');
  assert.equal(harness.statePosts.length, 1);
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
  await harness.ready();
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
