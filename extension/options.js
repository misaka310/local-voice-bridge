'use strict';

(() => {
  const SETTINGS_VERSION = 11;
  const DEFAULTS = Object.freeze({
    settingsVersion: SETTINGS_VERSION,
    previewMaxLines: 2,
    previewMaxChars: 80,
    sttModel: 'small',
    cancelGraceMs: 700,
    liveTtsProfile: 'speed',
  });
  const STT_MODELS = new Set(['small', 'medium', 'large-v3-turbo']);
  const LIVE_TTS_PROFILES = new Set(['speed', 'balanced', 'bridge']);

  function clampInteger(value, fallback, minimum, maximum) {
    if (value === '' || value === null || value === undefined) return fallback;
    const number = Number(value);
    if (!Number.isFinite(number)) return fallback;
    return Math.min(maximum, Math.max(minimum, Math.round(number)));
  }

  function normalizeSettings(raw = {}) {
    const sttModel = String(raw.sttModel || '').trim();
    return {
      settingsVersion: SETTINGS_VERSION,
      previewMaxLines: clampInteger(raw.previewMaxLines, DEFAULTS.previewMaxLines, 1, 20),
      previewMaxChars: clampInteger(raw.previewMaxChars, DEFAULTS.previewMaxChars, 40, 1000),
      sttModel: STT_MODELS.has(sttModel) ? sttModel : DEFAULTS.sttModel,
      cancelGraceMs: clampInteger(raw.cancelGraceMs, DEFAULTS.cancelGraceMs, 0, 5000),
      liveTtsProfile: LIVE_TTS_PROFILES.has(String(raw.liveTtsProfile || '').trim().toLowerCase())
        ? String(raw.liveTtsProfile).trim().toLowerCase()
        : DEFAULTS.liveTtsProfile,
    };
  }

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { DEFAULTS, SETTINGS_VERSION, normalizeSettings };
  }

  if (typeof document === 'undefined' || typeof chrome === 'undefined' || !chrome.storage?.local) return;

  const form = document.getElementById('settings-form');
  const linesInput = document.getElementById('preview-max-lines');
  const charsInput = document.getElementById('preview-max-chars');
  const sttModelSelect = document.getElementById('stt-model');
  const graceInput = document.getElementById('cancel-grace-seconds');
  const liveTtsProfileSelect = document.getElementById('live-tts-profile');
  const resetButton = document.getElementById('reset-button');
  const status = document.getElementById('save-status');
  let statusTimer = null;

  function setStatus(message, isError = false) {
    if (statusTimer) clearTimeout(statusTimer);
    status.textContent = String(message || '');
    status.classList.toggle('error', Boolean(isError));
    if (message && !isError) {
      statusTimer = setTimeout(() => {
        status.textContent = '';
      }, 3500);
    }
  }

  function render(values) {
    const normalized = normalizeSettings(values);
    linesInput.value = String(normalized.previewMaxLines);
    charsInput.value = String(normalized.previewMaxChars);
    sttModelSelect.value = normalized.sttModel;
    graceInput.value = (normalized.cancelGraceMs / 1000).toFixed(1);
    liveTtsProfileSelect.value = normalized.liveTtsProfile;
  }

  function readForm() {
    return normalizeSettings({
      previewMaxLines: linesInput.value,
      previewMaxChars: charsInput.value,
      sttModel: sttModelSelect.value,
      cancelGraceMs: Number(graceInput.value) * 1000,
      liveTtsProfile: liveTtsProfileSelect.value,
    });
  }

  async function syncRuntimeSettings() {
    try {
      await chrome.runtime.sendMessage({ type: 'options-settings-updated' });
    } catch (_error) {
      // The saved browser settings remain valid even while the local API is stopped.
    }
  }

  async function save(values, message = '設定を保存しました') {
    const normalized = normalizeSettings(values);
    await chrome.storage.local.set(normalized);
    render(normalized);
    await syncRuntimeSettings();
    setStatus(message);
    return normalized;
  }

  async function load() {
    try {
      const stored = await chrome.storage.local.get(DEFAULTS);
      const normalized = normalizeSettings(stored);
      render(normalized);
      if (Object.keys(normalized).some((key) => stored[key] !== normalized[key])) {
        await chrome.storage.local.set(normalized);
      }
    } catch (error) {
      setStatus(`設定を読み込めませんでした: ${error.message || String(error)}`, true);
    }
  }

  form.addEventListener('submit', (event) => {
    event.preventDefault();
    save(readForm()).catch((error) => {
      setStatus(`保存できませんでした: ${error.message || String(error)}`, true);
    });
  });

  resetButton.addEventListener('click', () => {
    save(DEFAULTS, '初期値に戻しました').catch((error) => {
      setStatus(`初期値に戻せませんでした: ${error.message || String(error)}`, true);
    });
  });

  for (const control of [linesInput, charsInput, sttModelSelect, graceInput, liveTtsProfileSelect]) {
    control.addEventListener('input', () => setStatus(''));
    control.addEventListener('change', () => setStatus(''));
  }

  void load();
})();
