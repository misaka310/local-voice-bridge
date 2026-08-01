'use strict';

(function exposeBackgroundSettingsCore(root, factory) {
  const backgroundCore = typeof module === 'object' && module.exports
    ? require('./background-core.js')
    : root.BackgroundCore;
  const api = factory(backgroundCore);
  if (typeof module === 'object' && module.exports) {
    module.exports = api;
    return;
  }
  root.BackgroundSettingsCore = api;
})(typeof globalThis !== 'undefined' ? globalThis : self, (backgroundCore) => {
  if (!backgroundCore || typeof backgroundCore.normalizeReferenceVoice !== 'function') {
    throw new Error('background-core.js must be loaded before background-settings-core.js');
  }

  const SETTINGS_VERSION = 11;
  const DEFAULT_SETTINGS = Object.freeze({
    settingsVersion: SETTINGS_VERSION,
    enabled: false,
    apiUrl: 'http://127.0.0.1:8717/v1/speak',
    healthUrl: 'http://127.0.0.1:8717/health',
    voiceProfile: 'irodori-v3',
    voiceId: '',
    referenceVoice: '',
    voicePrompt: '',
    voiceVolume: 0.6,
    previewMaxLines: 2,
    previewMaxChars: 80,
    previewMinChars: 40,
    previewStableMs: 1000,
    micConversationEnabled: false,
    sttModel: 'small',
    cancelGraceMs: 700,
    liveTtsProfile: 'speed',
  });
  const LEGACY_BROWSER_UI_STORAGE_KEYS = Object.freeze([
    'petMode',
    'selectedPetId',
    'petPosition',
    'panelPosition',
    'panelCollapsed',
  ]);

  function normalizeModel(_value) {
    return DEFAULT_SETTINGS.voiceProfile;
  }

  function normalizeStoredReference(value) {
    const normalized = backgroundCore.normalizeReferenceVoice(value);
    if (!normalized || ['qwen3', 'qwen', 'none'].includes(normalized.toLowerCase())) return '';
    return normalized;
  }

  function storedReferenceVoice(raw) {
    const safe = raw && typeof raw === 'object' ? raw : {};
    const voiceId = normalizeStoredReference(safe.voiceId);
    return voiceId || normalizeStoredReference(safe.referenceVoice);
  }

  function clampInteger(value, fallback, minimum, maximum) {
    if (value === '' || value === null || value === undefined) return fallback;
    const number = Number(value);
    if (!Number.isFinite(number)) return fallback;
    return Math.min(maximum, Math.max(minimum, Math.round(number)));
  }

  function normalizeSttModel(value) {
    const normalized = String(value || '').trim();
    return ['small', 'medium', 'large-v3-turbo'].includes(normalized)
      ? normalized
      : DEFAULT_SETTINGS.sttModel;
  }

  function normalizeCancelGraceMs(value) {
    return clampInteger(value, DEFAULT_SETTINGS.cancelGraceMs, 0, 5000);
  }

  function normalizeLiveTtsProfile(value) {
    const normalized = String(value || '').trim().toLowerCase();
    return ['speed', 'balanced', 'bridge'].includes(normalized)
      ? normalized
      : DEFAULT_SETTINGS.liveTtsProfile;
  }

  function sanitizeSettings(raw = {}) {
    const safe = raw && typeof raw === 'object' ? raw : {};
    const referenceVoice = storedReferenceVoice(safe);
    const sanitized = {
      ...DEFAULT_SETTINGS,
      ...safe,
      settingsVersion: SETTINGS_VERSION,
      model: DEFAULT_SETTINGS.voiceProfile,
      voiceId: referenceVoice,
      voiceProfile: normalizeModel(safe.model || safe.voiceProfile),
      referenceVoice,
      voicePrompt: '',
      previewMaxLines: clampInteger(safe.previewMaxLines, DEFAULT_SETTINGS.previewMaxLines, 1, 20),
      previewMaxChars: clampInteger(safe.previewMaxChars, DEFAULT_SETTINGS.previewMaxChars, 40, 1000),
      sttModel: normalizeSttModel(safe.sttModel),
      cancelGraceMs: normalizeCancelGraceMs(safe.cancelGraceMs),
      liveTtsProfile: normalizeLiveTtsProfile(safe.liveTtsProfile),
    };
    for (const key of LEGACY_BROWSER_UI_STORAGE_KEYS) delete sanitized[key];
    return sanitized;
  }

  function externalReferenceSelection(remote, fallbackValue = '') {
    const safe = remote && typeof remote === 'object' ? remote : {};
    const hasReference = Object.prototype.hasOwnProperty.call(safe, 'voiceId')
      || Object.prototype.hasOwnProperty.call(safe, 'referenceVoice');
    if (!hasReference) return normalizeStoredReference(fallbackValue);
    const referenceVoice = storedReferenceVoice(safe);
    return referenceVoice || Boolean(safe.referenceVoiceExplicit)
      ? referenceVoice
      : normalizeStoredReference(fallbackValue);
  }

  function legacyExternalReferenceNeedsRepair(remote, fallbackValue = '') {
    const safe = remote && typeof remote === 'object' ? remote : {};
    const hasReference = Object.prototype.hasOwnProperty.call(safe, 'voiceId')
      || Object.prototype.hasOwnProperty.call(safe, 'referenceVoice');
    return Boolean(
      hasReference
      && !storedReferenceVoice(safe)
      && !safe.referenceVoiceExplicit
      && normalizeStoredReference(fallbackValue),
    );
  }

  function planExternalSettings(currentValue, remoteValue) {
    const current = sanitizeSettings(currentValue);
    const remote = remoteValue && typeof remoteValue === 'object' ? remoteValue : {};
    const referenceVoice = externalReferenceSelection(remote, current.referenceVoice);
    const next = {
      enabled: Object.prototype.hasOwnProperty.call(remote, 'enabled')
        ? Boolean(remote.enabled)
        : Boolean(current.enabled),
      voiceVolume: Object.prototype.hasOwnProperty.call(remote, 'voiceVolume')
        ? Math.min(1, Math.max(0, Number(remote.voiceVolume) || 0))
        : Number(current.voiceVolume),
      voiceId: referenceVoice,
      referenceVoice,
      micConversationEnabled: Object.prototype.hasOwnProperty.call(remote, 'micConversationEnabled')
        ? Boolean(remote.micConversationEnabled)
        : Boolean(current.micConversationEnabled),
    };
    const changedReference = normalizeStoredReference(current.referenceVoice) !== referenceVoice;
    const changed = Boolean(current.enabled) !== next.enabled
      || Number(current.voiceVolume) !== next.voiceVolume
      || changedReference
      || normalizeStoredReference(current.voiceId) !== referenceVoice
      || Boolean(current.micConversationEnabled) !== next.micConversationEnabled;
    return {
      changed,
      changedReference,
      next,
      referenceVoice,
      effectiveSettings: sanitizeSettings({ ...current, ...next }),
    };
  }

  return {
    DEFAULT_SETTINGS,
    LEGACY_BROWSER_UI_STORAGE_KEYS,
    SETTINGS_VERSION,
    clampInteger,
    externalReferenceSelection,
    legacyExternalReferenceNeedsRepair,
    normalizeCancelGraceMs,
    normalizeLiveTtsProfile,
    normalizeModel,
    normalizeStoredReference,
    normalizeSttModel,
    planExternalSettings,
    sanitizeSettings,
    storedReferenceVoice,
  };
});
