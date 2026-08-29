'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { create } = require('../extension/background-control-sync.js');

test('completion recovery sweep stays enabled when Auto reading is off', async () => {
  const heartbeatCalls = [];
  const previousPollPolicy = globalThis.BackgroundControlPollPolicy;
  globalThis.BackgroundControlPollPolicy = { intervalMs: () => 5000 };

  try {
    const sync = create({
      chrome: {
        storage: {
          local: {
            async get() { return {}; },
            async set() {},
          },
        },
        runtime: {
          reload() {},
          async openOptionsPage() {},
        },
        tabs: { async sendMessage() { return { ok: true }; } },
      },
      crypto: { randomUUID: () => 'consumer-test' },
      settingsCore: {
        sanitizeSettings: (value) => ({ ...value }),
        normalizeStoredReference: (value) => String(value || ''),
        normalizeSttModel: (value) => String(value || 'small'),
        normalizeCancelGraceMs: (value) => Number(value ?? 700),
      },
      conversationSessionTargets: new Map(),
      conversationSessionTargetLocations: new Map(),
      tabs: new Map([[101, {}]]),
      pushMessageToRegisteredTabs: async () => {},
      requestAutoRecheckForRegisteredTabs: (enabled) => { heartbeatCalls.push(Boolean(enabled)); },
      statePublisher: { async publishIfNeeded() {} },
      recoverExpiredPlayback() {},
      async hydrateBrowserRuntime() {},
      async getSettings() {
        return {
          enabled: false,
          referenceVoice: '',
          sttModel: 'small',
          cancelGraceMs: 700,
          micConversationEnabled: false,
        };
      },
      async controlPanelRequest(_settings, path) {
        if (path === '/v1/control-panel/poll') {
          return {
            ok: true,
            initialized: true,
            commands: [],
            conversationEvents: [],
            conversation: { phase: 'off' },
          };
        }
        throw new Error(`unexpected request: ${path}`);
      },
      setConversationPhase() {},
      getConversationPhase: () => 'off',
      ensureOwner() {},
      externalStateSnapshot: () => ({ tabs: [] }),
      async reconnectOpenChatGptTabs() {},
      rememberReferenceVoice: (value) => value,
    });

    await sync.synchronize();
    assert.deepEqual(heartbeatCalls, [true]);
  } finally {
    if (previousPollPolicy === undefined) delete globalThis.BackgroundControlPollPolicy;
    else globalThis.BackgroundControlPollPolicy = previousPollPolicy;
  }
});
