'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const ROOT = path.resolve(__dirname, '..');
const settingsCore = require('../extension/background-settings-core.js');
const options = require('../extension/options.js');
require('../extension/background-message-router.js');


test('Local API advanced runtime settings overwrite the browser mirror when the server exposes the new ownership contract', () => {
  const current = settingsCore.sanitizeSettings({
    enabled: false,
    micConversationEnabled: false,
    sttModel: 'small',
    cancelGraceMs: 700,
    liveTtsProfile: 'speed',
  });
  const plan = settingsCore.planExternalSettings(current, {
    enabled: true,
    micConversationEnabled: true,
    sttModel: 'medium',
    cancelGraceMs: 1200,
    liveTtsProfile: 'bridge',
  });

  assert.equal(plan.next.sttModel, 'medium');
  assert.equal(plan.next.cancelGraceMs, 1200);
  assert.equal(plan.next.liveTtsProfile, 'bridge');
  assert.equal(plan.effectiveSettings.sttModel, 'medium');
  assert.equal(plan.effectiveSettings.cancelGraceMs, 1200);
  assert.equal(plan.effectiveSettings.liveTtsProfile, 'bridge');
});


test('legacy Local API snapshots do not overwrite browser runtime settings before the ownership migration exists', () => {
  const current = settingsCore.sanitizeSettings({
    sttModel: 'small',
    cancelGraceMs: 700,
    liveTtsProfile: 'speed',
  });
  const plan = settingsCore.planExternalSettings(current, {
    sttModel: 'medium',
    cancelGraceMs: 1200,
  });

  assert.equal(plan.next.sttModel, 'small');
  assert.equal(plan.next.cancelGraceMs, 700);
  assert.equal(plan.next.liveTtsProfile, 'speed');
});


test('browser options own only preview limits', () => {
  const html = fs.readFileSync(path.join(ROOT, 'extension', 'options.html'), 'utf8');
  assert.doesNotMatch(html, /id="stt-model"/);
  assert.doesNotMatch(html, /id="cancel-grace-seconds"/);
  assert.doesNotMatch(html, /id="live-tts-profile"/);

  const normalized = options.normalizeSettings({
    previewMaxLines: 9,
    previewMaxChars: 420,
    sttModel: 'large-v3-turbo',
    cancelGraceMs: 2400,
    liveTtsProfile: 'bridge',
  });
  assert.deepEqual(normalized, {
    settingsVersion: options.SETTINGS_VERSION,
    previewMaxLines: 9,
    previewMaxChars: 420,
  });
});


test('options update refreshes browser preview settings without pushing runtime settings to Local API', async () => {
  let runtimePushes = 0;
  let browserRefreshes = 0;
  const router = globalThis.BackgroundMessageRouter.create({
    getSettings: async () => ({ previewMaxLines: 4, previewMaxChars: 180 }),
    pushOptionSettings: async () => {
      runtimePushes += 1;
      return { ok: true };
    },
    broadcastOptionSettings: async () => {
      browserRefreshes += 1;
      return { ok: true };
    },
  });

  const response = await new Promise((resolve) => {
    const pending = router({ type: 'options-settings-updated' }, {}, resolve);
    assert.equal(pending, true);
  });

  assert.equal(response.ok, true);
  assert.equal(browserRefreshes, 1);
  assert.equal(runtimePushes, 0);
});


test('external state context derives Auto scope, manual target and playback source from existing state', () => {
  const modulePath = path.join(ROOT, 'extension', 'background-external-state.js');
  assert.equal(fs.existsSync(modulePath), true, 'background-external-state.js must provide focused derivation');
  const { buildContext } = require(modulePath);
  const tabs = new Map([
    [11, { title: 'Tab A' }],
    [22, { title: 'Tab B' }],
    [33, { title: 'Tab C' }],
  ]);

  assert.deepEqual(buildContext({
    tabs,
    manualTargetTabId: 22,
    currentItem: { tabId: 11, tabTitle: 'Tab A' },
    lastPlayedItem: { tabId: 33, tabTitle: 'Tab C' },
  }), {
    autoScopeTabs: 3,
    manualTargetTabId: 22,
    manualTargetTitle: 'Tab B',
    playbackSourceTabId: 11,
    playbackSourceTitle: 'Tab A',
  });

  assert.deepEqual(buildContext({
    tabs,
    manualTargetTabId: 22,
    currentItem: null,
    lastPlayedItem: { tabId: 33, tabTitle: 'Tab C' },
  }), {
    autoScopeTabs: 3,
    manualTargetTabId: 22,
    manualTargetTitle: 'Tab B',
    playbackSourceTabId: 33,
    playbackSourceTitle: 'Tab C',
  });
});
