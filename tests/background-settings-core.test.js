'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const settingsCore = require('../extension/background-settings-core.js');

test('settings sanitization preserves the preview UX and removes legacy browser UI state', () => {
  const result = settingsCore.sanitizeSettings({
    enabled: true,
    previewMaxLines: 999,
    previewMaxChars: 2,
    referenceVoice: 'asuka',
    voicePrompt: 'stale',
    panelPosition: { x: 1, y: 2 },
  });

  assert.equal(result.enabled, true);
  assert.equal(result.previewMaxLines, 20);
  assert.equal(result.previewMaxChars, 40);
  assert.equal(result.referenceVoice, 'asuka');
  assert.equal(result.voiceId, 'asuka');
  assert.equal(result.voicePrompt, '');
  assert.equal(Object.hasOwn(result, 'panelPosition'), false);
});

test('explicit none is distinct from a legacy missing external reference', () => {
  assert.equal(
    settingsCore.externalReferenceSelection({ referenceVoice: '', referenceVoiceExplicit: true }, 'asuka'),
    '',
  );
  assert.equal(settingsCore.externalReferenceSelection({}, 'asuka'), 'asuka');
  assert.equal(
    settingsCore.legacyExternalReferenceNeedsRepair({ referenceVoice: '' }, 'asuka'),
    true,
  );
});

test('external settings plan reports exact persistence and pet-sync changes', () => {
  const unchanged = settingsCore.planExternalSettings(
    { enabled: true, voiceVolume: 0.6, referenceVoice: 'asuka', micConversationEnabled: false },
    { enabled: true, voiceVolume: 0.6, referenceVoice: 'asuka', referenceVoiceExplicit: true },
  );
  const changed = settingsCore.planExternalSettings(
    { enabled: false, voiceVolume: 0.6, referenceVoice: 'asuka', micConversationEnabled: false },
    { enabled: true, voiceVolume: 2, referenceVoice: 'mika', referenceVoiceExplicit: true },
  );

  assert.equal(unchanged.changed, false);
  assert.equal(unchanged.changedReference, false);
  assert.equal(changed.changed, true);
  assert.equal(changed.changedReference, true);
  assert.equal(changed.next.voiceVolume, 1);
  assert.equal(changed.effectiveSettings.referenceVoice, 'mika');
});

test('invalid STT and cancellation values return documented safe defaults', () => {
  assert.equal(settingsCore.normalizeSttModel('unknown'), 'small');
  assert.equal(settingsCore.normalizeSttModel('large-v3-turbo'), 'large-v3-turbo');
  assert.equal(settingsCore.normalizeCancelGraceMs(-100), 0);
  assert.equal(settingsCore.normalizeCancelGraceMs(99999), 5000);
});
