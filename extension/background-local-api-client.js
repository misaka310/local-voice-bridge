'use strict';

(function initBackgroundLocalApiClient(global) {
  function create(options = {}) {
    const fetchImpl = options.fetch;
    const getSettings = options.getSettings;
    const defaultSettings = options.defaultSettings || {};
    const normalizeStoredReference = options.normalizeStoredReference || ((value) => String(value || '').trim());

    async function speak(text, requestId, _voiceProfile, referenceVoice, _voicePrompt, playLocal = false) {
      const settings = await getSettings();
      const pickedProfile = defaultSettings.voiceProfile;
      const pickedReferenceVoice = normalizeStoredReference(referenceVoice !== undefined ? referenceVoice : settings.referenceVoice);
      const pickedVoicePrompt = '';
      const response = await fetchImpl(settings.apiUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          text,
          requestId,
          source: 'chatgpt-web',
          model: pickedProfile,
          voiceProfile: pickedProfile,
          voiceId: pickedReferenceVoice,
          referenceVoice: pickedReferenceVoice,
          voicePrompt: pickedVoicePrompt,
          instruct: pickedVoicePrompt,
          voiceVolume: Number(settings.voiceVolume),
          playLocal: playLocal === true,
        }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok || !body.ok) throw new Error(body.error || `HTTP ${response.status}`);
      const returnedReferenceVoice = normalizeStoredReference(body.referenceVoice ?? body.voiceId);
      const usedReferenceAudio = String(body.usedReferenceAudio || '').trim();
      if (pickedReferenceVoice && (returnedReferenceVoice !== pickedReferenceVoice || !usedReferenceAudio)) {
        throw new Error(`Reference voice was not applied: ${pickedReferenceVoice}`);
      }
      return body;
    }

    function isAllowedAudioUrl(targetUrl, settings) {
      try {
        const target = new URL(String(targetUrl || ''));
        if (!target.pathname.startsWith('/audio/')) return false;
        const allowedHosts = new Set(['127.0.0.1', 'localhost']);
        if (!allowedHosts.has(target.hostname)) return false;
        const candidates = [settings.apiUrl, settings.healthUrl]
          .map((value) => {
            try { return new URL(String(value || '')); } catch (_error) { return null; }
          })
          .filter(Boolean);
        return candidates.some((candidate) => allowedHosts.has(candidate.hostname)
          && candidate.protocol === target.protocol
          && candidate.port === target.port);
      } catch (_error) {
        return false;
      }
    }

    function arrayBufferToBase64(buffer) {
      const bytes = new Uint8Array(buffer);
      let binary = '';
      const chunkSize = 0x8000;
      for (let i = 0; i < bytes.length; i += chunkSize) {
        binary += String.fromCharCode(...bytes.subarray(i, i + chunkSize));
      }
      return btoa(binary);
    }

    async function fetchAudioPayload(url) {
      const targetUrl = String(url || '');
      const settings = await getSettings();
      if (!isAllowedAudioUrl(targetUrl, settings)) throw new Error('unsupported audio URL');
      const cacheBustedUrl = `${targetUrl}${targetUrl.includes('?') ? '&' : '?'}t=${Date.now()}`;
      const response = await fetchImpl(cacheBustedUrl, { cache: 'no-store' });
      if (!response.ok) throw new Error(`audio fetch failed: ${response.status}`);
      const contentType = response.headers.get('Content-Type') || 'audio/wav';
      const buffer = await response.arrayBuffer();
      if (!buffer || buffer.byteLength === 0) throw new Error('audio blob is empty');
      return { base64: arrayBufferToBase64(buffer), contentType, size: buffer.byteLength };
    }

    async function fetchReferenceVoices() {
      const settings = await getSettings();
      const candidates = [];
      try {
        const healthUrl = new URL(settings.healthUrl || defaultSettings.healthUrl);
        const refUrl = new URL(healthUrl.toString());
        refUrl.pathname = '/v1/reference-voices';
        refUrl.search = '';
        candidates.push(refUrl.toString(), healthUrl.toString());
      } catch (_error) {
        candidates.push('http://127.0.0.1:8717/v1/reference-voices', settings.healthUrl || defaultSettings.healthUrl);
      }
      for (const url of candidates) {
        try {
          const response = await fetchImpl(url, { cache: 'no-store' });
          const body = await response.json().catch(() => ({}));
          if (!response.ok || !body) continue;
          const voices = Array.isArray(body.voices) ? body.voices
            : Array.isArray(body.referenceVoices) ? body.referenceVoices
              : Array.isArray(body.availableReferenceVoices) ? body.availableReferenceVoices : [];
          return { ok: true, voices };
        } catch (_error) {}
      }
      return { ok: true, voices: [] };
    }

    async function syncDesktopPetSelection(petId) {
      const settings = await getSettings();
      const url = new URL(settings.healthUrl || defaultSettings.healthUrl);
      url.pathname = '/v1/desktop-pet';
      url.search = '';
      const response = await fetchImpl(url.toString(), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ petId: String(petId || 'placeholder') }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || !payload.ok) throw new Error(payload.error || `HTTP ${response.status}`);
      return payload;
    }

    function controlPanelUrl(settings, pathname, search = '') {
      const url = new URL(settings.healthUrl || defaultSettings.healthUrl);
      url.pathname = pathname;
      url.search = search;
      url.hash = '';
      return url.toString();
    }

    async function controlPanelRequest(settings, pathname, requestOptions = {}) {
      const response = await fetchImpl(controlPanelUrl(settings, pathname, requestOptions.search || ''), {
        method: requestOptions.method || 'GET',
        headers: requestOptions.body ? { 'Content-Type': 'application/json' } : undefined,
        body: requestOptions.body ? JSON.stringify(requestOptions.body) : undefined,
        cache: 'no-store',
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || !payload.ok) throw new Error(payload.error || `HTTP ${response.status}`);
      return payload;
    }

    async function replayLocalAudio(text = '') {
      const settings = await getSettings();
      return controlPanelRequest(settings, '/v1/playback/replay', {
        method: 'POST',
        body: { text: String(text || ''), voiceVolume: Number(settings.voiceVolume) },
      });
    }

    async function stopLocalAudio() {
      const settings = await getSettings();
      return controlPanelRequest(settings, '/v1/playback/stop', { method: 'POST', body: {} });
    }

    async function postConversationState(payload) {
      const settings = await getSettings();
      const safe = payload && typeof payload === 'object' ? payload : {};
      return controlPanelRequest(settings, '/v1/conversation/state', {
        method: 'POST',
        body: {
          phase: String(safe.phase || 'error'),
          statusText: String(safe.statusText || ''),
          sttDevice: String(safe.sttDevice || ''),
          sttModel: String(safe.sttModel || settings.sttModel || 'small'),
          error: String(safe.error || ''),
        },
      });
    }

    async function postNetworkDiagnostic(payload) {
      const settings = await getSettings();
      const safe = payload && typeof payload === 'object' ? payload : {};
      return controlPanelRequest(settings, '/v1/debug/chatgpt-network-event', {
        method: 'POST',
        body: safe,
      });
    }

    return {
      speak,
      fetchAudioPayload,
      fetchReferenceVoices,
      syncDesktopPetSelection,
      controlPanelUrl,
      controlPanelRequest,
      replayLocalAudio,
      stopLocalAudio,
      postConversationState,
      postNetworkDiagnostic,
    };
  }

  global.BackgroundLocalApiClient = Object.freeze({ create });
})(globalThis);
