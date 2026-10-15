'use strict';

(function initContentSettings(global) {
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
  const DEFAULT_PET_ID = 'placeholder';
  const LEGACY_BROWSER_UI_STORAGE_KEYS = Object.freeze([
    'petMode',
    'selectedPetId',
    'petPosition',
    'panelPosition',
    'panelCollapsed',
  ]);

  function clampVolume(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return DEFAULT_SETTINGS.voiceVolume;
    return Math.min(1, Math.max(0, n));
  }

  function normalizeVoiceId(value) {
    return String(value || '').trim();
  }

  function resolveDesktopPetId(value) {
    const petId = normalizeVoiceId(value).toLowerCase();
    if (!petId || petId === 'none' || petId === '.' || petId === '..' || /[\\/]/.test(petId)) {
      return DEFAULT_PET_ID;
    }
    return petId;
  }

  function normalizeReferenceVoice(value) {
    const normalized = String(value || '').trim();
    if (!normalized || ['none', 'qwen3', 'qwen'].includes(normalized.toLowerCase())) return '';
    return normalized;
  }

  function storedReferenceVoice(raw) {
    const voiceId = normalizeReferenceVoice(raw && raw.voiceId);
    if (voiceId) return voiceId;
    return normalizeReferenceVoice(raw && raw.referenceVoice);
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

  async function sanitizeStoredSettings(raw, chromeObject = global.chrome) {
    const source = raw && typeof raw === 'object' ? raw : {};
    const referenceVoice = storedReferenceVoice(source);
    const next = {
      ...DEFAULT_SETTINGS,
      ...source,
      settingsVersion: SETTINGS_VERSION,
      model: DEFAULT_SETTINGS.voiceProfile,
      voiceId: referenceVoice,
      voiceProfile: DEFAULT_SETTINGS.voiceProfile,
      referenceVoice,
      voicePrompt: '',
      previewMaxLines: clampInteger(source.previewMaxLines, DEFAULT_SETTINGS.previewMaxLines, 1, 20),
      previewMaxChars: clampInteger(source.previewMaxChars, DEFAULT_SETTINGS.previewMaxChars, 40, 1000),
      sttModel: normalizeSttModel(source.sttModel),
      cancelGraceMs: normalizeCancelGraceMs(source.cancelGraceMs),
      liveTtsProfile: normalizeLiveTtsProfile(source.liveTtsProfile),
    };
    for (const key of LEGACY_BROWSER_UI_STORAGE_KEYS) delete next[key];
    if (chromeObject && chromeObject.storage && chromeObject.storage.local) {
      await chromeObject.storage.local.set(next);
      await chromeObject.storage.local.remove(LEGACY_BROWSER_UI_STORAGE_KEYS);
    }
    return next;
  }

  global.LocalVoiceContentSettings = Object.freeze({
    SETTINGS_VERSION,
    DEFAULT_SETTINGS,
    DEFAULT_PET_ID,
    LEGACY_BROWSER_UI_STORAGE_KEYS,
    clampVolume,
    normalizeVoiceId,
    resolveDesktopPetId,
    normalizeReferenceVoice,
    storedReferenceVoice,
    clampInteger,
    normalizeSttModel,
    normalizeCancelGraceMs,
    normalizeLiveTtsProfile,
    sanitizeStoredSettings,
  });
})(globalThis);
